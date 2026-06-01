"""Per-job ephemeral YouTube cookies on ``POST /jobs`` (issue #77).

The submitting client may attach a Netscape ``cookies.txt`` blob to a job. It
is validated at submit, carried in-memory only (never the DB), never logged,
and consumed once by the worker's download stage. These tests pin that
contract at the API boundary; the worker-side handling is covered in
``tests/worker/test_pipeline_perjob_cookies.py``.
"""
from __future__ import annotations

import logging

import pytest

from karaoke.worker import job_cookies

TAB = "\t"
_HEADER = "# Netscape HTTP Cookie File"
_SECRET = "perjob-secret-sid-value"


def _valid_blob(secret: str = _SECRET) -> str:
    """A minimal but well-formed logged-in YouTube jar."""
    lines = [
        _HEADER,
        TAB.join([".youtube.com", "TRUE", "/", "TRUE", "2147483647", "SID", secret]),
        TAB.join([".google.com", "TRUE", "/", "TRUE", "2147483647", "SAPISID", "g-secret"]),
    ]
    return "\n".join(lines) + "\n"


@pytest.fixture(autouse=True)
def _clear_registry():
    """The ephemeral registry is module-global; isolate every test."""
    job_cookies._PENDING.clear()
    yield
    job_cookies._PENDING.clear()


def _no_worker(monkeypatch):
    """Neutralise the scheduler so the stashed blob is not consumed mid-test."""
    monkeypatch.setattr("karaoke.api.routes.schedule_job", lambda *a, **k: None)


def test_post_jobs_accepts_and_stashes_cookies(client, monkeypatch):
    _no_worker(monkeypatch)
    blob = _valid_blob()
    resp = client.post("/jobs", json={"url": "https://yt/x", "youtube_cookies": blob})
    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    # Carried in the ephemeral registry keyed by job id — popping it proves the
    # blob was held in memory (never the DB) and is exactly what was submitted.
    assert job_cookies.pop(job_id) == blob
    # The response never echoes the cookie value.
    assert _SECRET not in resp.text


def test_post_jobs_without_cookies_stashes_nothing(client, monkeypatch):
    _no_worker(monkeypatch)
    resp = client.post("/jobs", json={"url": "https://yt/x"})
    assert resp.status_code == 201
    assert job_cookies.pop(resp.json()["id"]) is None
    assert job_cookies._PENDING == {}


def test_post_jobs_blank_cookies_treated_as_absent(client, monkeypatch):
    _no_worker(monkeypatch)
    resp = client.post(
        "/jobs", json={"url": "https://yt/x", "youtube_cookies": "   \n  "}
    )
    assert resp.status_code == 201
    assert job_cookies.pop(resp.json()["id"]) is None


def test_post_jobs_malformed_cookies_rejected_no_job(client, monkeypatch):
    _no_worker(monkeypatch)
    bad = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tonly\tfour\n"
    resp = client.post("/jobs", json={"url": "https://yt/x", "youtube_cookies": bad})
    assert resp.status_code == 422
    # Value-free error — never echoes the offending field tokens.
    assert "only" not in resp.text and "four" not in resp.text
    # No job was created and nothing was stashed.
    assert client.get("/jobs").json() == []
    assert job_cookies._PENDING == {}


def test_post_jobs_cookies_without_youtube_rejected(client, monkeypatch):
    _no_worker(monkeypatch)
    blob = (
        "# Netscape HTTP Cookie File\n"
        + TAB.join([".example.com", "TRUE", "/", "FALSE", "0", "x", "y"])
        + "\n"
    )
    resp = client.post("/jobs", json={"url": "https://yt/x", "youtube_cookies": blob})
    assert resp.status_code == 422
    assert "youtube" in resp.json()["detail"]
    assert client.get("/jobs").json() == []


def test_post_jobs_oversized_cookies_rejected(client, monkeypatch):
    _no_worker(monkeypatch)
    huge = _valid_blob() + ("x" * (1024 * 1024 + 1))
    resp = client.post("/jobs", json={"url": "https://yt/x", "youtube_cookies": huge})
    assert resp.status_code == 413
    assert client.get("/jobs").json() == []
    assert job_cookies._PENDING == {}


def test_post_jobs_cookie_value_never_logged(client, monkeypatch, caplog):
    _no_worker(monkeypatch)
    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/jobs", json={"url": "https://yt/x", "youtube_cookies": _valid_blob()}
        )
    assert resp.status_code == 201
    assert _SECRET not in caplog.text


def test_cancel_discards_stashed_cookies(client, monkeypatch):
    # Cancel before the worker pops the blob must clear the registry (#77).
    _no_worker(monkeypatch)
    resp = client.post(
        "/jobs", json={"url": "https://yt/x", "youtube_cookies": _valid_blob()}
    )
    job_id = resp.json()["id"]
    assert job_id in job_cookies._PENDING
    cancelled = client.post(f"/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert job_cookies.pop(job_id) is None


def test_delete_discards_stashed_cookies(client, monkeypatch):
    # Delete before the worker pops the blob must clear the registry (#77).
    _no_worker(monkeypatch)
    resp = client.post(
        "/jobs", json={"url": "https://yt/x", "youtube_cookies": _valid_blob()}
    )
    job_id = resp.json()["id"]
    assert job_id in job_cookies._PENDING
    deleted = client.delete(f"/jobs/{job_id}")
    assert deleted.status_code == 204
    assert job_cookies.pop(job_id) is None
