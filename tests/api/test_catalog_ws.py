"""Catalog access at the WebSocket boundary, also exercised on rollback."""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from karaoke.api import ws
from karaoke.catalog_identity import PILOT_OWNER

from .test_auth import _sqlite_path, _unverified_jwt

OWNER = "user_catalog_owner"
OTHER = "user_other_owner"
PILOT_JOB = 10
OWNER_LINK = 11
LEGACY_JOB = 12
PRIVATE_ERROR = "download failed for https://example.test/private-source"


def _clerk_token(subject: str) -> str:
    return _unverified_jwt({"sub": subject, "exp": 4_000_000_000})


def _clerk_header(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_clerk_token(subject)}"}


@pytest.fixture
def catalog_rows(client):
    now = dt.datetime.now(dt.UTC).isoformat()
    with sqlite3.connect(_sqlite_path()) as db:
        db.executemany(
            """INSERT INTO jobs (id, job_token, owner_subject, source_url, status,
               progress, stage_note, error, created_at, updated_at)
               VALUES (?, ?, ?, 'https://example.test/source', 'failed', 15,
                       'private stage note', ?, ?, ?)""",
            [
                (PILOT_JOB, "pilot-storage", PILOT_OWNER, PRIVATE_ERROR, now, now),
                (OWNER_LINK, "clerk.owner-link", OWNER, PRIVATE_ERROR, now, now),
                (LEGACY_JOB, "old-share-token", OWNER, "ordinary failure", now, now),
            ],
        )
    return client


def _assert_not_found(frame: dict, job_id: int) -> None:
    assert frame["type"] == "error"
    assert frame["job_id"] == job_id
    assert frame["error"] == "job not found"
    assert "progress" not in frame
    assert "stage_note" not in frame


@pytest.mark.parametrize("viewer", [{}, _clerk_header(OTHER), {"Authorization": "Bearer test-service-token"}])
@pytest.mark.parametrize("cache", [False, True])
def test_catalog_ws_replay_hides_pilot_and_owner_link(catalog_rows, viewer, cache):
    if cache:
        hub = ws.get_hub()
        for job_id in (PILOT_JOB, OWNER_LINK):
            hub.publish_threadsafe(ws.make_stage_event(job_id, "failed", 23, error=PRIVATE_ERROR))
            hub.publish_threadsafe(ws.make_heartbeat_event(job_id, "downloading", 22))

    for job_id in (PILOT_JOB, OWNER_LINK):
        with catalog_rows.websocket_connect(f"/ws/{job_id}", headers=dict(viewer)) as socket:
            _assert_not_found(socket.receive_json(), job_id)
        with catalog_rows.websocket_connect("/ws", headers=dict(viewer)) as socket:
            socket.send_json({"action": "subscribe", "job_id": job_id})
            _assert_not_found(socket.receive_json(), job_id)


@pytest.mark.parametrize("viewer", [{}, _clerk_header(OTHER), _clerk_header(OWNER)])
def test_catalog_ws_broadcast_filters_all_event_types(catalog_rows, viewer):
    hub = ws.get_hub()
    with catalog_rows.websocket_connect("/ws", headers=dict(viewer)) as socket:
        for job_id in (PILOT_JOB, OWNER_LINK):
            hub.publish_threadsafe(ws.make_error_event(job_id, PRIVATE_ERROR))
            hub.publish_threadsafe(ws.make_cost_event(job_id, 0.02, vast_instance_id="private-id"))
            hub.publish_threadsafe(ws.make_heartbeat_event(job_id, "downloading", 22))
            hub.publish_threadsafe(ws.make_stage_event(job_id, "failed", 23, error=PRIVATE_ERROR))
        hub.publish_threadsafe(ws.make_stage_event(LEGACY_JOB, "completed", 100))
        if viewer == _clerk_header(OWNER):
            for kind in (ws.ERROR, ws.COST_UPDATE, ws.HEARTBEAT, ws.STAGE_CHANGE):
                event = socket.receive_json()
                assert (event["job_id"], event["type"]) == (OWNER_LINK, kind)
        event = socket.receive_json()
        assert (event["job_id"], event["type"]) == (LEGACY_JOB, ws.STAGE_CHANGE)


def test_owning_clerk_can_subscribe_and_replay_catalog_link(catalog_rows):
    with catalog_rows.websocket_connect("/ws") as socket:
        socket.send_json({"action": "auth", "token": _clerk_token(OWNER)})
        socket.send_json({"action": "subscribe", "job_id": PILOT_JOB})
        _assert_not_found(socket.receive_json(), PILOT_JOB)
        socket.send_json({"action": "subscribe", "job_id": OWNER_LINK})
        frame = socket.receive_json()
        assert frame["type"] == "stage_change"
        assert frame["job_id"] == OWNER_LINK
        assert frame["error"] == PRIVATE_ERROR
        socket.send_json({"action": "auth", "token": _clerk_token(OTHER)})
        socket.send_json({"action": "subscribe", "job_id": OWNER_LINK})
        _assert_not_found(socket.receive_json(), OWNER_LINK)

    with catalog_rows.websocket_connect(f"/ws/{OWNER_LINK}", headers=_clerk_header(OWNER)) as socket:
        assert socket.receive_json()["job_id"] == OWNER_LINK
        ws.get_hub().publish_threadsafe(ws.make_stage_event(OWNER_LINK, "completed", 100))
        assert socket.receive_json()["status"] == "completed"


def test_missing_catalog_row_cannot_replay_stale_cached_event(catalog_rows):
    ws.get_hub().publish_threadsafe(ws.make_stage_event(OWNER_LINK, "failed", 23, error=PRIVATE_ERROR))
    with sqlite3.connect(_sqlite_path()) as db:
        db.execute("DELETE FROM jobs WHERE id = ?", (OWNER_LINK,))
    with catalog_rows.websocket_connect(f"/ws/{OWNER_LINK}") as socket:
        _assert_not_found(socket.receive_json(), OWNER_LINK)
