"""Tests for the RunPod client and scheduler dispatch.

All HTTP is mocked through the ``http`` injection seam — never touches the
network. Locks down the safety properties the lead reviews:

* ``_check_daily_cap`` blocks BEFORE any HTTP call.
* per-job cost cap cancels mid-poll.
* ``finally`` POSTs cancel on the exception path AND on the success path
  it does NOT cancel (already terminal).
* scheduler dispatches mock / runpod / vast correctly.
"""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from karaoke.config import Settings
from karaoke.worker.runpod_client import (
    RunpodBudgetError,
    RunpodClient,
    RunpodError,
    RunpodFailedError,
    RunpodTimeoutError,
)


# ---------------------------------------------------------------------------
# fixtures + helpers
# ---------------------------------------------------------------------------
@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        runpod_api_key="rpa_test_key",
        runpod_endpoint_id="ep_test",
        runpod_max_job_cost=0.50,
        runpod_daily_cost_cap=5.0,
        runpod_poll_interval_s=0.0,  # tests never sleep
        runpod_request_timeout_s=5,
        runpod_wall_ceiling_s=900.0,
        runpod_hourly_rate_estimate=0.68,
        artifact_root=str(tmp_path),
    )


def _mix_wav(tmp_path: Path) -> Path:
    p = tmp_path / "mix.wav"
    p.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfake")
    return p


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class _Recorder:
    """Replays a scripted sequence of HTTP responses keyed by (method, url-suffix)."""

    def __init__(self, script: list[dict]) -> None:
        self.script = script
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method, url, api_key, body=None, *, timeout):
        self.calls.append((method, url, body))
        if not self.script:
            raise AssertionError(
                f"unscripted HTTP call: {method} {url} body={body!r}"
            )
        step = self.script.pop(0)
        # Optional sanity check on the URL fragment if the step says so.
        if step.get("expect_in") and step["expect_in"] not in url:
            raise AssertionError(
                f"expected url to contain {step['expect_in']!r}, got {url}"
            )
        return step["code"], step["body"]


# ---------------------------------------------------------------------------
# 1. daily cap blocks BEFORE any HTTP
# ---------------------------------------------------------------------------
def test_daily_cap_refuses_before_any_http(settings, tmp_path):
    settings.runpod_daily_cost_cap = 5.0
    settings.runpod_max_job_cost = 0.50
    rec = _Recorder([])  # any call must fail the test

    client = RunpodClient(
        settings,
        prior_24h_cost_micros=int(5.0 * 1_000_000),  # already AT cap
        http=rec,
    )

    with pytest.raises(RunpodBudgetError):
        client.run(_mix_wav(tmp_path), tmp_path / "work")
    assert rec.calls == [], f"daily cap must block before HTTP; calls={rec.calls!r}"


def test_daily_cap_projection_refuses_when_next_job_would_breach(settings, tmp_path):
    # spent + max_job_cost > cap → refuse
    settings.runpod_daily_cost_cap = 5.0
    settings.runpod_max_job_cost = 0.50
    rec = _Recorder([])
    client = RunpodClient(
        settings,
        prior_24h_cost_micros=int(4.80 * 1_000_000),
        http=rec,
    )
    with pytest.raises(RunpodBudgetError):
        client.run(_mix_wav(tmp_path), tmp_path / "work")
    assert rec.calls == []


