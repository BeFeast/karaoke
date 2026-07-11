"""Smoke tests for the karaoke HTTP routes."""
from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime
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
    import karaoke

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": karaoke.__version__}


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


def _seed_job_with_metadata(
    *,
    artist: str,
    track: str,
    album: str | None = None,
    duration: int | None = None,
    gpu_instance_id: str | None = None,
    gpu_cost_micros: int | None = None,
) -> tuple[int, str]:
    """Insert a job row carrying the new source-metadata + GPU columns.

    The GPU kwargs map onto the legacy ``vast_*`` DB columns (JobOut exposes
    them under runtime-neutral ``gpu_*`` names).
    """
    db_path = os.environ["KARAOKE_DATABASE_URL"].split("///", 1)[1]
    token = secrets.token_urlsafe(16)
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA busy_timeout=3000")
        con.execute(
            "INSERT INTO jobs "
            "(job_token, owner_subject, source_url, title, artist, track, album, "
            " duration, vast_instance_id, vast_cost_micros, status, progress, "
            " created_at, updated_at, completed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                token,
                "lan-default",
                "https://example.com/seed",
                f"{artist} - {track}",
                artist,
                track,
                album,
                duration,
                gpu_instance_id,
                gpu_cost_micros,
                JobStatus.completed.value,
                100,
                "2026-06-01 00:00:00+00:00",
                "2026-06-01 00:00:00+00:00",
                "2026-06-01 00:05:00+00:00",
            ),
        )
        con.commit()
        row = con.execute("SELECT id FROM jobs WHERE job_token=?", (token,)).fetchone()
        return int(row[0]), token
    finally:
        con.close()


def test_jobout_exposes_artist_and_track(client):
    """``JobOut`` (via /jobs/{id}/status and /jobs) surfaces artist/track."""
    job_id, _ = _seed_job_with_metadata(artist="Daft Punk", track="Get Lucky")

    status_resp = client.get(f"/jobs/{job_id}/status")
    assert status_resp.status_code == 200, status_resp.text
    body = status_resp.json()
    assert body["artist"] == "Daft Punk"
    assert body["track"] == "Get Lucky"

    list_resp = client.get("/jobs")
    assert list_resp.status_code == 200, list_resp.text
    match = next(j for j in list_resp.json() if j["id"] == job_id)
    assert match["artist"] == "Daft Punk"
    assert match["track"] == "Get Lucky"


def test_share_payload_exposes_artist_and_track(client):
    """The JSON ``SharePayload`` surfaces artist/track too."""
    _, token = _seed_job_with_metadata(artist="Queen", track="Bohemian Rhapsody")

    share = client.get(f"/share/{token}", headers={"Accept": "application/json"})
    assert share.status_code == 200, share.text
    body = share.json()
    assert body["artist"] == "Queen"
    assert body["track"] == "Bohemian Rhapsody"


def test_jobout_exposes_seeded_metadata_and_gpu_cost(client):
    """Seeded album/duration/GPU columns surface via /jobs/{id}/status and /jobs."""
    job_id, _ = _seed_job_with_metadata(
        artist="ABBA",
        track="SOS",
        album="ABBA",
        duration=202,
        gpu_instance_id="runpod-abc123",
        gpu_cost_micros=43210,
    )

    status_resp = client.get(f"/jobs/{job_id}/status")
    assert status_resp.status_code == 200, status_resp.text
    body = status_resp.json()
    assert body["album"] == "ABBA"
    assert body["duration"] == 202
    assert body["gpu_instance_id"] == "runpod-abc123"
    assert body["gpu_cost_micros"] == 43210
    # ISO-8601 parseable; SQLite returns naive datetimes, so no offset assert.
    datetime.fromisoformat(body["created_at"])

    list_resp = client.get("/jobs")
    assert list_resp.status_code == 200, list_resp.text
    match = next(j for j in list_resp.json() if j["id"] == job_id)
    assert match["album"] == "ABBA"
    assert match["duration"] == 202
    assert match["gpu_instance_id"] == "runpod-abc123"
    assert match["gpu_cost_micros"] == 43210
    datetime.fromisoformat(match["created_at"])


