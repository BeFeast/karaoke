#!/usr/bin/env python3
"""Submit deployed coordinator jobs until the yt-dlp download stage succeeds.

With KARAOKE_CANARY_MODE=full the job is instead driven to ``completed``: the
GPU stages run for real, which both verifies the whole pipeline and keeps the
RunPod endpoint from being auto-scaled to zero workers after a week without
requests (it was silently paused 2026-09-08..24).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_URL = "https://www.youtube.com/watch?v=BaW_jenozKc"
DOWNLOAD_SUCCESS_STATUSES = {"separating", "transcribing", "completed"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
MODES = ("download", "full")


class CanaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class CanaryConfig:
    base_url: str
    service_token: str
    urls: tuple[str, ...]
    timeout_s: float
    poll_s: float
    mode: str = "download"


class CanaryClient:
    def __init__(self, base_url: str, service_token: str):
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token

    def request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None
        headers = {
            "Authorization": f"Bearer {self.service_token}",
            "Accept": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise CanaryError(f"{method} {path} returned HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise CanaryError(f"{method} {path} failed: {exc.reason}") from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CanaryError(f"{method} {path} returned non-JSON") from exc
        if not isinstance(parsed, dict):
            raise CanaryError(f"{method} {path} returned unexpected JSON")
        return parsed


def _split_urls(raw: str | None) -> tuple[str, ...]:
    if not raw or not raw.strip():
        return (DEFAULT_URL,)
    urls = []
    for chunk in raw.replace(",", "\n").splitlines():
        value = chunk.strip()
        if value:
            urls.append(value)
    return tuple(urls) or (DEFAULT_URL,)


def config_from_env(env: dict[str, str] = os.environ) -> CanaryConfig:
    base_url = env.get("KARAOKE_CANARY_BASE_URL", "").strip()
    token = env.get("KARAOKE_CANARY_SERVICE_TOKEN", "").strip()
    if not base_url:
        raise CanaryError("KARAOKE_CANARY_BASE_URL is required")
    if not token:
        raise CanaryError("KARAOKE_CANARY_SERVICE_TOKEN is required")
    mode = env.get("KARAOKE_CANARY_MODE", "download").strip() or "download"
    if mode not in MODES:
        raise CanaryError(f"KARAOKE_CANARY_MODE must be one of {MODES}, got {mode!r}")
    return CanaryConfig(
        base_url=base_url,
        service_token=token,
        urls=_split_urls(env.get("KARAOKE_CANARY_URLS")),
        timeout_s=float(env.get("KARAOKE_CANARY_TIMEOUT_SECONDS", "900")),
        poll_s=float(env.get("KARAOKE_CANARY_POLL_SECONDS", "10")),
        mode=mode,
    )


def _status(body: dict[str, Any]) -> str:
    value = body.get("status")
    if not isinstance(value, str) or not value:
        raise CanaryError("job response did not include a status")
    return value


def run_one(
    client: CanaryClient,
    source_url: str,
    *,
    timeout_s: float,
    poll_s: float,
    mode: str = "download",
    sleep=time.sleep,
    now=time.monotonic,
) -> dict[str, Any]:
    full = mode == "full"
    success = {"completed"} if full else DOWNLOAD_SUCCESS_STATUSES
    stage = "completion" if full else "download success"
    job = client.request_json(
        "POST",
        "/jobs",
        {"url": source_url, "title": "GPU keepalive canary" if full else "yt-dlp nightly canary"},
    )
    job_id = job.get("id")
    if not isinstance(job_id, int):
        raise CanaryError("create job response did not include an integer id")

    deadline = now() + timeout_s
    last = job
    try:
        while now() < deadline:
            status = _status(last)
            progress = last.get("progress", "?")
            print(f"job {job_id}: {status} ({progress}%)", flush=True)
            if status in success:
                return last
            if status in TERMINAL_STATUSES:
                raise CanaryError(
                    f"job {job_id} reached {status} before {stage}: "
                    f"{last.get('error') or 'no error detail'}"
                )
            sleep(poll_s)
            last = client.request_json("GET", f"/jobs/{job_id}/status")
    finally:
        final_status = str(last.get("status") or "")
        if final_status not in TERMINAL_STATUSES:
            try:
                client.request_json("POST", f"/jobs/{job_id}/cancel")
                print(f"job {job_id}: cancelled after canary {mode} check", flush=True)
            except CanaryError as exc:
                print(f"job {job_id}: cancel failed after canary check: {exc}", file=sys.stderr)
    raise CanaryError(f"job {job_id} did not reach {stage} within {timeout_s:.0f}s")


def mode_label(env: dict[str, str]) -> str:
    return env.get("KARAOKE_CANARY_MODE", "download").strip() or "download"


def main() -> int:
    try:
        cfg = config_from_env()
        client = CanaryClient(cfg.base_url, cfg.service_token)
        for url in cfg.urls:
            run_one(client, url, timeout_s=cfg.timeout_s, poll_s=cfg.poll_s, mode=cfg.mode)
    except CanaryError as exc:
        print(f"canary ({mode_label(os.environ)}) failed: {exc}", file=sys.stderr)
        return 1
    print(f"canary ({cfg.mode}) passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