# ---------------------------------------------------------------------------
# 2. happy path — COMPLETED writes artifacts
# ---------------------------------------------------------------------------
def test_run_writes_artifacts_on_completed(settings, tmp_path):
    work = tmp_path / "work"
    output = {
        "vocals_b64": _b64(b"vocals-bytes"),
        "instrumental_b64": _b64(b"instrumental-bytes"),
        "lyrics_txt": "line one\nline two",
        "lyrics_json": {"language": "en", "duration": 1.2, "segments": []},
        "gpu_model": "NVIDIA RTX A4000",
        "elapsed_s": 12.34,
    }
    rec = _Recorder([
        # POST /run -> id
        {"expect_in": "/run", "code": 200, "body": {"id": "rp-abc-123"}},
        # GET /status -> COMPLETED
        {
            "expect_in": "/status/rp-abc-123",
            "code": 200,
            "body": {
                "status": "COMPLETED",
                "executionTime": 12000,
                "output": output,
            },
        },
    ])

    client = RunpodClient(settings, http=rec)
    result = client.run(_mix_wav(tmp_path), work)

    assert (work / "vocals.wav").read_bytes() == b"vocals-bytes"
    assert (work / "instrumental.wav").read_bytes() == b"instrumental-bytes"
    assert (work / "lyrics.txt").read_text() == "line one\nline two"
    assert "language" in (work / "lyrics.json").read_text()
    assert result.vast_instance_id == "runpod-rp-abc-123"
    assert result.gpu_model == "NVIDIA RTX A4000"
    assert result.vast_cost > 0
    # No cancel was issued on the success path.
    cancel_calls = [c for c in rec.calls if "/cancel/" in c[1]]
    assert cancel_calls == [], f"happy path must not cancel; got {cancel_calls!r}"


# ---------------------------------------------------------------------------
# 3. per-job cost cap cancels mid-poll
# ---------------------------------------------------------------------------
def test_per_job_cost_cancels_and_raises(settings, tmp_path):
    # Set a tiny per-job cap and a fat reported execution time so projection
    # blows the cap on the very first poll.
    settings.runpod_max_job_cost = 0.01
    settings.runpod_hourly_rate_estimate = 100.0  # forces projection > cap fast
    rec = _Recorder([
        {"expect_in": "/run", "code": 200, "body": {"id": "rp-budget"}},
        # (no successful GET — the per-job cap trips before the next poll;
        # then the finally must POST cancel.)
        {"expect_in": "/cancel/rp-budget", "code": 200, "body": {"status": "ok"}},
    ])

    client = RunpodClient(settings, http=rec)
    # Manually pump executionTime via a side channel: set the FIRST GET to
    # report enough execution to project past the cap. We do this by adding
    # a status response BEFORE the cancel.
    rec.script.insert(
        1,
        {
            "expect_in": "/status/rp-budget",
            "code": 200,
            "body": {"status": "IN_PROGRESS", "executionTime": 60000},  # 60s
        },
    )

    with pytest.raises(RunpodBudgetError):
        client.run(_mix_wav(tmp_path), tmp_path / "work")

    # finally must have cancelled the job
    cancel_calls = [c for c in rec.calls if "/cancel/rp-budget" in c[1]]
    assert len(cancel_calls) == 1, f"expected 1 cancel POST; got {rec.calls!r}"


# ---------------------------------------------------------------------------
# 4. finally cancels on EXCEPTION path (mid-poll error)
# ---------------------------------------------------------------------------
def test_finally_cancels_on_exception_during_poll(settings, tmp_path):
    """If something throws mid-poll (e.g. our own bug), the in-flight RunPod
    job MUST still be cancelled."""

    class _Blowup:
        def __init__(self):
            self.calls = []

        def __call__(self, method, url, api_key, body=None, *, timeout):
            self.calls.append((method, url, body))
            if method == "POST" and url.endswith("/run"):
                return 200, {"id": "rp-boom"}
            if "/cancel/" in url:
                return 200, {"status": "ok"}
            # First and only GET → simulate an unexpected error:
            raise RuntimeError("synthetic transport error")

    boom = _Blowup()
    client = RunpodClient(settings, http=boom)
    with pytest.raises(RuntimeError, match="synthetic transport error"):
        client.run(_mix_wav(tmp_path), tmp_path / "work")
    cancel_calls = [c for c in boom.calls if "/cancel/rp-boom" in c[1]]
    assert len(cancel_calls) == 1, (
        f"finally must cancel on exception; calls={boom.calls!r}"
    )


