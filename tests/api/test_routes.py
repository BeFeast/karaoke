"""Smoke tests for the karaoke HTTP routes."""
from __future__ import annotations

import time
from pathlib import Path

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

    # JSON path — same auth, but explicit Accept negotiates to the API payload.
    json_headers = {"Accept": "application/json"}
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        share = client.get(f"/share/{token}", headers=json_headers)
        if share.status_code == 200 and share.json()["status"] == JobStatus.completed.value:
            break
        time.sleep(0.05)

    share = client.get(f"/share/{token}", headers=json_headers)
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
    response = client.get("/share/does-not-exist")
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


def test_share_endpoint_renders_html_for_browsers(client):
    """Default Accept (or text/html) returns the HTML share page with audio tags."""
    create = client.post("/jobs", json={"url": "https://example.com/x", "title": "TuneHTML"})
    assert create.status_code == 201
    token = create.json()["job_token"]

    # Wait for the mock worker to populate Artifact rows.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        s = client.get(f"/share/{token}", headers={"Accept": "application/json"})
        if s.status_code == 200 and s.json()["status"] == JobStatus.completed.value:
            break
        time.sleep(0.05)

    resp = client.get(f"/share/{token}", headers={"Accept": "text/html"})
    assert resp.status_code == 200, resp.text
    body = resp.text
    assert "<audio" in body, "expected an <audio> player in HTML share page"
    assert f"/share/{token}/karaoke.mp3" in body
    assert f"/share/{token}/vocals.mp3" in body
    assert "TuneHTML" in body


def test_share_artifact_404_for_unknown_artifact_name(client):
    create = client.post("/jobs", json={"url": "https://example.com/x"})
    token = create.json()["job_token"]
    # Path-traversal / random names: must 404, not serve.
    for bad in ("../../../etc/passwd", "secret.env", "lyrics.json"):
        r = client.get(f"/share/{token}/{bad}")
        assert r.status_code == 404, (bad, r.status_code, r.text)


def test_share_artifact_404_when_file_missing(client, tmp_path):
    create = client.post("/jobs", json={"url": "https://example.com/x"})
    token = create.json()["job_token"]
    r = client.get(f"/share/{token}/karaoke.mp3")
    # Default artifact_root in tests is /srv/artifacts (not present) -> 404.
    assert r.status_code == 404


def test_share_artifact_serves_file_when_present(client, tmp_path):
    from karaoke.config import Settings, get_settings

    create = client.post("/jobs", json={"url": "https://example.com/x"})
    token = create.json()["job_token"]

    exports = Path(tmp_path) / token / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "karaoke.mp3").write_bytes(b"ID3\x04\x00fake-mp3-bytes")

    def fake_settings() -> Settings:
        return Settings(artifact_root=str(tmp_path))

    client.app.dependency_overrides[get_settings] = fake_settings
    try:
        r = client.get(f"/share/{token}/karaoke.mp3")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)

    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("audio/mpeg")
    assert r.content.startswith(b"ID3")
