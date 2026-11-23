"""Run the browser acceptance scenarios against a deployed Karaoke coordinator."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import tempfile
import uuid
from contextlib import suppress
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import yaml
from verify_cdp import VerifyError, open_tab, origin, wait_for
from ytdlp_canary import _split_urls

ROOT = Path(__file__).resolve().parents[1]
PROD = "https://karaoke.oklabs.uk"
SCRIPT = (ROOT / "scripts/verify_browser.js").read_text()
TERMINAL = {"completed", "failed", "cancelled"}
EXPORTS = ("karaoke.mp3", "vocals.mp3", "lyrics.txt", "lyrics.lrc")
TOKEN_PATTERN = re.compile(r"((?:/share/|#/job/))[\w-]+")
HEBREW = re.compile(r"[\u05d0-\u05ea]")


def get_bearer():
    if token := os.environ.get("KARAOKE_SERVICE_TOKEN", "").strip():
        return token
    command = [
        "infisical", "secrets", "get", "KARAOKE_SERVICE_TOKEN", "--env=prod",
        "--path=/karaoke", "--silent", "--plain", "--telemetry=false",
    ]
    if project := os.getenv("KARAOKE_INFISICAL_PROJECT_ID"):
        command.extend(["--projectId", project])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        raise VerifyError("Infisical could not read KARAOKE_SERVICE_TOKEN from prod /karaoke") from None
    token = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout).strip()
    if not token or any(char.isspace() for char in token):
        raise VerifyError("Infisical did not return a bearer token")
    return token


def video_id(url):
    parts = urlsplit(url)
    if parts.hostname == "youtu.be":
        return parts.path.strip("/")
    return parse_qs(parts.query).get("v", [url])[0]


def route(job):
    return f"/share/{job['job_token']}"


class Coordinator:
    def __init__(self, http, bearer):
        self.http = http
        self.auth = {"Authorization": f"Bearer {bearer}", "Accept": "application/json"}

    async def request(self, method, path, *, private=True, expected=(200,), **kwargs):
        response = await self.http.request(method, path, headers=self.auth if private else None, **kwargs)
        if response.status_code not in expected:
            safe_path = TOKEN_PATTERN.sub(r"\1[redacted]", path)
            raise VerifyError(f"{method} {safe_path} returned HTTP {response.status_code}")
        return response

    async def jobs(self):
        response = await self.request("GET", "/jobs?limit=200")
        return response.json()

    async def status(self, job):
        response = await self.request("GET", f"/jobs/{job['id']}/status")
        return response.json()

    async def lyrics(self, job):
        return (await self.request("GET", f"{route(job)}/lyrics", private=False)).json()

    async def fixture(self, state, reused):
        if reused:
            jobs = [await self.status({"id": reused})]
        else:
            jobs = [job for job in await self.jobs()
                    if not urlsplit(job["source_url"]).fragment.startswith("karaoke-verify-")
                    and video_id(job["source_url"]) == video_id(state["completed_song"])]
        for job in jobs:
            if job["status"] != "completed" or not job.get("gpu_instance_id"):
                continue
            lyrics = await self.lyrics(job)
            lines = lyrics.get("lines") or []
            if lyrics["synced"] and sum(not line.get("speech") for line in lines) >= 2:
                if state.get("lyrics_script") == "Hebrew" and not any(
                    HEBREW.search(line["text"]) for line in lines
                ):
                    continue
                return job, lyrics
        raise VerifyError("No completed real job with synced lyrics for this song. Use --reuse-job ID.")

    async def cleanup(self, job, grace):
        # Let a running worker finish writing before DELETE. In v0.44.1
        # /cancel marks the row immediately and the worker can still write #337.
        final = await self.status(job)
        deadline = asyncio.get_running_loop().time() + grace
        while final["status"] not in TERMINAL and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(3)
            final = await self.status(job)
        settled = final["status"] in {"completed", "failed"}
        if final["status"] not in TERMINAL:
            await self.request("POST", f"/jobs/{job['id']}/cancel", expected=(200, 409))
            settled = (await self.status(job))["status"] in {"completed", "failed"}
        deletion = await self.request("DELETE", f"/jobs/{job['id']}", expected=(204, 404))
        removed = await self.request("GET", f"/jobs/{job['id']}/status", expected=(404,))
        export_status = {}
        for name in EXPORTS:
            response = await self.request("GET", f"{route(job)}/{name}", private=False, expected=(404,))
            export_status[name] = response.status_code
        return settled, {
            "job_id": job["id"], "delete_http": deletion.status_code,
            "job_http": removed.status_code, "export_http": export_status,
            "worker_terminal_before_delete": settled,
        }


class Evidence:
    def __init__(self, directory, bearer):
        self.directory = directory
        self.bearer = bearer
        self.rows = []
        self.screenshots = []

    def redact(self, value):
        if self.bearer:
            value = value.replace(self.bearer, "[redacted]")
        return TOKEN_PATTERN.sub(r"\1[redacted]", value)

    def check(self, scope, name, expected, observed, passed):
        record = dict(scope=scope, assertion=name, expected=expected, observed=observed, passed=bool(passed))
        self.rows.append(record)
        verdict = "PASS" if passed else "FAIL"
        print(self.redact(f"{verdict} {scope}.{name} expected={expected} observed={observed}"), flush=True)

    def skip(self, scope, checks, reason):
        for name, expected in checks.items():
            self.check(scope, name, expected, reason, False)

    async def capture(self, tab, scope, moment):
        path = self.directory / f"{scope}.{moment}.png"
        await tab.screenshot(path)
        self.screenshots.append(path.name)
        print(f"  screenshot {path}", flush=True)

    def console(self, tab, scope):
        (self.directory / f"{scope}.console.log").write_text(
            self.redact("\n".join(tab.log)) + "\n", encoding="utf-8",
        )


def check_player(evidence, scope, checks, samples):
    before, after = samples["before"], samples["after"]
    delta = round(after["time"] - before["time"], 3) if (
        before["time"] is not None and after["time"] is not None
    ) else None
    gains = samples["mix"]
    times = samples["timing"]
    overflow = {
        "viewport_px": before["pageWidth"], "page_px": before["contentWidth"],
        "lyrics_viewport_px": before["lyricsWidth"], "lyrics_scroll_px": before["lyricsScrollWidth"],
    }
    font = {"min_px": before["minFontPx"],
            "active_px": times[-1]["active_font_px"] if times else after["activeFontPx"]}
    direction = {
        "container": before["lyricsDirection"], "hebrew_lines": before["hebrewLines"],
        "rtl_hebrew_lines": before["rtlLines"], "plain_hebrew": before["plainHebrew"],
    }
    for name, expected in checks.items():
        match name:
            case "playback_advances":
                observed = {"delta_s": delta, "player": after["player"], "error": after["error"]}
                passed = delta is not None and delta >= expected
            case "stem_mix_changes":
                observed = {"control": before["mixPresent"], "gains_before_after": gains}
                passed = bool(gains and len(gains[0]) == len(gains[1]) == 2
                              and gains[0][0] - gains[1][0] >= expected
                              and gains[1][1] - gains[0][1] >= expected)
            case "lyric_highlight_sync":
                observed = times or {"highlight": after["active"], "synced_lines": after["lineCount"]}
                passed = (
                    len(times) == 2 and times[0]["highlight_index"] != times[1]["highlight_index"]
                    and all(
                        sample["distance_s"] is not None and sample["distance_s"] <= expected
                        and abs(sample["time_s"] - sample["sample_at_s"]) <= expected
                        for sample in times
                    )
                )
            case "lyric_highlight_progresses":
                observed = [sample["highlight_index"] for sample in times]
                passed = (len(observed) == 2 and all(value is not None for value in observed)
                          and observed[1] > observed[0])
            case "lyric_font_size":
                observed = font
                passed = font["min_px"] is not None and font["min_px"] >= expected
            case "no_horizontal_scroll":
                observed = overflow
                passed = before["contentWidth"] <= before["pageWidth"] + 1 and (
                    before["lyricsWidth"] is None or before["lyricsScrollWidth"] <= before["lyricsWidth"] + 1
                )
            case "controls_reachable":
                observed = samples["controls"]
                passed = observed["play"] and observed["mix"] and abs(observed["scale"] - 1) < 0.02
            case "hebrew_direction":
                observed = direction
                passed = direction["container"] == expected and (
                    direction["hebrew_lines"] > 0
                    and direction["rtl_hebrew_lines"] == direction["hebrew_lines"]
                    or direction["plain_hebrew"]
                )
            case _:
                raise VerifyError(f"Unknown player assertion {name}")
        evidence.check(scope, name, expected, observed, passed)


def lyric_intervals(lyrics):
    # LRC word timing can start a line before its coarse line tag. Fit the
    # highlight against the server's timing range for that line.
    lines = lyrics.get("lines") or []
    for index, line in enumerate(lines):
        next_start = lines[index + 1]["t"] if index + 1 < len(lines) else float("inf")
        first_word = (line.get("words") or [{"t": line["t"]}])[0]["t"]
        start = min(first_word, line["t"]) if line["t"] - first_word <= 2 else line["t"]
        yield start, next_start, line["text"], line.get("speech", False)


async def timing_samples(tab, lyrics, duration):
    intervals = list(lyric_intervals(lyrics))
    pair = next((
        (index, intervals[index + 1][0])
        for index, ((start, end, _, speech), (_, _, _, next_speech)) in
        enumerate(zip(intervals, intervals[1:], strict=False))
        if not speech and not next_speech and 1.6 <= end - start <= 5
        and end + 0.9 < duration
    ), None)
    if not pair:
        return []
    index, boundary = pair
    selector = f'.synced-lyrics-line:nth-child({index + 1}) button'
    point = await tab.element_point(f"document.querySelector({json.dumps(selector)})")
    if not point:
        return []
    await tab.tap(point)
    samples = []
    for threshold in (intervals[index][0] + 0.65, boundary + 0.62):
        async def reached(threshold=threshold):
            sample = await tab.js("karaokeVerify.snapshot()")
            if sample["time"] is not None and sample["time"] >= threshold:
                return sample
            return None

        try:
            sample = await wait_for(reached, 8, "Track did not reach the lyric sample")
        except VerifyError:
            break
        now = sample["time"]
        active = sample["active"]
        active_index = active["index"] if active else None
        distance = None
        if active is not None and active_index < len(intervals):
            start, end, text, speech = intervals[active_index]
            if not speech and active["text"].split() == text.split():
                distance = round(max(start - now, now - end, 0), 3)
        expected_index = next(
            (i for i, (start, end, _, speech) in reversed(list(enumerate(intervals)))
             if start <= now < end and not speech), None,
        )
        samples.append({"time_s": round(now, 3), "sample_at_s": round(threshold, 3),
                        "highlight_index": active_index,
                        "expected_index": expected_index, "distance_s": distance,
                        "active_font_px": sample["activeFontPx"]})
    return samples


async def inspect_player(tab, evidence, scope, checks, lyrics):
    async def ready():
        value = await tab.js(
            'document.readyState !== "loading" ? window.karaokeVerify?.snapshot() : null'
        )
        return value if value and (value["ready"] or value["error"]) else None

    await wait_for(ready, 35, "Player never became ready")
    await tab.js("document.fonts.ready.then(() => true)")
    before = await tab.js("karaokeVerify.snapshot()")
    await evidence.capture(tab, scope, "before-play")
    point = await tab.js('karaokeVerify.point("play", true)')
    if point and point["reachable"]:
        await tab.tap(point)
    await asyncio.sleep(1.6)
    after = await tab.js("karaokeVerify.snapshot()")
    # Web Audio does not have a media-element clock before its sources start.
    if before["time"] is None and after["player"] == "web_audio":
        before["time"] = 0
    await evidence.capture(tab, scope, "after-play")
    mix = await tab.js('karaokeVerify.point("mix", true, 0.82)')
    gains = []
    if mix and mix["reachable"]:
        gains.append([round(track["gain"], 3) for track in after["tracks"]])
        await tab.tap(mix)
        await asyncio.sleep(0.35)
        mixed = await tab.js("karaokeVerify.snapshot()")
        gains.append([round(track["gain"], 3) for track in mixed["tracks"]])
        await evidence.capture(tab, scope, "after-mix")
    timing = []
    if after["lineCount"] and after["duration"]:
        timing = await timing_samples(tab, lyrics, after["duration"])
        await evidence.capture(tab, scope, "lyrics-playing")
    check_player(evidence, scope, checks, dict(
        before=before, after=after, timing=timing, mix=gains,
        controls={"play": bool(point and point["reachable"]), "mix": bool(mix and mix["reachable"]),
                  "scale": before["scale"], "native_audio_controls": before["controls"]},
    ))


async def run_player(args, evidence, profile, job, lyrics, step):
    surface = step["open"]
    scope = f"{profile}.{surface}"
    async with open_tab(args.cdp, profile, args.target, SCRIPT) as tab:
        try:
            path = route(job) if surface == "public_share" else f"/app/#/job/{job['job_token']}"
            await tab.navigate(f"{args.target}{path}")
            await inspect_player(tab, evidence, scope, step["assert"], lyrics)
        except Exception as exc:
            evidence.skip(scope, step["assert"], str(exc))
            with suppress(Exception):
                await evidence.capture(tab, scope, "error")
        finally:
            evidence.console(tab, scope)


async def submit(args, api, evidence, scenario, profile, bearer):
    submit_step, player_step, cleanup_step = scenario["action"]
    checks = submit_step["assert"]
    state = scenario["state"]
    source = state[submit_step["submit"]]
    if configured := os.getenv("KARAOKE_CANARY_URLS"):
        source = _split_urls(configured)[0]
    source = source.split("#", 1)[0] + f"#karaoke-verify-{uuid.uuid4().hex}"
    scope = f"{profile}.submit"
    job = None
    owner = None
    attempted = False
    checked = set()

    def check(name, actual, passed):
        checked.add(name)
        evidence.check(scope, name, checks[name], actual, passed)

    try:
        async with open_tab(args.cdp, profile, args.target, SCRIPT, bearer=bearer) as tab:
            try:
                await tab.navigate(f"{args.target}/app/")
                await wait_for(lambda: tab.js("location.pathname === '/app/' && !!document.body"),
                               15, "Booth navigation did not finish")
                owner = await tab.js('fetch("/me").then(r => r.json())')
                actual = owner.get("state")
                check("owner_authenticated", {
                    "state": actual, "machine_booth_bootstrap": tab.booth_bootstrap_overridden,
                }, actual == checks["owner_authenticated"])
                if actual != state["authenticated_owner"]:
                    raise VerifyError("The browser did not authenticate with the machine bearer")
                field = await wait_for(
                    lambda: tab.element_point('document.querySelector(\'input[aria-label="Source URL"]\')'),
                    20, "Booth source input unavailable",
                )
                await wait_for(lambda: asyncio.sleep(0, tab.open_sockets), 15,
                               "Booth WebSocket did not open")
                await tab.tap(field)
                await tab.call("Input.insertText", {"text": source})
                button = await tab.element_point(
                    '[...document.querySelectorAll("button")].find(el => el.textContent === "Put it on stage")'
                )
                if not button:
                    raise VerifyError("Submit button missing, disabled or obscured")
                attempted = True
                await tab.tap(button)
                job = await wait_for(lambda: asyncio.sleep(0, tab.response), 30,
                                     "No HTTP 201 submission in the browser")
                if job.get("source_url") != source or job.get("owner_subject") != owner.get("subject"):
                    job = None
                    raise VerifyError("The submission receipt did not match the test URL and owner")
                check("job_submitted", {"id": job["id"], "status": job["status"]},
                      job["status"] == checks["job_submitted"])
                await evidence.capture(tab, scope, "submitted")

                async def terminal_frame():
                    frames = tab.stages.get(job["id"], [])
                    return next((frame for frame in reversed(frames) if frame["status"] in TERMINAL), None)

                frame = await wait_for(terminal_frame, args.job_timeout,
                                       "No terminal progress frame on the Booth WebSocket")
                frames = tab.stages[job["id"]]
                check("websocket_completed", frames,
                      frame["status"] == "completed" and frame["progress"] == checks["websocket_completed"])
                if frame["status"] != "completed":
                    final = await api.status(job)
                    raise VerifyError(f"Pipeline ended {frame['status']}: {final.get('error')}")
                final = await api.status(job)
                kinds = {artifact["kind"] for artifact in final["artifacts"] if artifact["size"]}
                gpu = final.get("gpu_instance_id") or ""
                check("real_gpu_job", {"receipt_prefix": gpu[:7], "cost_micros": final.get("gpu_cost_micros"),
                                      "artifacts": sorted(kinds)},
                      gpu.startswith(checks["real_gpu_job"]) and {"vocals", "karaoke"} <= kinds)
                lyrics = await api.lyrics(job)
                count = sum(not line.get("speech") for line in lyrics.get("lines") or [])
                check("synced_lyrics", {"synced": lyrics["synced"], "sung_lines": count},
                      lyrics["synced"] == checks["synced_lyrics"] and count >= 2)
                href = json.dumps(f"#/job/{job['job_token']}")
                sing_link = await wait_for(
                    lambda: tab.element_point(
                        f'[...document.querySelectorAll("a")].find(el => el.getAttribute("href") === {href})'
                    ), 20, "Completed job has no visible Sing link in the Booth",
                )
                await evidence.capture(tab, scope, "done")
                await tab.tap(sing_link)
                await inspect_player(tab, evidence, f"{profile}.booth_sing_link", player_step["assert"], lyrics)
            except Exception as exc:
                with suppress(Exception):
                    await evidence.capture(tab, scope, "error")
                raise VerifyError(str(exc)) from None
            finally:
                evidence.console(tab, scope)
    except Exception as exc:
        evidence.skip(scope, {key: checks[key] for key in checks if key not in checked}, str(exc))
        player_scope = f"{profile}.booth_sing_link"
        remaining = {key: value for key, value in player_step["assert"].items() if not any(
            row["scope"] == player_scope and row["assertion"] == key for row in evidence.rows
        )}
        evidence.skip(player_scope, remaining, str(exc))
        if not remaining and len(checked) == len(checks):
            evidence.check(scope, "scenario_error", None, str(exc), False)
    finally:
        cleanup_scope = f"{profile}.cleanup"
        expected = cleanup_step["assert"]["job_and_exports_removed"]
        try:
            # Recover a committed POST even if the tab lost the response.
            if job is None and attempted:
                async def recover_job():
                    return next((item for item in await api.jobs()
                                 if item["source_url"] == source
                                 and item["owner_subject"] == owner["subject"]), None)

                job = await wait_for(recover_job, 15, "No job matched the attempted submission")
            if job is None:
                passed, observed = False, "No verifier job was found"
            else:
                passed, observed = await api.cleanup(job, args.cleanup_grace)
        except Exception as exc:
            passed, observed = False, {"job_id": job["id"] if job else None, "error": str(exc)}
        evidence.check(cleanup_scope, "job_and_exports_removed", expected, observed, passed)


def scenarios(selection):
    files = sorted((ROOT / "acceptance").glob("*.yaml"))
    if selection != "all":
        files = [file for file in files if file.stem == selection]
    if not files:
        raise VerifyError(f"Unknown scenario {selection}")
    return {file.stem: yaml.safe_load(file.read_text()) for file in files}


async def run(args, directory):
    selected = scenarios(args.scenario)
    bearer = get_bearer()
    results = {}
    print(f"Evidence: {directory}", flush=True)
    async with httpx.AsyncClient(base_url=args.target, timeout=25, follow_redirects=False) as http:
        api = Coordinator(http, bearer)
        for name, scenario in selected.items():
            scenario_dir = directory / name
            scenario_dir.mkdir()
            evidence = Evidence(scenario_dir, bearer)
            job_id = None
            try:
                state = scenario["state"]
                if "authenticated_owner" in state:
                    for profile in state["profiles"]:
                        await submit(args, api, evidence, scenario, profile, bearer)
                else:
                    job, lyrics = await api.fixture(state, args.reuse_job)
                    job_id = job["id"]
                    print(f"{name}: retained job #{job_id}", flush=True)
                    for profile in state["profiles"]:
                        for step in scenario["action"]:
                            await run_player(args, evidence, profile, job, lyrics, step)
            except Exception as exc:
                evidence.check(name, "scenario_setup", "ready", str(exc), False)
            finally:
                passed = bool(evidence.rows) and all(row["passed"] for row in evidence.rows)
                result = {"passed": passed, "fixture_job_id": job_id, "assertions": evidence.rows,
                          "screenshots": evidence.screenshots}
                (scenario_dir / "result.json").write_text(
                    evidence.redact(json.dumps(result, ensure_ascii=False, indent=2)) + "\n",
                    encoding="utf-8",
                )
                results[name] = passed
                print(f"{'PASS' if passed else 'FAIL'} {name}", flush=True)
    count = sum(results.values())
    (directory / "result.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Scenarios: {count} PASS, {len(results) - count} FAIL. Evidence: {directory}", flush=True)
    return 0 if all(results.values()) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario")
    parser.add_argument("--target", default="prod", metavar="prod|URL")
    parser.add_argument("--reuse-job", type=int, metavar="ID")
    args = parser.parse_args()
    args.target = PROD if args.target == "prod" else args.target.rstrip("/")
    args.cdp = os.getenv("KARAOKE_VERIFY_CDP_URL", "").rstrip("/")
    for label, value in (("--target", args.target), ("KARAOKE_VERIFY_CDP_URL", args.cdp)):
        if origin(value)[0] not in {"http", "https"} or not origin(value)[1]:
            parser.error(f"{label} must be an HTTP(S) origin")
        if urlsplit(value).path or urlsplit(value).query or urlsplit(value).fragment or urlsplit(value).username:
            parser.error(f"{label} must be an HTTP(S) origin")
    if args.reuse_job is not None and args.reuse_job < 1:
        parser.error("--reuse-job must be a positive ID")
    args.job_timeout = float(os.getenv("KARAOKE_VERIFY_JOB_TIMEOUT_SECONDS", "900"))
    args.cleanup_grace = float(os.getenv("KARAOKE_VERIFY_CLEANUP_GRACE_SECONDS", "900"))
    directory = Path(tempfile.mkdtemp(prefix="karaoke-verify-"))
    signal.signal(signal.SIGTERM, lambda *_: signal.raise_signal(signal.SIGINT))
    try:
        return asyncio.run(run(args, directory))
    except KeyboardInterrupt:
        return 130
    except VerifyError as exc:
        print(f"FAIL setup: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