# ---------------------------------------------------------------------------
# 5. terminal-FAILED does NOT double-cancel
# ---------------------------------------------------------------------------
def test_failed_status_does_not_cancel_again(settings, tmp_path):
    rec = _Recorder([
        {"expect_in": "/run", "code": 200, "body": {"id": "rp-fail"}},
        {
            "expect_in": "/status/rp-fail",
            "code": 200,
            "body": {"status": "FAILED", "error": "OOM"},
        },
    ])
    client = RunpodClient(settings, http=rec)
    with pytest.raises(RunpodFailedError, match="FAILED"):
        client.run(_mix_wav(tmp_path), tmp_path / "work")
    cancel_calls = [c for c in rec.calls if "/cancel/" in c[1]]
    assert cancel_calls == [], (
        f"FAILED is already terminal — must not POST cancel; "
        f"got {cancel_calls!r}"
    )


# ---------------------------------------------------------------------------
# 6. wall-clock ceiling
# ---------------------------------------------------------------------------
def test_wall_ceiling_raises_timeout(settings, tmp_path):
    """Wall-clock ceiling trips even if RunPod keeps replying IN_PROGRESS forever.

    Strategy: ceiling = 0; the very first loop-top check (started_wall=0) trips
    immediately, before the first GET. The finally then POSTs cancel.
    """
    settings.runpod_wall_ceiling_s = -1.0  # trip on the first loop iteration (wall>=-1 always true)
    settings.runpod_max_job_cost = 0.0  # disable per-job cost cap (cap<=0 skips)
    settings.runpod_daily_cost_cap = 0.0  # disable daily cap projection

    rec = _Recorder([
        {"expect_in": "/run", "code": 200, "body": {"id": "rp-slow"}},
        # No GET — wall ceiling trips on first loop iteration before any GET.
        # The finally must POST cancel.
        {"expect_in": "/cancel/rp-slow", "code": 200, "body": {}},
    ])

    client = RunpodClient(settings, http=rec)
    with pytest.raises(RunpodTimeoutError):
        client.run(_mix_wav(tmp_path), tmp_path / "work")
    cancel_calls = [c for c in rec.calls if "/cancel/rp-slow" in c[1]]
    assert len(cancel_calls) == 1, "wall-ceiling must cancel via finally"


# ---------------------------------------------------------------------------
# 7. config-missing errors don't even hit HTTP
# ---------------------------------------------------------------------------
def test_missing_api_key_raises_before_http(tmp_path):
    s = Settings(runpod_api_key="", runpod_endpoint_id="ep", artifact_root=str(tmp_path))
    rec = _Recorder([])
    with pytest.raises(RunpodError):
        RunpodClient(s, http=rec).run(_mix_wav(tmp_path), tmp_path / "work")
    assert rec.calls == []


def test_missing_endpoint_id_raises_before_http(tmp_path):
    s = Settings(runpod_api_key="rpa_x", runpod_endpoint_id="", artifact_root=str(tmp_path))
    rec = _Recorder([])
    with pytest.raises(RunpodError):
        RunpodClient(s, http=rec).run(_mix_wav(tmp_path), tmp_path / "work")
    assert rec.calls == []


# ---------------------------------------------------------------------------
# 8. scheduler dispatch
# ---------------------------------------------------------------------------
def test_scheduler_uses_runpod_when_only_runpod_keys_set(tmp_path):
    from karaoke.worker.scheduler import _use_mock, _use_runpod

    s = Settings(
        device_mode="auto",
        runpod_api_key="rpa_x",
        runpod_endpoint_id="ep",
        vast_api_key="",
        artifact_root=str(tmp_path),
    )
    assert not _use_mock(s)
    assert _use_runpod(s)


def test_scheduler_uses_vast_when_both_keys_set_in_auto(tmp_path):
    from karaoke.worker.scheduler import _use_mock, _use_runpod

    s = Settings(
        device_mode="auto",
        runpod_api_key="rpa_x",
        runpod_endpoint_id="ep",
        vast_api_key="vk",
        artifact_root=str(tmp_path),
    )
    assert not _use_mock(s)
    # Auto + both keys → vast wins (explicit runpod override required).
    assert not _use_runpod(s)


