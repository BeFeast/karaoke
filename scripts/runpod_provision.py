"""Idempotent provisioner for the karaoke RunPod Serverless endpoint.

Reads ``RUNPOD_API_KEY`` from env (refuses if missing). On run:

1. ``GET /v1/endpoints`` — find an endpoint named ``karaoke-poc``.
2. If found: ``PATCH /v1/endpoints/{id}`` with the locked spec (minus name).
3. If not found: ``POST /v1/endpoints`` with the locked spec.

Locked params live in ``ENDPOINT_SPEC`` below — DO NOT change without
telling the lead (per issue #33).

Exit codes:
    0 — endpoint created or updated
    2 — RUNPOD_API_KEY missing
    3 — RunPod API returned a 4xx/5xx
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

API_BASE = "https://rest.runpod.io/v1"
USER_AGENT = "karaoke-provision/0.1"

ENDPOINT_NAME = "karaoke-poc"

# Locked per issue #33. Coordinate with the lead before changing.
ENDPOINT_SPEC: dict[str, Any] = {
    "name": ENDPOINT_NAME,
    "imageName": "ghcr.io/befeast/karaoke-runpod:cuda12.4",
    "gpuTypeIds": ["NVIDIA RTX A4000", "NVIDIA L4"],
    "workersMin": 0,
    "workersMax": 1,
    "idleTimeout": 5,
    "executionTimeoutMs": 600000,
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


def _find_endpoint_id(token: str) -> str | None:
    payload = _request("GET", "/endpoints", token)
    # RunPod returns either a bare list or {"endpoints": [...]} depending
    # on version. Handle both.
    items: list[dict[str, Any]]
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict) and isinstance(payload.get("endpoints"), list):
        items = payload["endpoints"]
    else:
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("name") == ENDPOINT_NAME:
            ep_id = item.get("id")
            if isinstance(ep_id, str) and ep_id:
                return ep_id
    return None


def main() -> int:
    token = os.environ.get("RUNPOD_API_KEY")
    if not token:
        print("RUNPOD_API_KEY is not set in the environment.", file=sys.stderr)
        return 2

    existing_id = _find_endpoint_id(token)

    if existing_id is not None:
        update_body = {k: v for k, v in ENDPOINT_SPEC.items() if k != "name"}
        result = _request("PATCH", f"/endpoints/{existing_id}", token, update_body)
        ep_id = existing_id
        if isinstance(result, dict):
            ep_id = result.get("id") or existing_id
        print(f"updated endpoint id={ep_id}")
    else:
        result = _request("POST", "/endpoints", token, ENDPOINT_SPEC)
        if not isinstance(result, dict):
            print(
                f"unexpected RunPod response when creating endpoint: {result!r}",
                file=sys.stderr,
            )
            return 3
        ep_id = result.get("id")
        if not isinstance(ep_id, str) or not ep_id:
            print(
                f"RunPod response missing endpoint id: {result!r}", file=sys.stderr
            )
            return 3
        print(f"created endpoint id={ep_id}")

    print(f"RUNPOD_ENDPOINT_ID={ep_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