def test_jobout_null_metadata_for_fresh_job(client):
    """A queued job has nulls for the optional metadata, but a created_at."""
    job_id, _ = _seed_job(JobStatus.queued.value)

    status_resp = client.get(f"/jobs/{job_id}/status")
    assert status_resp.status_code == 200, status_resp.text
    body = status_resp.json()
    assert body["album"] is None
    assert body["duration"] is None
    assert body["gpu_instance_id"] is None
    assert body["gpu_cost_micros"] is None
    assert body["completed_at"] is None
    datetime.fromisoformat(body["created_at"])

    list_resp = client.get("/jobs")
    assert list_resp.status_code == 200, list_resp.text
    match = next(j for j in list_resp.json() if j["id"] == job_id)
    assert match["album"] is None
    assert match["duration"] is None
    assert match["gpu_instance_id"] is None
    assert match["gpu_cost_micros"] is None
    assert match["completed_at"] is None
    datetime.fromisoformat(match["created_at"])


def test_jobout_exposes_gpu_fields_after_mock_completion(client):
    """The mock worker's GPU bookkeeping + completed_at surface in JobOut."""
    create = client.post("/jobs", json={"url": "https://example.com/song"})
    job_id = create.json()["id"]

    deadline = time.monotonic() + 5.0
    body: dict | None = None
    while time.monotonic() < deadline:
        body = client.get(f"/jobs/{job_id}/status").json()
        if body["status"] == JobStatus.completed.value:
            break
        time.sleep(0.05)
    assert body is not None and body["status"] == JobStatus.completed.value, body

    assert body["gpu_instance_id"] is not None
    assert body["gpu_instance_id"].startswith("mock-")
    # The mock spends $0 — assert explicitly, 0 is falsy.
    assert body["gpu_cost_micros"] is not None
    assert body["gpu_cost_micros"] == 0
    assert body["completed_at"] is not None
    datetime.fromisoformat(body["created_at"])
    datetime.fromisoformat(body["completed_at"])


def test_share_payload_exposes_album_duration_but_no_private_keys(client):
    """SharePayload carries display metadata — never cost, identity, or created_at.

    The Stage page (#154) reads ``source_url`` + ``completed_at`` for its meta
    row, so those are public; GPU cost/receipt and the owner subject must not
    leak through an unlisted share link.
    """
    _, token = _seed_job_with_metadata(
        artist="Queen",
        track="Bohemian Rhapsody",
        album="A Night at the Opera",
        duration=355,
        gpu_instance_id="runpod-xyz789",
        gpu_cost_micros=99999,
    )

    share = client.get(f"/share/{token}", headers={"Accept": "application/json"})
    assert share.status_code == 200, share.text
    body = share.json()
    assert body["album"] == "A Night at the Opera"
    assert body["duration"] == 355
    assert body["source_url"] == "https://example.com/seed"
    assert body["completed_at"] is not None
    datetime.fromisoformat(body["completed_at"])
    # The share token is an unlisted public link — cost/identity must not leak.
    for private_key in (
        "gpu_instance_id",
        "gpu_cost_micros",
        "owner_subject",
        "created_at",
    ):
        assert private_key not in body


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


def test_share_artifact_serves_lyrics_lrc(client, tmp_path):
    from karaoke.config import Settings, get_settings

    create = client.post("/jobs", json={"url": "https://example.com/x"})
    token = create.json()["job_token"]

    exports = Path(tmp_path) / token / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "lyrics.lrc").write_text("[00:12.00]line one\n", encoding="utf-8")

    def fake_settings() -> Settings:
        return Settings(artifact_root=str(tmp_path))

    client.app.dependency_overrides[get_settings] = fake_settings
    try:
        r = client.get(f"/share/{token}/lyrics.lrc")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)

    assert r.status_code == 200, r.text
    assert r.headers.get("content-type", "").startswith("text/plain")
    assert "[00:12.00]" in r.text


def _insert_artifact(
    job_id: int, *, kind: str, relative_path: str, size_bytes: int | None
) -> None:
    """Insert an Artifact row directly (the mock worker also creates these)."""
    db_path = os.environ["KARAOKE_DATABASE_URL"].split("///", 1)[1]
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA busy_timeout=3000")
        con.execute(
            "INSERT INTO artifacts "
            "(job_id, kind, relative_path, size_bytes, created_at) "
            "VALUES (?,?,?,?,?)",
            (job_id, kind, relative_path, size_bytes, "2026-06-01 00:00:00+00:00"),
        )
        con.commit()
    finally:
        con.close()


