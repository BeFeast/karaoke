from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ytdlp_canary", ROOT / "scripts/ytdlp_canary.py"
)
assert SPEC is not None and SPEC.loader is not None
ytdlp_canary = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ytdlp_canary
SPEC.loader.exec_module(ytdlp_canary)

CanaryConfig = ytdlp_canary.CanaryConfig
CanaryError = ytdlp_canary.CanaryError
_split_urls = ytdlp_canary._split_urls
config_from_env = ytdlp_canary.config_from_env
run_one = ytdlp_canary.run_one


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request_json(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_split_urls_accepts_commas_and_lines():
    assert _split_urls("https://a\n https://b,https://c ") == (
        "https://a",
        "https://b",
        "https://c",
    )


def test_config_requires_base_url_and_token():
    with pytest.raises(CanaryError, match="BASE_URL"):
        config_from_env({"KARAOKE_CANARY_SERVICE_TOKEN": "x"})
    with pytest.raises(CanaryError, match="SERVICE_TOKEN"):
        config_from_env({"KARAOKE_CANARY_BASE_URL": "https://karaoke.example"})


def test_config_uses_defaults():
    cfg = config_from_env(
        {
            "KARAOKE_CANARY_BASE_URL": "https://karaoke.example/",
            "KARAOKE_CANARY_SERVICE_TOKEN": "token",
        }
    )
    assert isinstance(cfg, CanaryConfig)
    assert cfg.urls == ("https://www.youtube.com/watch?v=BaW_jenozKc",)
    assert cfg.timeout_s == 900
    assert cfg.poll_s == 10


def test_run_one_succeeds_once_download_stage_passed():
    client = FakeClient(
        [
            {"id": 123, "status": "queued", "progress": 0},
            {"id": 123, "status": "downloading", "progress": 20},
            {"id": 123, "status": "separating", "progress": 40},
            {"id": 123, "status": "cancelled", "progress": 40},
        ]
    )
    result = run_one(
        client,
        "https://yt/x",
        timeout_s=10,
        poll_s=1,
        sleep=lambda _s: None,
        now=iter([0, 0, 1, 1, 2, 2]).__next__,
    )
    assert result["status"] == "separating"
    assert client.calls[-1] == ("POST", "/jobs/123/cancel", None)


def test_run_one_fails_when_job_fails_before_download_passes():
    client = FakeClient(
        [
            {"id": 123, "status": "queued", "progress": 0},
            {"id": 123, "status": "failed", "progress": 10, "error": "yt-dlp broke"},
        ]
    )
    with pytest.raises(CanaryError, match="yt-dlp broke"):
        run_one(
            client,
            "https://yt/x",
            timeout_s=10,
            poll_s=1,
            sleep=lambda _s: None,
            now=iter([0, 0, 1, 1, 2, 2]).__next__,
        )
