"""Tests for the WebSocket live-progress channel (issue #8).

Covers the per-job feed (``WS /ws/{job_id}``), the broadcast feed
(``WS /ws``), late-subscriber replay (hub cache + DB fallback), heartbeats
at the configured interval, cost events, and the worker→hub wiring.
"""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from karaoke.api import ws as ws_module
from karaoke.api.app import create_app
from karaoke.config import reset_settings_for_tests
from karaoke.db.models import Base, Job, JobStatus
from karaoke.db.session import create_engine_and_sessionmaker

# Canonical lifecycle order for ordering assertions (issue #8 vocabulary).
_STAGE_ORDER = [
    "queued",
    "downloading",
    "separating",
    "transcribing",
    "finalizing",
    "completed",
]


def _publish_stage(hub, job_id: int, status: str, progress: int) -> None:
    hub.publish_threadsafe(ws_module.make_stage_event(job_id, status, progress))


# ---------------------------------------------------------------------------
# per-job feed
# ---------------------------------------------------------------------------


def test_per_job_feed_streams_fake_job_through_all_stages(client):
    """A subscriber sees a stage_change for every transition a (fake) worker
    performs, plus cost_update and error events, in publish order."""
    hub = ws_module.get_hub()
    job_id = 7777
    _publish_stage(hub, job_id, "queued", 0)

    with client.websocket_connect(f"/ws/{job_id}") as sock:
        replay = sock.receive_json()
        assert replay["type"] == "stage_change"
        assert replay["status"] == "queued"
        assert replay["job_id"] == job_id

        # Drive the fake job through the full lifecycle from the test thread —
        # this exercises the same thread-safe path the vast callback uses.
        for status, progress in [
            ("downloading", 25),
            ("separating", 60),
            ("transcribing", 90),
        ]:
            _publish_stage(hub, job_id, status, progress)
        hub.publish_threadsafe(
            ws_module.make_cost_event(
                job_id, 0.0, vast_instance_id="vast-1", phase="provisioned"
            )
        )
        hub.publish_threadsafe(
            ws_module.make_cost_event(
                job_id, 0.1234, vast_instance_id="vast-1", phase="teardown"
            )
        )
        _publish_stage(hub, job_id, "finalizing", 95)
        _publish_stage(hub, job_id, "completed", 100)

        frames = []
        while True:
            frame = sock.receive_json()
            frames.append(frame)
            if frame.get("status") == "completed":
                break

    stage_frames = [f for f in frames if f["type"] == "stage_change"]
    assert [f["status"] for f in stage_frames] == [
        "downloading",
        "separating",
        "transcribing",
        "finalizing",
        "completed",
    ]
    cost_frames = [f for f in frames if f["type"] == "cost_update"]
    assert [c["phase"] for c in cost_frames] == ["provisioned", "teardown"]
    assert cost_frames[0]["vast_cost"] == 0.0
    assert cost_frames[1]["vast_cost"] == pytest.approx(0.1234)
    assert all(c["vast_instance_id"] == "vast-1" for c in cost_frames)
    assert all(f["job_id"] == job_id and f["ts"] for f in frames)


def test_per_job_feed_streams_mock_worker_to_completion(client):
    """End-to-end: the mock worker's transitions reach a /ws/{job_id} client."""
    create = client.post("/jobs", json={"url": "https://example.com/song"})
    assert create.status_code == 201
    job_id = create.json()["id"]

    with client.websocket_connect(f"/ws/{job_id}") as sock:
        frames = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            frame = sock.receive_json()
            frames.append(frame)
            if frame.get("status") == JobStatus.completed.value:
                break

    assert frames, "no frames received"
    assert all(f["job_id"] == job_id for f in frames)
    statuses = [f["status"] for f in frames if f["type"] == "stage_change"]
    assert statuses[-1] == "completed"
    # Whatever subset of transitions we caught (the sleep=0 mock races the
    # subscribe; the replay frame may duplicate a live frame), the observed
    # order must follow the canonical lifecycle.
    deduped = [s for i, s in enumerate(statuses) if i == 0 or statuses[i - 1] != s]
    assert deduped == sorted(set(deduped), key=_STAGE_ORDER.index)


def test_per_job_feed_unknown_job_gets_error_event(client):
    """No hub cache and no DB row → a typed error event, then close."""
    with client.websocket_connect("/ws/999999") as sock:
        frame = sock.receive_json()
    assert frame["type"] == "error"
    assert frame["job_id"] == 999999
    assert "not found" in frame["error"]


# ---------------------------------------------------------------------------
# late subscribers / replay
# ---------------------------------------------------------------------------