def test_jobout_exposes_artifacts(client):
    """``JobOut`` (via /jobs/{id}/status and /jobs) lists the job's artifacts."""
    job_id, token = _seed_job(JobStatus.completed.value)
    _insert_artifact(
        job_id,
        kind="karaoke",
        relative_path=f"{token}/exports/karaoke.mp3",
        size_bytes=12345,
    )
    _insert_artifact(
        job_id,
        kind="lyrics_lrc",
        relative_path=f"{token}/exports/lyrics.lrc",
        size_bytes=None,
    )

    status_resp = client.get(f"/jobs/{job_id}/status")
    assert status_resp.status_code == 200, status_resp.text
    arts = status_resp.json()["artifacts"]
    by_kind = {a["kind"]: a for a in arts}
    assert by_kind["karaoke"]["name"] == "karaoke.mp3"
    assert by_kind["karaoke"]["size"] == 12345
    assert by_kind["lyrics_lrc"]["name"] == "lyrics.lrc"
    assert by_kind["lyrics_lrc"]["size"] is None

    list_resp = client.get("/jobs")
    assert list_resp.status_code == 200, list_resp.text
    match = next(j for j in list_resp.json() if j["id"] == job_id)
    assert {a["kind"] for a in match["artifacts"]} == {"karaoke", "lyrics_lrc"}


def test_create_job_has_empty_artifacts(client):
    """A freshly created job projects an empty (not missing) artifacts list."""
    resp = client.post("/jobs", json={"url": "https://example.com/song"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["artifacts"] == []


def _override_artifact_root(client, tmp_path):
    from karaoke.config import Settings, get_settings

    def fake_settings() -> Settings:
        return Settings(artifact_root=str(tmp_path))

    client.app.dependency_overrides[get_settings] = fake_settings


def test_share_lyrics_synced_parses_lines(client, tmp_path):
    from karaoke.config import get_settings

    _, token = _seed_job_with_metadata(artist="A", track="B")
    exports = Path(tmp_path) / token / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "lyrics.lrc").write_text(
        "[ar:Some Artist]\n"
        "[00:12.50]first line\n"
        "[01:05.00]second line\n"
        "[00:30.00]\n",  # blank-text timed line -> dropped
        encoding="utf-8",
    )
    (exports / "lyrics.txt").write_text("first line\nsecond line\n", encoding="utf-8")
    (exports / "metadata.json").write_text(
        '{"lyrics_source": "lrclib_synced", "synced": true, "instrumental": false}',
        encoding="utf-8",
    )

    _override_artifact_root(client, tmp_path)
    try:
        r = client.get(f"/share/{token}/lyrics")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["synced"] is True
    assert body["source"] == "lrclib_synced"
    assert body["lrc"].startswith("[ar:Some Artist]")
    assert body["plain"] == "first line\nsecond line\n"
    # Parsed, sorted, blank-text line dropped.
    assert body["lines"] == [
        {"t": 12.5, "text": "first line"},
        {"t": 65.0, "text": "second line"},
    ]


def test_share_lyrics_synced_without_txt_strips_timestamps(client, tmp_path):
    """No lyrics.txt → plain is derived from the LRC body."""
    from karaoke.config import get_settings

    _, token = _seed_job_with_metadata(artist="A", track="B")
    exports = Path(tmp_path) / token / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "lyrics.lrc").write_text(
        "[00:01.00]only line\n", encoding="utf-8"
    )

    _override_artifact_root(client, tmp_path)
    try:
        r = client.get(f"/share/{token}/lyrics")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["synced"] is True
    assert body["plain"] == "only line"
    # No metadata.json -> source inferred.
    assert body["source"] == "lrclib_synced"


