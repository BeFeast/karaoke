"""Bounded retry for the R2 PUT/GET calls (429 seen mid-GPU-stage, 2026-11-24)."""
from __future__ import annotations

import email.message
import io
import urllib.error

import pytest

from karaoke.worker import r2_client


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://r2.example/b/k", code, "err", headers, io.BytesIO(b""))


def _patch(monkeypatch, outcomes):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(r2_client.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_retries_429_then_succeeds(monkeypatch):
    sleeps = []
    calls = _patch(monkeypatch, [_http_error(429, "1"), _http_error(503), "ok"])
    assert r2_client.urlopen_retry("https://r2.example/b/k", timeout=5, sleep=sleeps.append) == "ok"
    assert len(calls) == 3
    assert sleeps == [2.0, 4.0]


def test_honours_retry_after_but_caps_it(monkeypatch):
    sleeps = []
    _patch(monkeypatch, [_http_error(429, "120"), "ok"])
    r2_client.urlopen_retry("https://r2.example/b/k", timeout=5, sleep=sleeps.append)
    assert sleeps == [10.0]


def test_gives_up_after_three_attempts(monkeypatch):
    sleeps = []
    calls = _patch(monkeypatch, [_http_error(429)] * 3)
    with pytest.raises(urllib.error.HTTPError) as info:
        r2_client.urlopen_retry("https://r2.example/b/k", timeout=5, sleep=sleeps.append)
    assert info.value.code == 429
    assert len(calls) == 3
    assert len(sleeps) == 2


def test_does_not_retry_client_errors(monkeypatch):
    calls = _patch(monkeypatch, [_http_error(403)])
    with pytest.raises(urllib.error.HTTPError):
        r2_client.urlopen_retry("https://r2.example/b/k", timeout=5, sleep=lambda _: None)
    assert len(calls) == 1
