"""Idempotent provisioner for the karaoke RunPod Serverless endpoint.

Reads ``RUNPOD_API_KEY`` from env (refuses if missing). On run:

1. Find or create a Template named ``karaoke-poc-tmpl``
   (RunPod Serverless requires endpoints to reference a Template via
   ``templateId``; the older ``imageName``-at-endpoint-level flow was
   removed from the REST API, see https://rest.runpod.io/v1/openapi.json).
2. Find or create an Endpoint named ``karaoke-poc`` that points at that
   template id, with the locked guardrails (workersMax=1, executionTimeoutMs=600000,
   FlashBoot=true) from issue #33.
3. When step 1 changed the template image (or created the template), flush
   the endpoint's workers (bounce ``workersMax`` to 0 and back) so the next
   job runs on the NEW image instead of a stale cached/standby worker.

Locked params live in ``TEMPLATE_SPEC`` and ``ENDPOINT_SPEC`` below —
DO NOT change without telling the lead (per issue #33).

Exit codes:
    0 — endpoint created or updated; prints ``RUNPOD_ENDPOINT_ID=<id>``.
    2 — RUNPOD_API_KEY missing
    3 — RunPod API returned a 4xx/5xx or network error
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

API_BASE = "https://rest.runpod.io/v1"
# The v2 "jobs" plane (distinct from the REST v1 CRUD plane) — used only to
# read endpoint worker health while flushing stale workers after an image bump.
HEALTH_BASE = "https://api.runpod.ai/v2"
USER_AGENT = "karaoke-provision/0.1"

# Stale-worker flush (after a template image change). RunPod does NOT recycle
# existing/standby workers when the image changes, so we bounce workersMax to 0
# (terminating all workers, incl. the always-on standby) and wait for the pool
# to drain before restoring it — the next job then cold-pulls the new image.
_FLUSH_DRAIN_TIMEOUT_S = 120.0
_FLUSH_POLL_INTERVAL_S = 8.0

ENDPOINT_NAME = "karaoke-poc"
TEMPLATE_NAME = "karaoke-poc-tmpl"

# Locked per issue #33. Coordinate with the lead before changing.
TEMPLATE_SPEC: dict[str, Any] = {
    "name": TEMPLATE_NAME,
    "imageName": "ghcr.io/befeast/karaoke-runpod:cuda12.4-r10",
    "isServerless": True,
    "containerDiskInGb": 30,
    # The handler doesn't write a workspace; tiny volume is fine, but RunPod
    # requires the field. Default 20 GB is the cheapest viable.
    "volumeInGb": 0,
    "env": {},
    "ports": [],
}

# Endpoint guardrails (issue #33). templateId added at runtime.
ENDPOINT_SPEC: dict[str, Any] = {
    "name": ENDPOINT_NAME,
    # Wide pool to dodge supply-low throttling — verified 2026-05-30 against
    # the live `endpoints.gpuTypeIds` enum. workersMax=3 lets a few requests
    # run in parallel; per-job + daily cost caps still apply on the client.
    "gpuTypeIds": [
        "NVIDIA RTX A4000",
        "NVIDIA RTX A4500",
        "NVIDIA RTX 4000 Ada Generation",
        "NVIDIA RTX A5000",
        "NVIDIA L4",
        "NVIDIA GeForce RTX 3090",
        "NVIDIA L40",
        "NVIDIA L40S",
        "NVIDIA GeForce RTX 4090",
    ],
    "workersMin": 0,
    "workersMax": 3,
    "idleTimeout": 5,
    # Hard kill from RunPod side: handler runs in ~90-150s on warm worker;
    # 5 min is generous headroom. Coordinator wall_ceiling (600s) covers
    # the queue-wait window separately.
    "executionTimeoutMs": 300000,
    "flashboot": True,
}


def _request(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> Any:
    url = f"{API_BASE}{path}"
    data = None
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print(
            f"RunPod API {method} {path} failed: HTTP {exc.code}\n{err_body}",
            file=sys.stderr,
        )
        sys.exit(3)
    except urllib.error.URLError as exc:
        print(f"RunPod API {method} {path} network error: {exc}", file=sys.stderr)
        sys.exit(3)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.decode("utf-8", errors="replace")


def _items(payload: Any, key: str) -> list[dict[str, Any]]:
    """RunPod REST returns either a bare list or ``{"<key>": [...]}`` depending
    on version. Normalise to a list of dicts."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        v = payload.get(key)
        if isinstance(v, list):
            return [x for x in v if isinstance(x, dict)]
    return []


def _find_by_name(token: str, path: str, key: str, name: str) -> dict[str, Any] | None:
    for item in _items(_request("GET", path, token), key):
        if str(item.get("name") or "") == name:
            return item
    return None


