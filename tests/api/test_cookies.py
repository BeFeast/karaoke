"""Tests for the YouTube cookie-rotation endpoint (issue #73)."""
from __future__ import annotations

import base64
import datetime as dt
import json
import os
import sqlite3
import time
from collections.abc import Iterator

import pytest

from karaoke.api.auth import new_extension_token, token_hash
from karaoke.config import Settings, get_settings

TAB = "\t"
_HEADER = "# Netscape HTTP Cookie File"
_SECRET_VALUE = "super-secret-sid-value"


def _valid_blob() -> str:
    """A minimal but well-formed logged-in YouTube jar."""
    lines = [
        _HEADER,
        TAB.join([".youtube.com", "TRUE", "/", "TRUE", "2147483647", "SID", _SECRET_VALUE]),
        "#HttpOnly_.youtube.com"
        + TAB
        + TAB.join(["TRUE", "/", "TRUE", "2147483647", "HSID", "another-secret"]),
        TAB.join([".google.com", "TRUE", "/", "TRUE", "2147483647", "SAPISID", "g-secret"]),
    ]
    return "\n".join(lines) + "\n"


def _sqlite_path() -> str:
    return os.environ["KARAOKE_DATABASE_URL"].replace("sqlite+aiosqlite:///", "")


def _seed_extension_token(raw: str, *, owner_subject: str) -> None:
    conn = sqlite3.connect(_sqlite_path())
    try:
        conn.execute(
            """
            INSERT INTO extension_tokens
                (token_hash, owner_subject, owner_email, owner_display_name,
                 label, disabled, created_at, last_used_at)
            VALUES (?, ?, NULL, NULL, NULL, 0, ?, NULL)
            """,
            (token_hash(raw), owner_subject, dt.datetime.now(dt.UTC).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def _clerk_jwt(sub: str) -> str:
    """Forge a Clerk-test-mode JWT (signature unchecked under AUTH_TEST_MODE)."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    body = (
        base64.urlsafe_b64encode(
            json.dumps({"sub": sub, "email": f"{sub}@e.com", "exp": int(time.time()) + 600}).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


@pytest.fixture
def cookie_path(client, tmp_path) -> Iterator:
    """Point ``ytdlp_cookies_file`` at a writable tmp path for the app."""
    path = tmp_path / "cookies" / "youtube-cookies.txt"

    def override() -> Settings:
        return Settings(ytdlp_cookies_file=str(path))

    client.app.dependency_overrides[get_settings] = override
    try:
        yield path
    finally:
        client.app.dependency_overrides.pop(get_settings, None)


def _post(client, blob, **headers):
    return client.post(
        "/cookies/youtube",
        content=blob,
        headers={"Content-Type": "text/plain", **headers},
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_machine_bearer_can_upload(client, cookie_path):
    resp = _post(client, _valid_blob(), Authorization="Bearer test-service-token")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] is True
    assert body["cookies"] == 3
    assert body["youtube_cookies"] == 2
    assert body["last_good_kept"] is False
    assert cookie_path.is_file()
    assert oct(cookie_path.stat().st_mode)[-3:] == "600"


def test_extension_token_can_upload(client, cookie_path):
    raw = new_extension_token()
    _seed_extension_token(raw, owner_subject="user_ext_cookie")
    resp = _post(client, _valid_blob(), Authorization=f"Bearer {raw}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["youtube_cookies"] == 2


def test_trusted_lan_anonymous_is_rejected(client, cookie_path):
    # TestClient is trusted-LAN by conftest, but cookie upload requires the
    # extension token or machine bearer — LAN-anon is not enough.
    resp = _post(client, _valid_blob())
    assert resp.status_code == 403


def test_clerk_user_is_rejected(client, cookie_path):
    resp = _post(client, _valid_blob(), Authorization=f"Bearer {_clerk_jwt('user_clerk_x')}")
    assert resp.status_code == 403


def test_no_auth_without_lan_trust_is_401(client, tmp_path):
    path = tmp_path / "cookies" / "youtube-cookies.txt"

    def override() -> Settings:
        return Settings(trusted_cidrs="", ytdlp_cookies_file=str(path))

    client.app.dependency_overrides[get_settings] = override
    try:
        resp = _post(client, _valid_blob())
    finally:
        client.app.dependency_overrides.pop(get_settings, None)
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_invalid_format_is_rejected_and_keeps_last_known_good(client, cookie_path):
    # Seed a good jar first.
    assert _post(client, _valid_blob(), Authorization="Bearer test-service-token").status_code == 200
    good = cookie_path.read_text()

    bad = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tonly\tfour\n"
    resp = _post(client, bad, Authorization="Bearer test-service-token")
    assert resp.status_code == 422
    # The canonical jar is untouched — last-known-good preserved.
    assert cookie_path.read_text() == good
    # The error never echoes cookie field contents.
    assert "only" not in resp.text and "four" not in resp.text


def test_blob_without_youtube_cookie_is_rejected(client, cookie_path):
    blob = (
        "# Netscape HTTP Cookie File\n"
        + TAB.join([".example.com", "TRUE", "/", "FALSE", "0", "x", "y"])
        + "\n"
    )
    resp = _post(client, blob, Authorization="Bearer test-service-token")
    assert resp.status_code == 422
    assert "youtube" in resp.json()["detail"]
    assert not cookie_path.exists()


def test_empty_payload_is_rejected(client, cookie_path):
    resp = _post(client, "   \n", Authorization="Bearer test-service-token")
    assert resp.status_code == 422


def test_oversized_payload_is_rejected(client, cookie_path):
    resp = _post(client, "x" * (1024 * 1024 + 1), Authorization="Bearer test-service-token")
    assert resp.status_code == 413


def test_unconfigured_store_returns_503(client):
    def override() -> Settings:
        return Settings(ytdlp_cookies_file="")

    client.app.dependency_overrides[get_settings] = override
    try:
        resp = _post(client, _valid_blob(), Authorization="Bearer test-service-token")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Atomic write / last-known-good / no value leakage
# ---------------------------------------------------------------------------


def test_second_upload_keeps_previous_as_last_known_good(client, cookie_path):
    first = _valid_blob()
    assert _post(client, first, Authorization="Bearer test-service-token").status_code == 200

    second = first.replace(_SECRET_VALUE, "rotated-sid-value")
    resp = _post(client, second, Authorization="Bearer test-service-token")
    assert resp.status_code == 200, resp.text
    assert resp.json()["last_good_kept"] is True

    previous = cookie_path.with_name(cookie_path.name + ".previous")
    assert previous.is_file()
    assert _SECRET_VALUE in previous.read_text()  # the prior jar
    assert "rotated-sid-value" in cookie_path.read_text()  # the new canonical jar


def test_response_never_echoes_cookie_values(client, cookie_path):
    resp = _post(client, _valid_blob(), Authorization="Bearer test-service-token")
    assert resp.status_code == 200
    assert _SECRET_VALUE not in resp.text


# ---------------------------------------------------------------------------
# Status endpoint
# ---------------------------------------------------------------------------


def test_status_reports_presence(client, cookie_path):
    before = client.get("/cookies/youtube", headers={"Authorization": "Bearer test-service-token"})
    assert before.status_code == 200
    assert before.json() == {
        "configured": True,
        "present": False,
        "bytes": None,
        "modified_at": None,
        "last_good_present": False,
    }

    _post(client, _valid_blob(), Authorization="Bearer test-service-token")
    after = client.get("/cookies/youtube", headers={"Authorization": "Bearer test-service-token"})
    body = after.json()
    assert body["configured"] is True
    assert body["present"] is True
    assert body["bytes"] > 0
    assert body["modified_at"]


def test_status_requires_cookie_writer(client, cookie_path):
    # LAN-anon cannot read jar metadata either.
    assert client.get("/cookies/youtube").status_code == 403
