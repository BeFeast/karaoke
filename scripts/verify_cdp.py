"""A small CDP client for an isolated tab in a shared Chrome."""
from __future__ import annotations

import asyncio
import base64
import itertools
import json
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect


class VerifyError(RuntimeError):
    pass


async def wait_for(probe, seconds, label):
    try:
        async with asyncio.timeout(seconds):
            while True:
                if value := await probe():
                    return value
                await asyncio.sleep(0.1)
    except TimeoutError:
        raise VerifyError(f"{label} after {seconds:g}s") from None


def origin(url):
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname, parsed.port


class Tab:
    def __init__(self, socket, session, mobile, target, bearer):
        self.socket = socket
        self.session = session
        self.mobile = mobile
        self.target = target
        self.bearer = bearer
        self.ids = itertools.count(1)
        self.pending = {}
        self.log = []
        self.sockets = set()
        self.open_sockets = set()
        self.booth_bootstrap_overridden = False
        self.stages = {}
        self.submissions = set()
        self.response = None
        self.background = set()
        self.reader = asyncio.create_task(self.read())

    async def close(self):
        self.reader.cancel()
        for task in self.background:
            task.cancel()
        await asyncio.gather(self.reader, *self.background, return_exceptions=True)

    async def call(self, method, params=None, *, browser=False):
        request_id = next(self.ids)
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        message = {"id": request_id, "method": method, "params": params or {}}
        if not browser:
            message["sessionId"] = self.session
        try:
            async with asyncio.timeout(15):
                await self.socket.send(json.dumps(message))
                reply = await future
        except TimeoutError:
            raise VerifyError(f"CDP timed out: {method}") from None
        finally:
            self.pending.pop(request_id, None)
        if "error" in reply:
            raise VerifyError(f"CDP {method}: {reply['error']['message']}")
        return reply.get("result", {})

    def spawn(self, coro):
        async def run():
            try:
                await coro
            except Exception as exc:
                self.log.append(f"CDP event handler: {exc}")

        task = asyncio.create_task(run())
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def read(self):
        try:
            async for raw in self.socket:
                message = json.loads(raw)
                if future := self.pending.get(message.get("id")):
                    if not future.done():
                        future.set_result(message)
                elif message.get("sessionId") == self.session and "method" in message:
                    self.event(message["method"], message.get("params", {}))
        finally:
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(VerifyError("CDP connection closed"))

    def event(self, method, p):
        if method == "Runtime.consoleAPICalled":
            values = [str(arg.get("value", arg.get("description", ""))) for arg in p["args"]]
            self.log.append(f"{p['type']}: {' '.join(values)}")
        elif method == "Runtime.exceptionThrown":
            detail = p["exceptionDetails"]
            self.log.append(detail.get("exception", {}).get("description", detail["text"]))
        elif method == "Log.entryAdded":
            self.log.append(f"{p['entry']['level']}: {p['entry']['text']}")
        elif method == "Page.javascriptDialogOpening":
            self.spawn(self.call("Page.handleJavaScriptDialog", {"accept": False}))
        elif method == "Fetch.requestPaused":
            self.spawn(self.authorize(p))
        elif method == "Network.webSocketCreated":
            url = urlsplit(p["url"])
            if (url.hostname, url.port) == origin(self.target)[1:] and url.path == "/ws":
                self.sockets.add(p["requestId"])
        elif method == "Network.webSocketHandshakeResponseReceived" and p["requestId"] in self.sockets:
            if p["response"]["status"] == 101:
                self.open_sockets.add(p["requestId"])
        elif method == "Network.webSocketClosed":
            self.open_sockets.discard(p["requestId"])
            self.sockets.discard(p["requestId"])
        elif method == "Network.webSocketFrameReceived" and p["requestId"] in self.sockets:
            try:
                frame = json.loads(p["response"]["payloadData"])
            except (KeyError, ValueError):
                return
            if isinstance(frame, dict) and frame.get("type") == "stage_change":
                stages = self.stages.setdefault(frame["job_id"], [])
                stages.append({key: frame.get(key) for key in ("status", "progress")})
        elif method == "Network.responseReceived" and self.bearer:
            response = p["response"]
            if urlsplit(response["url"]).path == "/jobs" and response["status"] == 201:
                self.submissions.add(p["requestId"])
        elif method == "Network.loadingFinished" and p["requestId"] in self.submissions:
            self.spawn(self.capture_submission(p["requestId"]))

    async def authorize(self, event):
        request = event["request"]
        params = {"requestId": event["requestId"]}
        if "responseStatusCode" in event:
            await self.bootstrap_booth(event, params)
            return
        path = urlsplit(request["url"]).path
        if (origin(request["url"]) == origin(self.target) and self.bearer
            and (path in {"/jobs", "/me"} or path.startswith("/jobs/"))):
            headers = {k: v for k, v in request["headers"].items() if k.lower() != "authorization"}
            headers["Authorization"] = f"Bearer {self.bearer}"
            params["headers"] = [{"name": key, "value": value} for key, value in headers.items()]
        await self.call("Fetch.continueRequest", params)

    async def bootstrap_booth(self, event, params):
        # The SPA's sign-in wall only recognizes Clerk and trusted LAN.
        # Boot its LAN shell in this isolated tab for the machine owner.
        # Authenticated API calls still go to the real coordinator.
        if event["responseStatusCode"] == 200 and origin(event["request"]["url"]) == origin(self.target):
            body = await self.call("Fetch.getResponseBody", params)
            config = json.loads(base64.b64decode(body["body"]) if body["base64Encoded"] else body["body"])
            if config.get("clerk_enabled") and not config.get("trusted_client"):
                config["trusted_client"] = True
                await self.call("Fetch.fulfillRequest", {
                    **params, "responseCode": 200,
                    "responseHeaders": [{"name": "Content-Type", "value": "application/json"}],
                    "body": base64.b64encode(json.dumps(config).encode()).decode(),
                })
                self.booth_bootstrap_overridden = True
                return
        await self.call("Fetch.continueResponse", params)

    async def capture_submission(self, request_id):
        data = await self.call("Network.getResponseBody", {"requestId": request_id})
        body = base64.b64decode(data["body"]) if data["base64Encoded"] else data["body"]
        self.response = json.loads(body)

    async def js(self, expression):
        result = await self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        if detail := result.get("exceptionDetails"):
            raise VerifyError(detail.get("exception", {}).get("description", detail["text"]))
        return result["result"].get("value")

    async def navigate(self, url):
        result = await self.call("Page.navigate", {"url": url})
        if result.get("errorText"):
            raise VerifyError(f"Navigation failed: {result['errorText']}")

    async def tap(self, point):
        x, y = point["x"], point["y"]
        if self.mobile:
            await self.call("Input.dispatchTouchEvent", {
                "type": "touchStart", "touchPoints": [{"x": x, "y": y}],
            })
            await self.call("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        else:
            for event in ("mousePressed", "mouseReleased"):
                await self.call("Input.dispatchMouseEvent", {
                    "type": event, "x": x, "y": y, "button": "left", "clickCount": 1,
                })

    async def element_point(self, expression):
        return await self.js(f"""(() => {{
          const el = {expression};
          if (!el || el.disabled) return null;
          el.scrollIntoView({{block: "center", behavior: "instant"}});
          const r = el.getBoundingClientRect();
          const x = r.left + r.width / 2, y = r.top + r.height / 2;
          return el.contains(document.elementFromPoint(x, y)) ? {{x, y}} : null;
        }})()""")

    async def screenshot(self, path):
        result = await self.call("Page.captureScreenshot", {"format": "png", "fromSurface": True})
        path.write_bytes(base64.b64decode(result["data"]))


PROFILES = {
    "iphone": (390, 844, "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
               "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"),
    "android": (390, 844, "Mozilla/5.0 (Linux; Android 15; Pixel 8) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Mobile Safari/537.36"),
    "desktop": (1440, 1000, None),
}


@asynccontextmanager
async def open_tab(endpoint, profile, target, script, bearer=None):
    async with httpx.AsyncClient(timeout=10) as http:
        metadata = await http.get(f"{endpoint}/json/version")
        metadata.raise_for_status()
        ws_path = urlsplit(metadata.json()["webSocketDebuggerUrl"]).path
    ws_origin = endpoint.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
    async with connect(f"{ws_origin}{ws_path}", open_timeout=10, max_size=24 * 1024 * 1024) as socket:
        width, height, ua = PROFILES[profile]
        tab = Tab(socket, None, ua is not None, target, bearer)
        context = None
        try:
            context = (await tab.call("Target.createBrowserContext", {
                "disposeOnDetach": True,
            }, browser=True))["browserContextId"]
            target_id = (await tab.call("Target.createTarget", {
                "url": "about:blank", "browserContextId": context,
            }, browser=True))["targetId"]
            tab.session = (await tab.call("Target.attachToTarget", {
                "targetId": target_id, "flatten": True,
            }, browser=True))["sessionId"]
            for domain in ("Page", "Runtime", "Log", "Network"):
                await tab.call(f"{domain}.enable")
            await tab.call("Emulation.setDeviceMetricsOverride", {
                "width": width, "height": height, "deviceScaleFactor": 1, "mobile": tab.mobile,
            })
            await tab.call("Emulation.setTouchEmulationEnabled", {"enabled": tab.mobile})
            if ua:
                await tab.call("Emulation.setUserAgentOverride", {"userAgent": ua})
            await tab.call("Page.addScriptToEvaluateOnNewDocument", {"source": script})
            if bearer:
                await tab.call("Fetch.enable", {"patterns": [
                    {"urlPattern": f"{target}/jobs*"},
                    {"urlPattern": f"{target}/me"},
                    {"urlPattern": f"{target}/config", "requestStage": "Response"},
                ]})
            yield tab
        finally:
            try:
                if context:
                    await tab.call("Target.disposeBrowserContext", {"browserContextId": context}, browser=True)
            finally:
                await tab.close()