def test_scheduler_runpod_mode_forces_runpod(tmp_path):
    from karaoke.worker.scheduler import _use_mock, _use_runpod

    s = Settings(
        device_mode="runpod",
        runpod_api_key="rpa_x",
        runpod_endpoint_id="ep",
        vast_api_key="vk",
        artifact_root=str(tmp_path),
    )
    assert not _use_mock(s)
    assert _use_runpod(s)


def test_scheduler_falls_back_to_mock_with_no_keys(tmp_path):
    """Regression: test_routes.py and friends rely on this default."""
    from karaoke.worker.scheduler import _use_mock, _use_runpod

    s = Settings(
        device_mode="auto",
        runpod_api_key="",
        runpod_endpoint_id="",
        vast_api_key="",
        artifact_root=str(tmp_path),
    )
    assert _use_mock(s)
    assert not _use_runpod(s)


def test_scheduler_runpod_mode_without_keys_does_not_use_mock(tmp_path):
    """device_mode=runpod is explicit — even with missing keys we don't fall
    back to mock; the real client raises a clear error at runtime."""
    from karaoke.worker.scheduler import _use_mock

    s = Settings(
        device_mode="runpod",
        runpod_api_key="",
        runpod_endpoint_id="",
        artifact_root=str(tmp_path),
    )
    assert not _use_mock(s)


# ---------------------------------------------------------------------------
# 9. R2 upload path
# ---------------------------------------------------------------------------
def test_run_uses_audio_url_when_r2_configured(settings, tmp_path):
    settings.r2_endpoint_url = "https://acct.r2.cloudflarestorage.com"
    settings.r2_bucket = "karaoke-job-uploads"
    settings.r2_access_key_id = "AKID"
    settings.r2_secret_access_key = "SECRET"

    captured: dict = {}

    def fake_uploader(path: Path) -> str:
        captured["path"] = path
        return "https://acct.r2.cloudflarestorage.com/karaoke-job-uploads/jobs/123-mix.wav?X-Amz-Signature=abc"

    rec = _Recorder([
        {"expect_in": "/run", "code": 200, "body": {"id": "rp-r2-1"}},
        {
            "expect_in": "/status/rp-r2-1",
            "code": 200,
            "body": {
                "status": "COMPLETED",
                "executionTime": 5000,
                "output": {
                    "vocals_b64": _b64(b"v"),
                    "instrumental_b64": _b64(b"i"),
                    "lyrics_txt": "x",
                    "lyrics_json": {},
                    "gpu_model": "A4000",
                    "elapsed_s": 5.0,
                },
            },
        },
    ])

    client = RunpodClient(settings, http=rec, r2_uploader=fake_uploader)
    client.run(_mix_wav(tmp_path), tmp_path / "work")

    assert captured["path"].name == "mix.wav"
    # The submit body must carry audio_url, not audio_base64.
    submit_call = next(c for c in rec.calls if "/run" in c[1] and c[2])
    payload = submit_call[2]["input"]
    assert "audio_url" in payload
    assert "audio_base64" not in payload


def test_run_falls_back_to_base64_when_r2_not_configured(settings, tmp_path):
    # Default fixture has no R2 settings -> base64 path.
    rec = _Recorder([
        {"expect_in": "/run", "code": 200, "body": {"id": "rp-b64-1"}},
        {
            "expect_in": "/status/rp-b64-1",
            "code": 200,
            "body": {
                "status": "COMPLETED",
                "executionTime": 5000,
                "output": {
                    "vocals_b64": _b64(b"v"),
                    "instrumental_b64": _b64(b"i"),
                    "lyrics_txt": "x",
                    "lyrics_json": {},
                    "gpu_model": "A4000",
                    "elapsed_s": 5.0,
                },
            },
        },
    ])
    RunpodClient(settings, http=rec).run(_mix_wav(tmp_path), tmp_path / "work")
    submit_call = next(c for c in rec.calls if "/run" in c[1] and c[2])
    payload = submit_call[2]["input"]
    assert "audio_base64" in payload
    assert "audio_url" not in payload


