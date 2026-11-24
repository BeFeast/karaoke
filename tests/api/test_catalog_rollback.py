"""Exercise the compatibility guard through the app's real HTTP routes.

The same test is run against the previous release with the guard installed
by the companion's build step.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3

from karaoke.api.catalog_rollback import protect_legacy_api
from karaoke.db.session import get_session

from .test_auth import _sqlite_path, _unverified_jwt


def _headers(subject: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_unverified_jwt({'sub': subject, 'exp': 4_000_000_000})}"}


def test_rollback_does_not_expose_catalog_rows(client):
    if os.getenv("KARAOKE_TEST_PATCHED_ROLLBACK") == "1":
        assert get_session in client.app.dependency_overrides
    else:
        protect_legacy_api(client.app)
    owner = "user_catalog_owner"
    with sqlite3.connect(_sqlite_path()) as db:
        now = dt.datetime.now(dt.UTC).isoformat()
        db.executemany(
            """INSERT INTO jobs (id, job_token, owner_subject, owner_display_name, source_url,
               title, status, progress, created_at, updated_at, completed_at)
               VALUES (?, ?, ?, 'private name', 'https://youtu.be/9xg7L2qHZDU',
                       'test song', 'completed', 100, ?, ?, ?)""",
            [
                (1, "pilot-storage-token", "internal:catalog-pilot", now, now, now),
                (2, "clerk.owner-link", owner, now, now, now),
                (3, "private-job-token", owner, now, now, now),
            ],
        )

    admin = {"Authorization": "Bearer test-service-token"}
    for viewer in ({}, _headers("user_different_owner"), admin, _headers(owner)):
        for token in ("pilot-storage-token", "clerk.owner-link"):
            for suffix in ("", "/lyrics", "/metadata.json", "/vocals.mp3"):
                headers = {"Accept": "application/json", **viewer}
                assert client.get(f"/share/{token}{suffix}", headers=headers).status_code == 404

    for viewer in (admin, _headers(owner)):
        jobs = client.get("/jobs", headers=viewer)
        assert jobs.status_code == 200
        assert [job["id"] for job in jobs.json()] == [3]
        for job_id in (1, 2):
            assert client.get(f"/jobs/{job_id}/status", headers=viewer).status_code == 404
            assert client.delete(f"/jobs/{job_id}", headers=viewer).status_code == 404

    assert client.get("/share/private-job-token", headers={"Accept": "application/json"}).status_code == 200
    with sqlite3.connect(_sqlite_path()) as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone() == (3,)
