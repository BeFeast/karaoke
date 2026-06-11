"""Smoke tests for the karaoke HTTP routes."""
from __future__ import annotations

import time

from karaoke.db.models import JobStatus


def test_health_is_public(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_post_jobs_returns_initial_payload(client):
    response = client.post("/jobs", json={"url": "https://example.com/song", "title": "Song"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_url"] == "https://example.com/song"
    assert body["title"] == "Song"
    assert body["progress"] == 0
    assert body["status"] in {JobStatus.queued.value, JobStatus.downloading.value}
    assert body["share_url"].startswith("http://test.local/share/")
    assert body["job_token"] in body["share_url"]


def test_get_status_owner_isolation(client):
    """A clerk-authenticated user cannot read another clerk user's job."""
    # alice creates a job via Clerk-test-mode JWT.
    import base64
    import json

    def _jwt(sub: str) -> str:
        header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(
            json.dumps({"sub": sub, "email": f"{sub}@example.com", "exp": int(time.time()) + 600}).encode()
        ).rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
        return f"{header}.{body}.{sig}"

    create = client.post(
        "/jobs",
        json={"url": "https://x"},
        headers={"Authorization": f"Bearer {_jwt('alice')}"},
    )
    assert create.status_code == 201, create.text
    job = create.json()

    # Alice can read it.
    own = client.get(
        f"/jobs/{job['id']}/status",
        headers={"Authorization": f"Bearer {_jwt('alice')}"},
    )
    assert own.status_code == 200, own.text

    # Bob cannot — gets 404 (not 403, to hide existence).
    other = client.get(
        f"/jobs/{job['id']}/status",
        headers={"Authorization": f"Bearer {_jwt('bob')}"},
    )
    assert other.status_code == 404


def test_share_endpoint_works_with_unlisted_token(client):
    """Anyone holding the job_token can fetch the share payload (it IS the secret)."""
    # Create as LAN-trusted (default conftest).
    create = client.post("/jobs", json={"url": "https://example.com/x", "title": "Tune"})
    assert create.status_code == 201
    token = create.json()["job_token"]

    # Wait briefly for the mock worker to advance state at least once.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        share = client.get(f"/api/share/{token}")
        if share.status_code == 200 and share.json()["status"] == JobStatus.completed.value:
            break
        time.sleep(0.05)

    share = client.get(f"/api/share/{token}")
    assert share.status_code == 200, share.text
    body = share.json()
    assert body["job_token"] == token
    assert body["title"] == "Tune"
    # Mock worker eventually populates artifacts.
    assert body["status"] in {
        JobStatus.completed.value,
        JobStatus.transcribing.value,
        JobStatus.separating.value,
        JobStatus.downloading.value,
        JobStatus.queued.value,
    }


def test_share_endpoint_404_for_unknown_token(client):
    response = client.get("/api/share/does-not-exist")
    assert response.status_code == 404


def test_status_404_for_missing_job(client):
    response = client.get("/jobs/9999/status")
    assert response.status_code == 404


def test_mock_worker_completes_job(client):
    """The mocked worker should drive the job to completed within a reasonable window."""
    create = client.post("/jobs", json={"url": "https://example.com/song"})
    job_id = create.json()["id"]

    deadline = time.monotonic() + 5.0
    last_status: str | None = None
    while time.monotonic() < deadline:
        st = client.get(f"/jobs/{job_id}/status").json()
        last_status = st["status"]
        if last_status == JobStatus.completed.value:
            assert st["progress"] == 100
            return
        time.sleep(0.05)

    raise AssertionError(f"job did not complete; last status = {last_status}")


def test_websocket_streams_progress_to_completion(client):
    """``WS /ws`` should emit at least one progress frame and a terminal frame."""
    create = client.post("/jobs", json={"url": "https://example.com/song"})
    job_id = create.json()["id"]

    with client.websocket_connect("/ws") as ws:
        ws.send_json({"action": "subscribe", "job_id": job_id})
        frames: list[dict] = []
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            frame = ws.receive_json()
            frames.append(frame)
            if frame.get("status") == JobStatus.completed.value:
                break

    assert frames, "no frames received"
    assert any(f.get("status") == JobStatus.completed.value for f in frames)
    assert any(f.get("progress") == 100 for f in frames)