def test_share_lyrics_plain_only(client, tmp_path):
    from karaoke.config import get_settings

    _, token = _seed_job_with_metadata(artist="A", track="B")
    exports = Path(tmp_path) / token / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "lyrics.txt").write_text("a plain transcript\n", encoding="utf-8")
    (exports / "metadata.json").write_text(
        '{"lyrics_source": "whisper_asr", "synced": false, "instrumental": false}',
        encoding="utf-8",
    )

    _override_artifact_root(client, tmp_path)
    try:
        r = client.get(f"/share/{token}/lyrics")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["synced"] is False
    assert body["lrc"] is None
    assert body["lines"] is None
    assert body["plain"] == "a plain transcript\n"
    assert body["source"] == "whisper_asr"


def test_share_lyrics_whisper_asr_synced(client, tmp_path):
    """ASR-floor tracks with a segment-timed LRC (#145) serve synced lyrics
    with the ``whisper_asr_synced`` provenance passed through verbatim."""
    from karaoke.config import get_settings

    _, token = _seed_job_with_metadata(artist="A", track="B")
    exports = Path(tmp_path) / token / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "lyrics.lrc").write_text(
        "[00:01.50]asr one\n[00:10.25]asr two\n", encoding="utf-8"
    )
    (exports / "lyrics.txt").write_text("asr one\nasr two\n", encoding="utf-8")
    (exports / "metadata.json").write_text(
        '{"lyrics_source": "whisper_asr_synced", "synced": true, "instrumental": false}',
        encoding="utf-8",
    )

    _override_artifact_root(client, tmp_path)
    try:
        r = client.get(f"/share/{token}/lyrics")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["synced"] is True
    assert body["source"] == "whisper_asr_synced"
    assert body["lines"] == [
        {"t": 1.5, "text": "asr one"},
        {"t": 10.25, "text": "asr two"},
    ]


def test_share_lyrics_instrumental(client, tmp_path):
    from karaoke.config import get_settings

    _, token = _seed_job_with_metadata(artist="A", track="B")
    exports = Path(tmp_path) / token / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "metadata.json").write_text(
        '{"lyrics_source": "instrumental", "synced": false, "instrumental": true}',
        encoding="utf-8",
    )

    _override_artifact_root(client, tmp_path)
    try:
        r = client.get(f"/share/{token}/lyrics")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["synced"] is False
    assert body["lrc"] is None
    assert body["lines"] is None
    assert body["plain"] is None
    assert body["source"] == "instrumental"


def test_share_lyrics_404_for_unknown_token(client):
    r = client.get("/share/does-not-exist/lyrics")
    assert r.status_code == 404


def test_share_lyrics_empty_when_no_artifacts(client, tmp_path):
    """Job with no lyrics files yet -> empty payload, source inferred 'none'."""
    from karaoke.config import get_settings

    _, token = _seed_job_with_metadata(artist="A", track="B")

    _override_artifact_root(client, tmp_path)
    try:
        r = client.get(f"/share/{token}/lyrics")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {
        "synced": False,
        "lrc": None,
        "lines": None,
        "plain": None,
        "source": "none",
    }


def _get_lyrics(client, tmp_path, lrc_body):
    """Seed a job with ``lrc_body`` as its ``lyrics.lrc`` and GET its lyrics."""
    from karaoke.config import get_settings

    _, token = _seed_job_with_metadata(artist="A", track="B")
    exports = Path(tmp_path) / token / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    (exports / "lyrics.lrc").write_text(lrc_body, encoding="utf-8")

    _override_artifact_root(client, tmp_path)
    try:
        r = client.get(f"/share/{token}/lyrics")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)
    assert r.status_code == 200, r.text
    return r.json()


def test_share_lyrics_enhanced_words_with_trailing_end(client, tmp_path):
    """Enhanced-LRC line -> clean text + per-word timing + trailing sung end.

    Last word's ``d`` comes from the trailing ``<..>`` end tag; ``text`` and
    ``plain`` carry no ``<>`` tags; ``lrc`` keeps the raw word tags verbatim.
    """
    lrc = "[00:00.00]<00:01.00>hello <00:03.00>world <00:05.00>\n"
    body = _get_lyrics(client, tmp_path, lrc)

    assert body["synced"] is True
    # Raw LRC (export surface) keeps the inline word tags.
    assert body["lrc"] == lrc
    # No <..> tags ever leak into text or plain.
    assert "<" not in body["plain"] and ">" not in body["plain"]
    assert body["plain"] == "hello world"
    assert body["lines"] == [
        {
            "t": 0.0,
            "text": "hello world",
            "end": 5.0,
            "words": [
                {"t": 1.0, "d": 2.0, "text": "hello"},
                {"t": 3.0, "d": 2.0, "text": "world"},
            ],
        }
    ]
    line = body["lines"][0]
    assert "<" not in line["text"] and ">" not in line["text"]