def test_late_subscriber_replays_latest_cached_stage(client):
    """A subscriber connecting mid-job immediately sees the latest stage."""
    hub = ws_module.get_hub()
    job_id = 4242
    _publish_stage(hub, job_id, "downloading", 25)
    _publish_stage(hub, job_id, "separating", 60)

    with client.websocket_connect(f"/ws/{job_id}") as sock:
        replay = sock.receive_json()

    assert replay["type"] == "stage_change"
    assert replay["status"] == "separating"
    assert replay["progress"] == 60

    # Clean up the fake job's heartbeat task.
    _publish_stage(hub, job_id, "completed", 100)


def test_late_subscriber_replays_from_db_after_hub_restart(client):
    """Hub cache gone (process restart) → replay falls back to the Job row."""
    create = client.post("/jobs", json={"url": "https://example.com/song"})
    job_id = create.json()["id"]

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if (
            client.get(f"/jobs/{job_id}/status").json()["status"]
            == JobStatus.completed.value
        ):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("mock job did not complete in time")

    ws_module.reset_hub_for_tests()  # simulate a coordinator restart

    with client.websocket_connect(f"/ws/{job_id}") as sock:
        replay = sock.receive_json()

    assert replay["type"] == "stage_change"
    assert replay["status"] == JobStatus.completed.value
    assert replay["progress"] == 100


# ---------------------------------------------------------------------------
# broadcast feed
# ---------------------------------------------------------------------------


def test_broadcast_feed_carries_all_jobs(client):
    """``WS /ws`` without a subscribe message receives every job's events."""
    hub = ws_module.get_hub()
    with client.websocket_connect("/ws") as sock:
        _publish_stage(hub, 11, "downloading", 25)
        _publish_stage(hub, 22, "separating", 60)
        first = sock.receive_json()
        second = sock.receive_json()
        # Terminal events so the fake jobs' heartbeat tasks stop.
        _publish_stage(hub, 11, "failed", 25)
        _publish_stage(hub, 22, "failed", 60)

    assert {first["job_id"], second["job_id"]} == {11, 22}


def test_broadcast_feed_legacy_subscribe_narrows_to_one_job(client):
    """The pre-#8 ``{"action": "subscribe", "job_id": N}`` protocol still
    works: replay arrives, then only that job's events."""
    hub = ws_module.get_hub()
    _publish_stage(hub, 33, "downloading", 25)

    with client.websocket_connect("/ws") as sock:
        sock.send_json({"action": "subscribe", "job_id": 33})
        replay = sock.receive_json()
        assert replay["status"] == "downloading"
        assert replay["job_id"] == 33

        _publish_stage(hub, 44, "separating", 60)  # filtered out
        _publish_stage(hub, 33, "completed", 100)
        frame = sock.receive_json()

    assert frame["job_id"] == 33
    assert frame["status"] == "completed"
    _publish_stage(hub, 44, "failed", 60)  # stop the other heartbeat task


# ---------------------------------------------------------------------------
# heartbeats
# ---------------------------------------------------------------------------


def test_heartbeats_arrive_at_configured_interval_while_in_progress(monkeypatch):
    """While a stage is in progress, heartbeats tick at the configured
    interval (default 5s; cranked down here to keep the test fast)."""
    monkeypatch.setenv("KARAOKE_WS_HEARTBEAT_INTERVAL_S", "0.05")
    reset_settings_for_tests()

    app = create_app()
    with TestClient(app) as tc:
        hub = ws_module.get_hub()
        job_id = 555
        _publish_stage(hub, job_id, "transcribing", 90)

        with tc.websocket_connect(f"/ws/{job_id}") as sock:
            replay = sock.receive_json()
            assert replay["type"] == "stage_change"

            beats = []
            deadline = time.monotonic() + 5.0
            while len(beats) < 3 and time.monotonic() < deadline:
                frame = sock.receive_json()
                if frame["type"] == "heartbeat":
                    beats.append(frame)

        assert len(beats) >= 3, "expected recurring heartbeats"
        assert all(b["status"] == "transcribing" for b in beats)
        assert all(b["progress"] == 90 for b in beats)

        _publish_stage(hub, job_id, "completed", 100)  # stops the heartbeat