def test_queue_ceiling_fails_fast_with_capacity_message(settings, tmp_path, monkeypatch):
    """A job stuck IN_QUEUE past the queue ceiling fails fast (and is cancelled),
    with a clear 'capacity busy' message — NOT after the long backstop."""
    settings.runpod_queue_ceiling_s = 5.0
    settings.runpod_wall_ceiling_s = 10000.0
    settings.runpod_max_job_cost = 0.0
    settings.runpod_daily_cost_cap = 0.0

    import karaoke.worker.runpod_client as mod

    # started=0; iter1 wall=0 (no trip, GET IN_QUEUE); iter2 wall=99 > queue_ceiling.
    ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0, 99.0])

    class _FakeTime:
        def monotonic(self):
            return next(ticks, 99.0)

        def sleep(self, *_):
            pass

    monkeypatch.setattr(mod, "time", _FakeTime())

    rec = _Recorder([
        {"expect_in": "/run", "code": 200, "body": {"id": "rp-queue"}},
        {"expect_in": "/status/rp-queue", "code": 200, "body": {"status": "IN_QUEUE"}},
        {"expect_in": "/cancel/rp-queue", "code": 200, "body": {}},
    ])
    client = RunpodClient(settings, http=rec)
    with pytest.raises(RunpodTimeoutError, match="capacity busy"):
        client.run(_mix_wav(tmp_path), tmp_path / "work")
    assert [c for c in rec.calls if "/cancel/rp-queue" in c[1]], "queue fail must cancel"


def test_in_progress_not_killed_by_queue_ceiling(settings, tmp_path, monkeypatch):
    """Once a job is IN_PROGRESS, the queue ceiling must NOT abort it — even
    though wall-clock is well past the queue ceiling (it waited in queue, then
    a worker picked it up). It runs to COMPLETED."""
    settings.runpod_queue_ceiling_s = 5.0
    settings.runpod_wall_ceiling_s = 10000.0
    settings.runpod_max_job_cost = 0.0
    settings.runpod_daily_cost_cap = 0.0

    import karaoke.worker.runpod_client as mod

    # started=0; iter1 wall=0 -> GET IN_PROGRESS (exec_phase=True);
    # iter2 wall=500 (>> queue_ceiling) but exec_phase so NO trip -> GET COMPLETED.
    ticks = iter([0.0, 0.0, 500.0, 500.0, 500.0])

    class _FakeTime:
        def monotonic(self):
            return next(ticks, 500.0)

        def sleep(self, *_):
            pass

    monkeypatch.setattr(mod, "time", _FakeTime())

    output = {
        "vocals_b64": _b64(b"v"),
        "instrumental_b64": _b64(b"i"),
        "lyrics_txt": "x",
        "lyrics_json": {},
        "gpu_model": "L40",
        "elapsed_s": 31.0,
    }
    rec = _Recorder([
        {"expect_in": "/run", "code": 200, "body": {"id": "rp-run"}},
        {"expect_in": "/status/rp-run", "code": 200,
         "body": {"status": "IN_PROGRESS", "delayTime": 490000}},
        {"expect_in": "/status/rp-run", "code": 200,
         "body": {"status": "COMPLETED", "executionTime": 31000, "output": output}},
    ])
    client = RunpodClient(settings, http=rec)
    result = client.run(_mix_wav(tmp_path), tmp_path / "work")
    assert (tmp_path / "work" / "vocals.wav").exists()
    assert result.vast_instance_id == "runpod-rp-run"
    # A running job that completed must NOT have been cancelled.
    assert not [c for c in rec.calls if "/cancel/" in c[1]], "must not cancel a running job"
