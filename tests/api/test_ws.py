"""WebSocket live progress tests."""
from __future__ import annotations

from karaoke.api.ws import (
    publish_cost_update_threadsafe,
    publish_event_threadsafe,
    stage_event,
)
from karaoke.config import get_settings
from karaoke.db.models import JobStatus


def _disable_worker(monkeypatch):
    monkeypatch.setattr("karaoke.api.routes.schedule_job", lambda *_a, **_k: None)


def _receive_until(ws, predicate, *, limit: int = 12) -> dict:
    for _ in range(limit):
        event = ws.receive_json()
        if predicate(event):
            return event
    raise AssertionError("expected websocket event was not received")


def test_job_websocket_streams_stage_heartbeat_and_cost(client, monkeypatch):
    """A fake worker can drive /ws/{job_id} through the required event stream."""
    _disable_worker(monkeypatch)
    get_settings().ws_heartbeat_interval_s = 0.1

    create = client.post("/jobs", json={"url": "https://example.com/song"})
    assert create.status_code == 201, create.text
    job_id = create.json()["id"]

    with client.websocket_connect(f"/ws/{job_id}") as ws:
        first = ws.receive_json()
        assert first["type"] == "stage_change"
        assert first["stage"] == JobStatus.queued.value

        publish_event_threadsafe(stage_event(job_id, JobStatus.downloading, 15))
        downloading = _receive_until(
            ws,
            lambda e: e.get("type") == "stage_change"
            and e.get("stage") == JobStatus.downloading.value,
        )
        assert downloading["progress"] == 15

        heartbeat = _receive_until(ws, lambda e: e.get("type") == "heartbeat")
        assert heartbeat["stage"] == JobStatus.downloading.value
        assert heartbeat["interval_s"] == 0.1

        for stage, progress in [
            (JobStatus.separating, 45),
            (JobStatus.transcribing, 75),
            ("finalizing", 95),
            (JobStatus.completed, 100),
        ]:
            publish_event_threadsafe(stage_event(job_id, stage, progress))
            expected_stage = stage.value if isinstance(stage, JobStatus) else stage
            seen = _receive_until(
                ws,
                lambda e, expected=expected_stage: (
                    e.get("type") == "stage_change" and e.get("stage") == expected
                ),
            )
            assert seen["progress"] == progress

        publish_cost_update_threadsafe(job_id, 0.1234567, vast_instance_id="rp-1")
        cost = _receive_until(ws, lambda e: e.get("type") == "cost_update")
        assert cost["vast_cost"] == 0.123457
        assert cost["vast_instance_id"] == "rp-1"


def test_job_websocket_replays_latest_stage_to_late_subscriber(client, monkeypatch):
    _disable_worker(monkeypatch)

    create = client.post("/jobs", json={"url": "https://example.com/song"})
    assert create.status_code == 201, create.text
    job_id = create.json()["id"]

    publish_event_threadsafe(stage_event(job_id, JobStatus.separating, 45))

    with client.websocket_connect(f"/ws/{job_id}") as ws:
        replay = _receive_until(
            ws,
            lambda e: e.get("type") == "stage_change"
            and e.get("stage") == JobStatus.separating.value,
        )
        assert replay["type"] == "stage_change"
        assert replay["stage"] == JobStatus.separating.value
        assert replay["progress"] == 45