def test_heartbeat_replayed_to_late_subscriber(monkeypatch):
    """On connect the server replays the latest heartbeat after the stage."""
    monkeypatch.setenv("KARAOKE_WS_HEARTBEAT_INTERVAL_S", "0.05")
    reset_settings_for_tests()

    app = create_app()
    with TestClient(app) as tc:
        hub = ws_module.get_hub()
        job_id = 556
        _publish_stage(hub, job_id, "separating", 60)
        # Let at least one heartbeat tick land in the cache.
        deadline = time.monotonic() + 5.0
        while hub.latest_heartbeat(job_id) is None:
            assert time.monotonic() < deadline, "no heartbeat was cached"
            time.sleep(0.02)

        with tc.websocket_connect(f"/ws/{job_id}") as sock:
            first = sock.receive_json()
            second = sock.receive_json()

        assert first["type"] == "stage_change"
        assert first["status"] == "separating"
        assert second["type"] == "heartbeat"
        assert second["status"] == "separating"

        _publish_stage(hub, job_id, "failed", 60)  # stops the heartbeat


# ---------------------------------------------------------------------------
# API-side event wiring
# ---------------------------------------------------------------------------


def test_cancel_publishes_terminal_stage_change(client):
    """POST /jobs/{id}/cancel pushes a ``cancelled`` stage_change."""
    create = client.post("/jobs", json={"url": "https://example.com/song"})
    job_id = create.json()["id"]

    with client.websocket_connect(f"/ws/{job_id}") as sock:
        cancel = client.post(f"/jobs/{job_id}/cancel")
        if cancel.status_code != 200:
            # The sleep=0 mock worker beat us to a terminal state.
            assert cancel.status_code == 409
            return
        # A successful cancel MUST push its stage_change onto the stream —
        # even when the sleep=0 mock worker races a ``completed`` commit in
        # alongside it (the events may arrive in either order).
        frame = None
        for _ in range(50):
            frame = sock.receive_json()
            if frame.get("status") == JobStatus.cancelled.value:
                break
        assert frame is not None
        assert frame["type"] == "stage_change"
        assert frame["status"] == JobStatus.cancelled.value
        assert frame["job_id"] == job_id


# ---------------------------------------------------------------------------
# worker→hub wiring (pipeline helpers)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def factory(tmp_path):
    """Isolated async session factory (mirrors the worker pipeline tests)."""
    url = f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}"
    engine, fac = create_engine_and_sessionmaker(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield fac
    finally:
        await engine.dispose()


async def _make_job(session_factory) -> int:
    async with session_factory() as session:
        job = Job(
            job_token="tok-ws",
            owner_subject="owner",
            source_url="https://example.com/song",
            status=JobStatus.queued,
            progress=0,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job.id


@pytest.mark.asyncio
async def test_pipeline_set_stage_publishes_stage_change(factory):
    from karaoke.worker.pipeline import _set_stage

    job_id = await _make_job(factory)
    hub = ws_module.get_hub()
    queue = hub.subscribe(job_id)
    try:
        assert await _set_stage(
            factory, job_id, JobStatus.downloading, 15, stage_note="dl"
        )
        event = queue.get_nowait()
        assert event["type"] == "stage_change"
        assert event["status"] == "downloading"
        assert event["progress"] == 15
        assert event["stage_note"] == "dl"
    finally:
        hub.unsubscribe(queue, job_id)
        ws_module.publish_stage(job_id, JobStatus.failed, 15)  # stop heartbeat


@pytest.mark.asyncio
async def test_pipeline_mark_failed_publishes_error_and_terminal_stage(factory):
    from karaoke.worker.pipeline import _mark_failed

    job_id = await _make_job(factory)
    hub = ws_module.get_hub()
    queue = hub.subscribe(job_id)
    try:
        await _mark_failed(factory, job_id, "boom: GPU exploded")
        error_event = queue.get_nowait()
        stage_event = queue.get_nowait()
        assert error_event["type"] == "error"
        assert "GPU exploded" in error_event["error"]
        assert stage_event["type"] == "stage_change"
        assert stage_event["status"] == "failed"
        assert "GPU exploded" in stage_event["error"]
    finally:
        hub.unsubscribe(queue, job_id)


@pytest.mark.asyncio
async def test_vast_provision_callback_publishes_cost_update_threadsafe():
    """The pipeline's on_instance_created callback (run inside
    ``asyncio.to_thread``) lands a ``provisioned`` cost_update on the loop."""
    from karaoke.worker.pipeline import _provision_cost_publisher

    hub = ws_module.get_hub()
    hub.bind_loop(asyncio.get_running_loop())
    queue = hub.subscribe(88)
    try:
        await asyncio.to_thread(_provision_cost_publisher(88), 123456)
        event = await asyncio.wait_for(queue.get(), timeout=2.0)
        assert event["type"] == "cost_update"
        assert event["phase"] == "provisioned"
        assert event["vast_instance_id"] == "123456"
        assert event["vast_cost"] == 0.0
    finally:
        hub.unsubscribe(queue, 88)
