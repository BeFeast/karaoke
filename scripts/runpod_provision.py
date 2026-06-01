"""Idempotent provisioner for the karaoke RunPod Serverless endpoint.

Reads ``RUNPOD_API_KEY`` from env (refuses if missing). On run:

1. Find or create a Template named ``karaoke-poc-tmpl``
   (RunPod Serverless requires endpoints to reference a Template via
   ``templateId``; the older ``imageName``-at-endpoint-level flow was
   removed from the REST API, see https://rest.runpod.io/v1/openapi.json).
2. Find or create an Endpoint named ``karaoke-poc`` that points at that
   template id, with the locked guardrails (workersMax=1, executionTimeoutMs=600000,
   FlashBoot=true) from issue #33.

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
import urllib.error
import urllib.request
from typing import Any

API_BASE = "https://rest.runpod.io/v1"
USER_AGENT = "karaoke-provision/0.1"

ENDPOINT_NAME = "karaoke-poc"
TEMPLATE_NAME = "karaoke-poc-tmpl"

# Locked per issue #33. Coordinate with the lead before changing.
TEMPLATE_SPEC: dict[str, Any] = {
    "name": TEMPLATE_NAME,
    "imageName": "ghcr.io/befeast/karaoke-runpod:cuda12.4-r4",
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


def _ensure_template(token: str) -> str:
    existing = _find_by_name(token, "/templates", "templates", TEMPLATE_NAME)
    if existing and existing.get("id"):
        # PATCH to keep the spec in sync (idempotent). RunPod rejects
        # `isServerless` on PATCH (immutable after create).
        immutable = {"name", "isServerless"}
        body = {k: v for k, v in TEMPLATE_SPEC.items() if k not in immutable}
        _request("PATCH", f"/templates/{existing['id']}", token, body)
        print(f"# template OK (existing): id={existing['id']}", file=sys.stderr)
        return str(existing["id"])
    created = _request("POST", "/templates", token, TEMPLATE_SPEC)
    if not isinstance(created, dict) or not created.get("id"):
        print(
            f"RunPod template POST returned no id: {created!r}", file=sys.stderr
        )
        sys.exit(3)
    print(f"# template CREATED: id={created['id']}", file=sys.stderr)
    return str(created["id"])


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


def main() -> int:
    token = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not token:
        print("RUNPOD_API_KEY env var is required", file=sys.stderr)
        return 2

    template_id = _ensure_template(token)
    endpoint_id = _ensure_endpoint(token, template_id)

    # The operator pipes this into Infisical themselves.
    print(f"RUNPOD_ENDPOINT_ID={endpoint_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
