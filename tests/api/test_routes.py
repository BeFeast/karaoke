"""Smoke tests for the karaoke HTTP routes."""
from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

from karaoke.db.models import JobStatus


def _clerk_jwt(sub: str) -> str:
    """Forge a Clerk-test-mode JWT (signature unchecked in KARAOKE_AUTH_TEST_MODE)."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(
        json.dumps(
            {"sub": sub, "email": f"{sub}@example.com", "exp": int(time.time()) + 600}
        ).encode()
    ).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


def _seed_job(status: str, *, owner_subject: str = "lan-default") -> tuple[int, str]:
    """Insert a job row directly (the mock never produces failed/long-queued
    states). Uses a parallel sqlite3 connection — the pattern the conftest
    explicitly supports with its file-backed test DB."""
    db_path = os.environ["KARAOKE_DATABASE_URL"].split("///", 1)[1]
    token = secrets.token_urlsafe(16)
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA busy_timeout=3000")
        con.execute(
            "INSERT INTO jobs "
            "(job_token, owner_subject, source_url, title, status, progress, "
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                token,
                owner_subject,
                "https://example.com/seed",
                None,
                status,
                0,
                "2026-05-31 00:00:00+00:00",
                "2026-05-31 00:00:00+00:00",
            ),
        )
        con.commit()
        row = con.execute("SELECT id FROM jobs WHERE job_token=?", (token,)).fetchone()
        return int(row[0]), token
    finally:
        con.close()


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
    create = client.post(
        "/jobs",
        json={"url": "https://x"},
        headers={"Authorization": f"Bearer {_clerk_jwt('alice')}"},
    )
    assert create.status_code == 201, create.text
    job = create.json()

    # Alice can read it.
    own = client.get(
        f"/jobs/{job['id']}/status",
        headers={"Authorization": f"Bearer {_clerk_jwt('alice')}"},
    )
    assert own.status_code == 200, own.text

    # Bob cannot — gets 404 (not 403, to hide existence).
    other = client.get(
        f"/jobs/{job['id']}/status",
        headers={"Authorization": f"Bearer {_clerk_jwt('bob')}"},
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
    # Scribe-styled result page (not the bare 1996 template).
    assert 'class="player"' in body
    assert 'class="chip' in body


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


def test_me_returns_admin_for_trusted_lan(client):
    """Default conftest is trusted-LAN -> admin identity."""
    r = client.get("/me")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_admin"] is True
    assert body["state"] in {"trusted_lan", "machine_bearer"}
    assert body["subject"]


def test_list_jobs_returns_created_jobs(client):
    """A freshly created job shows up in the owner's job list."""
    create = client.post("/jobs", json={"url": "https://example.com/a", "title": "ListMe"})
    assert create.status_code == 201
    token = create.json()["job_token"]

    r = client.get("/jobs")
    assert r.status_code == 200, r.text
    jobs = r.json()
    assert isinstance(jobs, list)
    assert any(j["job_token"] == token for j in jobs)
    # newest-first ordering: our just-created job is at or near the top.
    assert jobs[0]["job_token"] == token or any(
        j["job_token"] == token for j in jobs[:5]
    )


def test_list_jobs_respects_limit(client):
    for i in range(3):
        client.post("/jobs", json={"url": f"https://example.com/{i}"})
    r = client.get("/jobs", params={"limit": 2})
    assert r.status_code == 200
    assert len(r.json()) <= 2


def test_delete_job_removes_it(client):
    """LAN-admin can delete a job; it then 404s and drops out of the list."""
    job_id, token = _seed_job(JobStatus.completed.value)

    r = client.delete(f"/jobs/{job_id}")
    assert r.status_code == 204, r.text

    assert client.get(f"/jobs/{job_id}/status").status_code == 404
    assert not any(j["job_token"] == token for j in client.get("/jobs").json())


def test_delete_job_404_for_missing(client):
    assert client.delete("/jobs/999999").status_code == 404


def test_delete_job_owner_isolation(client):
    """Bob cannot delete Alice's job (404 to hide existence); it survives."""
    create = client.post(
        "/jobs",
        json={"url": "https://x"},
        headers={"Authorization": f"Bearer {_clerk_jwt('alice')}"},
    )
    job_id = create.json()["id"]

    bob = client.delete(
        f"/jobs/{job_id}", headers={"Authorization": f"Bearer {_clerk_jwt('bob')}"}
    )
    assert bob.status_code == 404

    alice = client.get(
        f"/jobs/{job_id}/status",
        headers={"Authorization": f"Bearer {_clerk_jwt('alice')}"},
    )
    assert alice.status_code == 200


def test_cancel_nonterminal_job(client):
    """Cancelling a queued job flips it to cancelled."""
    job_id, _ = _seed_job(JobStatus.queued.value)

    r = client.post(f"/jobs/{job_id}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == JobStatus.cancelled.value

    assert client.get(f"/jobs/{job_id}/status").json()["status"] == JobStatus.cancelled.value


def test_cancel_terminal_job_conflicts(client):
    """A completed/failed job cannot be cancelled (409)."""
    job_id, _ = _seed_job(JobStatus.completed.value)
    r = client.post(f"/jobs/{job_id}/cancel")
    assert r.status_code == 409, r.text


def test_cancel_job_owner_isolation(client):
    job_id, _ = _seed_job(JobStatus.queued.value, owner_subject="alice")
    bob = client.post(
        f"/jobs/{job_id}/cancel",
        headers={"Authorization": f"Bearer {_clerk_jwt('bob')}"},
    )
    assert bob.status_code == 404


def test_clear_failed_deletes_only_failed(client):
    """clear-failed removes failed jobs and leaves others untouched."""
    f1, t1 = _seed_job(JobStatus.failed.value)
    f2, t2 = _seed_job(JobStatus.failed.value)
    ok_id, ok_token = _seed_job(JobStatus.completed.value)

    r = client.post("/jobs/clear-failed")
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 2

    tokens = {j["job_token"] for j in client.get("/jobs").json()}
    assert t1 not in tokens and t2 not in tokens
    assert ok_token in tokens


def test_clear_failed_is_owner_scoped(client):
    """A clerk user only clears their own failed jobs, not everyone's."""
    _alice_id, alice_token = _seed_job(JobStatus.failed.value, owner_subject="alice")

    r = client.post(
        "/jobs/clear-failed", headers={"Authorization": f"Bearer {_clerk_jwt('bob')}"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["deleted"] == 0  # bob has no failed jobs

    # alice's failed job is still present (LAN-admin can see it).
    assert any(j["job_token"] == alice_token for j in client.get("/jobs").json())