def _ensure_template(token: str) -> tuple[str, bool]:
    """Return ``(template_id, image_changed)``. ``image_changed`` is True when
    the existing template's image differs from the spec (a repoint) OR the
    template was just created — both mean the endpoint must roll onto a new
    image, so the caller flushes stale workers."""
    want_image = TEMPLATE_SPEC["imageName"]
    existing = _find_by_name(token, "/templates", "templates", TEMPLATE_NAME)
    if existing and existing.get("id"):
        old_image = str(existing.get("imageName") or "")
        image_changed = old_image != want_image
        # PATCH to keep the spec in sync (idempotent). RunPod rejects
        # `isServerless` on PATCH (immutable after create).
        immutable = {"name", "isServerless"}
        body = {k: v for k, v in TEMPLATE_SPEC.items() if k not in immutable}
        _request("PATCH", f"/templates/{existing['id']}", token, body)
        if image_changed:
            print(
                f"# template image changed: {old_image!r} -> {want_image!r}",
                file=sys.stderr,
            )
        print(f"# template OK (existing): id={existing['id']}", file=sys.stderr)
        return str(existing["id"]), image_changed
    created = _request("POST", "/templates", token, TEMPLATE_SPEC)
    if not isinstance(created, dict) or not created.get("id"):
        print(
            f"RunPod template POST returned no id: {created!r}", file=sys.stderr
        )
        sys.exit(3)
    print(f"# template CREATED: id={created['id']}", file=sys.stderr)
    # A brand-new template is, by definition, a new image for the endpoint.
    return str(created["id"]), True


def _ensure_endpoint(token: str, template_id: str) -> str:
    body = dict(ENDPOINT_SPEC)
    body["templateId"] = template_id

    existing = _find_by_name(token, "/endpoints", "endpoints", ENDPOINT_NAME)
    if existing and existing.get("id"):
        update = {k: v for k, v in body.items() if k != "name"}
        _request("PATCH", f"/endpoints/{existing['id']}", token, update)
        print(f"# endpoint OK (existing): id={existing['id']}", file=sys.stderr)
        return str(existing["id"])
    created = _request("POST", "/endpoints", token, body)
    if not isinstance(created, dict) or not created.get("id"):
        print(
            f"RunPod endpoint POST returned no id: {created!r}", file=sys.stderr
        )
        sys.exit(3)
    print(f"# endpoint CREATED: id={created['id']}", file=sys.stderr)
    return str(created["id"])


def _endpoint_worker_count(token: str, endpoint_id: str) -> int | None:
    """Total live workers from the v2 health plane (idle + initializing + ready
    + running + throttled). Returns ``None`` on any error so the drain loop
    treats it as 'not yet drained' rather than crashing the flush."""
    url = f"{HEALTH_BASE}/{endpoint_id}/health"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None
    workers = payload.get("workers") if isinstance(payload, dict) else None
    if not isinstance(workers, dict):
        return None
    return sum(
        int(workers.get(key, 0) or 0)
        for key in ("idle", "initializing", "ready", "running", "throttled")
    )


def _flush_workers(token: str, endpoint_id: str) -> None:
    """Recycle endpoint workers onto the new template image.

    RunPod does NOT recycle existing/standby workers when a template's image
    changes — they keep serving the OLD image until they happen to cycle, so a
    repoint silently keeps running the old handler. ``workersStandby`` is
    read-only via REST v1, so the only lever is a ``workersMax`` bounce: set it
    to 0 (terminates every worker, incl. the always-on standby), wait for the
    pool to drain, then restore the locked maximum. The next job cold-pulls the
    new image.
    """
    target_max = ENDPOINT_SPEC["workersMax"]
    print(
        f"# image changed: flushing workers "
        f"(workersMax {target_max} -> 0 -> drain -> restore)",
        file=sys.stderr,
    )
    _request("PATCH", f"/endpoints/{endpoint_id}", token, {"workersMax": 0})
    deadline = time.monotonic() + _FLUSH_DRAIN_TIMEOUT_S
    drained = False
    while time.monotonic() < deadline:
        if _endpoint_worker_count(token, endpoint_id) == 0:
            drained = True
            break
        time.sleep(_FLUSH_POLL_INTERVAL_S)
    if drained:
        print("# workers drained to 0", file=sys.stderr)
    else:
        print(
            f"# WARN: workers did not confirm drain within "
            f"{_FLUSH_DRAIN_TIMEOUT_S:.0f}s; restoring workersMax anyway",
            file=sys.stderr,
        )
    _request(
        "PATCH", f"/endpoints/{endpoint_id}", token, {"workersMax": target_max}
    )
    print(f"# restored workersMax={target_max}", file=sys.stderr)


def main() -> int:
    token = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not token:
        print("RUNPOD_API_KEY env var is required", file=sys.stderr)
        return 2

    template_id, image_changed = _ensure_template(token)
    endpoint_id = _ensure_endpoint(token, template_id)

    # Only flush when the image actually changed — an idempotent re-run must NOT
    # needlessly terminate workers (each flush forces a slow cold pull).
    if image_changed:
        _flush_workers(token, endpoint_id)

    # The operator pipes this into Infisical themselves.
    print(f"RUNPOD_ENDPOINT_ID={endpoint_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
