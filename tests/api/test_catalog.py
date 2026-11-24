"""Catalog access and owner-linked reuse through the production HTTP routes."""
from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from karaoke.catalog import youtube_id
from karaoke.catalog_identity import PILOT_OWNER
from karaoke.config import reset_settings_for_tests

from .test_auth import _sqlite_path, _unverified_jwt

VIDEO_ID = "1TAuiaxqJTY"
NOW = dt.datetime.now(dt.UTC).isoformat()


def clerk(subject: str) -> dict[str, str]:
    token = _unverified_jwt({"sub": subject, "exp": 4_000_000_000})
    return {"Authorization": f"Bearer {token}"}


def seed_catalog(export_root):
    with sqlite3.connect(_sqlite_path()) as db:
        db.execute(
            "INSERT INTO catalog_tracks (id, title, artist, aliases) VALUES (1, 'קומראן', 'Офек', '[\"Adanek\"]')"
        )
        db.execute(
            """INSERT INTO catalog_recordings
               (id, track_id, version, language, genres, genres_uncertain, collections, decade)
               VALUES (1, 1, 'studio', 'he', '["hip-hop"]', 1, '["Хиты на иврите"]', 2020)"""
        )
        db.execute(
            """INSERT INTO catalog_sources
               (id, recording_id, provider, external_id, provenance, available, metadata_allowed, playback_allowed)
               VALUES (1, 1, 'youtube', ?, '[{"playlist":"playlist1","rank":1}]', 1, 1, 1)""",
            (VIDEO_ID,),
        )
        job_id = db.execute(
            """INSERT INTO jobs (job_token, owner_subject, source_url, title, status,
               progress, created_at, updated_at, completed_at)
               VALUES ('pilot-storage', ?, ?, 'קומראן', 'completed', 100, ?, ?, ?) RETURNING id""",
            (PILOT_OWNER, f"https://youtu.be/{VIDEO_ID}", NOW, NOW, NOW),
        ).fetchone()[0]
        db.execute(
            """INSERT INTO catalog_assets
               (id, source_id, pipeline_version, job_id, state, download_started_at, reserved_micros)
               VALUES (1, 1, '1', ?, 'ready', ?, 100000)""", (job_id, NOW),
        )
        db.execute(
            """INSERT INTO artifacts (job_id, kind, relative_path, size_bytes, content_type, created_at)
               VALUES (?, 'karaoke', 'catalog/pilot-storage/exports/karaoke.mp3', 4, 'audio/mpeg', ?)""",
            (job_id, NOW),
        )
    exports = export_root / "catalog" / "pilot-storage" / "exports"
    exports.mkdir(parents=True)
    (exports / "karaoke.mp3").write_bytes(b"song")
    (exports / "lyrics.lrc").write_text("[00:00.00]first\n[00:01.00]second\n", encoding="utf-8")


@pytest.fixture
def catalog_exports(client, monkeypatch, tmp_path):
    exports = tmp_path / "artifacts"
    monkeypatch.setenv("KARAOKE_ARTIFACT_ROOT", str(exports))
    reset_settings_for_tests()
    seed_catalog(exports)


def test_multilingual_search_facets_and_protected_playback(client, catalog_exports):
    alice = clerk("user_alice")
    bob = clerk("user_bob")
    for headers in ({}, {"Authorization": "Bearer test-service-token"}):
        assert client.get("/catalog", headers=headers).status_code == 401
        assert client.get("/share/cat.1/lyrics", headers=headers).status_code == 401
        assert client.get("/share/cat.1/karaoke.mp3", headers=headers).status_code == 401

    for query in ("קוּמ", "ОФЕ", "DANE"):
        response = client.get("/catalog", params={"q": query}, headers=bob).json()
        assert [song["stage_token"] for song in response["songs"]] == ["cat.1"]
        assert response["facets"] == {
            "language": {"he": 1}, "genre": {"hip-hop": 1},
            "collection": {"Хиты на иврите": 1}, "decade": {"2020": 1},
        }
    assert client.get("/catalog", params={"language": "ru"}, headers=bob).json()["songs"] == []
    payload = client.get("/share/cat.1", headers={**bob, "Accept": "application/json"})
    assert payload.status_code == 200
    assert payload.json()["artifacts"][0]["relative_path"] == "karaoke.mp3"
    assert client.get("/share/cat.1", headers=bob).status_code == 404
    response = client.get("/share/cat.1/karaoke.mp3", headers={**bob, "Range": "bytes=1-2"})
    assert response.status_code == 206
    assert response.content == b"on"
    assert response.headers["cache-control"] == "private, no-store"
    assert client.get("/share/cat.1/lyrics", headers=alice).json()["synced"]
    assert client.get("/share/pilot-storage/karaoke.mp3", headers=alice).status_code == 404


def test_reuse_creates_only_owner_link_and_keeps_the_asset(client, catalog_exports, monkeypatch):
    alice, bob = clerk("user_alice"), clerk("user_bob")
    scheduled = []
    monkeypatch.setattr("karaoke.api.routes.schedule_job", lambda _factory, job_id, _settings: scheduled.append(job_id))

    first = client.post("/jobs", headers=alice, json={"url": f"https://youtu.be/{VIDEO_ID}?si=abc"}).json()
    second = client.post("/jobs", headers=bob, json={"url": f"https://music.youtube.com/watch?v={VIDEO_ID}"}).json()
    assert first["id"] != second["id"]
    assert first["status"] == second["status"] == "completed"
    assert first["share_url"] is None and first["gpu_instance_id"] is None
    assert not scheduled

    alias = f"/share/{first['job_token']}"
    assert client.get(f"{alias}/lyrics", headers=bob).status_code == 404
    assert client.get(f"{alias}/lyrics").status_code == 401
    assert client.get(f"{alias}/lyrics", headers=alice).json()["review_job_id"] is None
    assert client.delete(f"/jobs/{first['id']}", headers=alice).status_code == 204
    assert client.get(alias, headers=alice).status_code == 404
    assert [job["id"] for job in client.get("/jobs", headers=bob).json()] == [second["id"]]
    assert client.get("/share/cat.1/lyrics", headers=bob).status_code == 200

    unknown = client.post("/jobs", headers=alice, json={
        "url": "https://youtu.be/abcdefghijk", "title": "Outside catalog",
    }).json()
    assert unknown["status"] == "queued" and unknown["share_url"]
    assert scheduled == [unknown["id"]]
    assert client.get("/catalog?q=Outside", headers=alice).json()["songs"] == []
    with sqlite3.connect(_sqlite_path()) as db:
        db.execute("UPDATE catalog_sources SET playback_allowed = 0")
    assert client.get("/share/cat.1/karaoke.mp3", headers=bob).status_code == 404
    assert client.get("/catalog", headers=bob).json()["songs"][0]["stage_token"] is None


def test_url_normalization():
    assert youtube_id(f"https://www.youtube.com/watch?v={VIDEO_ID}&list=playlist") == VIDEO_ID
    assert youtube_id(f"https://music.youtube.com/watch?v={VIDEO_ID}") == VIDEO_ID
    assert youtube_id(f"https://youtu.be/{VIDEO_ID}#verify") == VIDEO_ID
    assert youtube_id(f"https://youtube.com.example.org/watch?v={VIDEO_ID}") is None
    assert youtube_id(f"https://youtube.com/watch?v={VIDEO_ID}&v=abcdefghijk") is None