def test_share_lyrics_enhanced_words_without_trailing_end(client, tmp_path):
    """No trailing tag -> line ``end`` omitted and last word's ``d`` is null."""
    body = _get_lyrics(
        client, tmp_path, "[00:00.00]<00:01.00>hello <00:03.00>world\n"
    )
    assert body["lines"] == [
        {
            "t": 0.0,
            "text": "hello world",
            "words": [
                {"t": 1.0, "d": 2.0, "text": "hello"},
                {"t": 3.0, "d": None, "text": "world"},
            ],
        }
    ]
    # No trailing end tag -> the line carries no ``end`` key.
    assert "end" not in body["lines"][0]


def test_share_lyrics_mixed_plain_and_enhanced(client, tmp_path):
    """Mixed file: plain lines stay word-less; enhanced lines gain words."""
    lrc = (
        "[00:01.00]plain line\n"
        "[00:10.00]<00:11.00>sung <00:12.50>word <00:13.00>\n"
    )
    body = _get_lyrics(client, tmp_path, lrc)

    assert body["plain"] == "plain line\nsung word"
    assert body["lines"] == [
        {"t": 1.0, "text": "plain line"},
        {
            "t": 10.0,
            "text": "sung word",
            "end": 13.0,
            "words": [
                {"t": 11.0, "d": 1.5, "text": "sung"},
                {"t": 12.5, "d": 0.5, "text": "word"},
            ],
        },
    ]
    # Plain line keeps today's byte-identical shape (no new keys).
    assert body["lines"][0] == {"t": 1.0, "text": "plain line"}


def test_share_lyrics_malformed_word_tag_degrades_to_plain(client, tmp_path):
    """Text before the first word tag is malformed -> degrade to a plain line.

    The line still strips its ``<>`` tags (hard requirement) and never 500s.
    """
    body = _get_lyrics(
        client, tmp_path, "[00:01.00]hello <00:02.00>world\n"
    )
    assert body["lines"] == [{"t": 1.0, "text": "hello world"}]
    assert "<" not in body["plain"] and ">" not in body["plain"]
    assert body["plain"] == "hello world"


def test_share_lyrics_plain_lrc_backward_compatible(client, tmp_path):
    """Plain LRC -> ``words``/``end`` absent, payload identical to today."""
    body = _get_lyrics(
        client, tmp_path, "[00:12.50]first line\n[01:05.00]second line\n"
    )
    assert body["lines"] == [
        {"t": 12.5, "text": "first line"},
        {"t": 65.0, "text": "second line"},
    ]
    for line in body["lines"]:
        assert "words" not in line
        assert "end" not in line


def test_share_lyrics_multiple_line_tags_duplicate_words(client, tmp_path):
    """Several leading ``[..]`` tags expand to one entry each, same words."""
    lrc = "[00:01.00][00:20.00]<00:01.50>hi <00:02.00>there <00:03.00>\n"
    body = _get_lyrics(client, tmp_path, lrc)

    words = [
        {"t": 1.5, "d": 0.5, "text": "hi"},
        {"t": 2.0, "d": 1.0, "text": "there"},
    ]
    assert body["lines"] == [
        {"t": 1.0, "text": "hi there", "end": 3.0, "words": words},
        {"t": 20.0, "text": "hi there", "end": 3.0, "words": words},
    ]


def test_share_lyrics_word_schema_in_openapi(client):
    """Response schema exposes the new ``LyricsWord`` / word-timing fields."""
    schema = client.get("/openapi.json").json()["components"]["schemas"]
    assert "LyricsWord" in schema
    assert set(schema["LyricsWord"]["properties"]) == {"t", "d", "text"}
    line_props = schema["LyricsLine"]["properties"]
    assert "words" in line_props and "end" in line_props


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
