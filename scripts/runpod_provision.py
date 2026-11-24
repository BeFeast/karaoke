"""Update the image of an existing Karaoke RunPod Serverless endpoint.

RunPod REST v2 copies a template's settings onto the endpoint. Update only the
image so that the endpoint keeps its GPU selection, environment and other
container settings. Recycle workers only when the image actually changes.

    RUNPOD_API_KEY=… RUNPOD_ENDPOINT_ID=… uv run python scripts/runpod_provision.py

Exit codes:
    0 — endpoint already uses the image or was updated; prints RUNPOD_ENDPOINT_ID
    2 — missing credentials or endpoint ID
    3 — endpoint configuration, API or network error
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API_BASE = "https://api.runpod.io/v2"
HEALTH_BASE = "https://api.runpod.ai/v2"
IMAGE = "ghcr.io/befeast/karaoke-runpod:cuda12.4-r16"
USER_AGENT = "karaoke-provision/0.2"
REQUEST_TIMEOUT_S = 30

_FLUSH_DRAIN_TIMEOUT_S = 120.0
_FLUSH_POLL_INTERVAL_S = 8.0

# Keep incompatible Blackwell MIG cards out of the CUDA 12.4 worker image when
# the operator's endpoint enables either of these pools.
REQUIRED_GPU_EXCLUSIONS = {
    "ADA_24": "NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 1g.24gb",
    "ADA_48_PRO": "NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_HTTP = urllib.request.build_opener(_NoRedirect())


def _fetch(
    base: str,
    method: str,
    path: str,
    token: str,
    body: dict[str, Any] | None = None,
) -> Any:
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    with _HTTP.open(req, timeout=REQUEST_TIMEOUT_S) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def _request(method: str, path: str, token: str, body: dict[str, Any] | None = None) -> Any:
    try:
        return _fetch(API_BASE, method, path, token, body)
    except urllib.error.HTTPError as exc:
        # Provider error bodies can include the endpoint environment.
        print(f"RunPod management API {method} failed: HTTP {exc.code}", file=sys.stderr)
    except (urllib.error.URLError, OSError, ValueError):
        print(f"RunPod management API {method} failed: network or response error", file=sys.stderr)
    raise SystemExit(3)


def _endpoint_path(endpoint_id: str) -> str:
    return "/serverless/" + urllib.parse.quote(endpoint_id, safe="")


def _endpoint_worker_count(token: str, endpoint_id: str) -> int | None:
    """Read the aggregate worker counts from the Serverless jobs API."""
    path = "/" + urllib.parse.quote(endpoint_id, safe="") + "/health"
    try:
        payload = _fetch(HEALTH_BASE, "GET", path, token)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    workers = payload.get("workers") if isinstance(payload, dict) else None
    if not isinstance(workers, dict):
        return None
    states = ("idle", "initializing", "ready", "running", "throttled")
    if not any(state in workers for state in states):
        return None
    counts = [workers.get(state, 0) for state in states]
    if any(type(value) is not int or value < 0 for value in counts):
        return None
    return sum(counts)


def _flush_workers(token: str, endpoint_id: str, bounds: dict[str, int]) -> None:
    """Drain stale workers and restore the endpoint's own min/max in all cases."""
    path = _endpoint_path(endpoint_id)
    try:
        # Lower min in the same PATCH to keep min <= max on warm endpoints.
        _request("PATCH", path, token, {"workers": {"min": 0, "max": 0}})
        deadline = time.monotonic() + _FLUSH_DRAIN_TIMEOUT_S
        while time.monotonic() < deadline:
            if _endpoint_worker_count(token, endpoint_id) == 0:
                print("# workers drained to 0", file=sys.stderr)
                break
            time.sleep(_FLUSH_POLL_INTERVAL_S)
        else:
            print("# WARN: drain did not confirm zero workers; restoring worker bounds", file=sys.stderr)
    finally:
        # Restore even when the pause request fails ambiguously or is interrupted.
        try:
            _request("PATCH", path, token, {"workers": bounds})
        except BaseException:
            payload = json.dumps({"workers": bounds})
            print(
                "ERROR: failed to restore worker bounds; the endpoint MAY BE LEFT PAUSED.\n"
                f'  curl -X PATCH "{API_BASE}/serverless/$RUNPOD_ENDPOINT_ID" '
                '-H "Authorization: Bearer $RUNPOD_API_KEY" '
                '-H "Content-Type: application/json" '
                f"-d '{payload}'",
                file=sys.stderr,
            )
            raise


def _check_gpu_selection(endpoint: dict[str, Any]) -> None:
    gpu = endpoint.get("gpu")
    pools = gpu.get("pools") if isinstance(gpu, dict) else None
    excluded = gpu.get("excludedTypes", []) if isinstance(gpu, dict) else None
    if not isinstance(pools, list) or not pools or not isinstance(excluded, list):
        print("RunPod endpoint must already have an approved GPU selection", file=sys.stderr)
        raise SystemExit(3)
    missing = {
        model for pool, model in REQUIRED_GPU_EXCLUSIONS.items()
        if pool in pools and model not in excluded
    }
    if missing:
        print(
            "RunPod endpoint is missing required Blackwell MIG exclusions. "
            "Configure the exclusions in RunPod and retry.",
            file=sys.stderr,
        )
        raise SystemExit(3)


def main() -> int:
    token = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not token:
        print("RUNPOD_API_KEY env var is required", file=sys.stderr)
        return 2
    endpoint_id = os.environ.get("RUNPOD_ENDPOINT_ID", "").strip()
    if not endpoint_id:
        print("RUNPOD_ENDPOINT_ID is required. Create and configure the endpoint in RunPod first.", file=sys.stderr)
        return 2

    path = _endpoint_path(endpoint_id)
    endpoint = _request("GET", path, token)
    if not isinstance(endpoint, dict) or not isinstance(endpoint.get("image"), str):
        print("RunPod endpoint response is missing its current image", file=sys.stderr)
        return 3
    _check_gpu_selection(endpoint)
    if endpoint["image"] != IMAGE:
        workers = endpoint.get("workers")
        bounds = {key: workers.get(key) for key in ("min", "max")} if isinstance(workers, dict) else {}
        if (set(bounds) != {"min", "max"} or any(type(v) is not int for v in bounds.values())
                or not 0 <= bounds["min"] <= bounds["max"]):
            print("RunPod endpoint response is missing valid worker bounds", file=sys.stderr)
            return 3
        _request("PATCH", path, token, {"image": IMAGE})
        _flush_workers(token, endpoint_id, bounds)
    else:
        print("# endpoint already on this image", file=sys.stderr)
    print(f"RUNPOD_ENDPOINT_ID={endpoint_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
