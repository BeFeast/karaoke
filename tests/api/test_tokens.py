"""Tests for the ``ktx_`` extension-token self-service API (issue #133)."""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sqlite3
import time

from karaoke.config import reset_settings_for_tests

MACHINE = {"Authorization": "Bearer test-service-token"}


def _unverified_jwt(claims: dict) -> str:
    """Build an RS256-shaped JWT whose payload is the given claims (test-mode only)."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


def _clerk_headers(subject: str = "user_clerk_a", email: str = "a@example.com") -> dict:
    token = _unverified_jwt(
        {"sub": subject, "email": email, "name": "Alice", "exp": int(time.time()) + 3600}
    )
    return {"Authorization": f"Bearer {token}"}


def _sqlite_path() -> str:
    """Translate KARAOKE_DATABASE_URL into a stdlib sqlite3 path."""
    raw = os.environ["KARAOKE_DATABASE_URL"]
    return raw.replace("sqlite+aiosqlite:///", "")


# ---------------------------------------------------------------------------
# Mint gate matrix
# ---------------------------------------------------------------------------


def test_mint_allowed_for_clerk_user(client):
    response = client.post("/tokens", json={"label": "My laptop"}, headers=_clerk_headers())
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"id", "token", "label", "created_at"}
    assert body["token"].startswith("ktx_")
    assert body["label"] == "My laptop"


def test_mint_allowed_for_machine_bearer_with_default_label(client):
    # No body at all — label defaults.
    response = client.post("/tokens", headers=MACHINE)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["token"].startswith("ktx_")
    assert body["label"] == "Chrome extension"


def test_mint_null_label_defaults(client):
    response = client.post("/tokens", json={"label": None}, headers=_clerk_headers())
    assert response.status_code == 201, response.text
    assert response.json()["label"] == "Chrome extension"


def test_mint_forbidden_for_trusted_lan(client):
    # conftest grants testclient LAN trust; no bearer → trusted_lan actor.
    response = client.post("/tokens", json={})
    assert response.status_code == 403


def test_mint_forbidden_for_extension_token_actor(client):
    # No token-begets-token: a freshly minted ktx_ actor cannot mint another.
    minted = client.post("/tokens", json={}, headers=_clerk_headers()).json()
    response = client.post(
        "/tokens", json={}, headers={"Authorization": f"Bearer {minted['token']}"}
    )
    assert response.status_code == 403


def test_mint_anonymous_is_401(monkeypatch, client):
    monkeypatch.setenv("KARAOKE_TRUSTED_CIDRS", "")
    reset_settings_for_tests()
    response = client.post("/tokens", json={})
    assert response.status_code == 401


def test_mint_label_over_255_chars_is_422(client):
    response = client.post("/tokens", json={"label": "x" * 256}, headers=_clerk_headers())
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Mint → use roundtrip + storage hygiene
# ---------------------------------------------------------------------------


def test_mint_use_roundtrip(client):
    minted = client.post(
        "/tokens", json={}, headers=_clerk_headers(subject="user_rt")
    ).json()
    response = client.get(
        "/me", headers={"Authorization": f"Bearer {minted['token']}"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "extension_token"
    assert body["subject"] == "user_rt"


def test_db_stores_sha256_hash_and_never_the_raw_token(client):
    minted = client.post("/tokens", json={}, headers=_clerk_headers()).json()
    raw = minted["token"]
    conn = sqlite3.connect(_sqlite_path())
    try:
        stored = conn.execute(
            "SELECT token_hash FROM extension_tokens WHERE id = ?", (minted["id"],)
        ).fetchone()[0]
        dump = "\n".join(conn.iterdump())
    finally:
        conn.close()
    assert stored == hashlib.sha256(raw.encode()).hexdigest()
    # The raw ktx_ value appears nowhere in the entire database.
    assert raw not in dump


def test_raw_token_is_never_logged_during_mint(client, caplog):
    with caplog.at_level(logging.DEBUG):
        minted = client.post("/tokens", json={}, headers=_clerk_headers()).json()
    raw = minted["token"]
    assert raw.startswith("ktx_")
    assert raw not in caplog.text
    for record in caplog.records:
        assert raw not in record.getMessage()


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def test_list_is_owner_scoped_and_admin_sees_all(client):
    a = client.post(
        "/tokens", json={"label": "A"}, headers=_clerk_headers(subject="user_a")
    ).json()
    b = client.post(
        "/tokens", json={"label": "B"}, headers=_clerk_headers(subject="user_b")
    ).json()

    listed = client.get("/tokens", headers=_clerk_headers(subject="user_a")).json()
    assert [t["id"] for t in listed] == [a["id"]]
    row = listed[0]
    # Exactly the documented fields — never token_hash, never the raw value.
    assert set(row) == {
        "id", "label", "disabled", "created_at", "last_used_at", "owner_subject",
    }
    assert row["owner_subject"] == "user_a"
    assert row["disabled"] is False
    assert row["label"] == "A"

    machine_rows = client.get("/tokens", headers=MACHINE).json()
    assert {t["id"] for t in machine_rows} >= {a["id"], b["id"]}

    lan_rows = client.get("/tokens").json()  # trusted_lan is admin like /jobs
    assert {t["id"] for t in lan_rows} >= {a["id"], b["id"]}


def test_list_anonymous_is_401(monkeypatch, client):
    monkeypatch.setenv("KARAOKE_TRUSTED_CIDRS", "")
    reset_settings_for_tests()
    assert client.get("/tokens").status_code == 401


def test_last_used_at_populated_after_use(client):
    headers = _clerk_headers(subject="user_lu")
    minted = client.post("/tokens", json={}, headers=headers).json()

    listed = client.get("/tokens", headers=headers).json()
    assert listed[0]["last_used_at"] is None

    assert (
        client.get(
            "/me", headers={"Authorization": f"Bearer {minted['token']}"}
        ).status_code
        == 200
    )

    listed = client.get("/tokens", headers=headers).json()
    assert listed[0]["last_used_at"] is not None


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------


def test_revoke_cross_owner_is_404(client):
    minted = client.post(
        "/tokens", json={}, headers=_clerk_headers(subject="user_a")
    ).json()
    response = client.delete(
        f"/tokens/{minted['id']}", headers=_clerk_headers(subject="user_b")
    )
    assert response.status_code == 404
    # Untouched: the token still works.
    assert (
        client.get(
            "/me", headers={"Authorization": f"Bearer {minted['token']}"}
        ).status_code
        == 200
    )


def test_revoke_unknown_id_is_404(client):
    assert client.delete("/tokens/99999", headers=MACHINE).status_code == 404


def test_revoke_disables_token_and_is_idempotent(client):
    headers = _clerk_headers(subject="user_a")
    minted = client.post("/tokens", json={}, headers=headers).json()

    response = client.delete(f"/tokens/{minted['id']}", headers=headers)
    assert response.status_code == 204

    # Soft revoke: the audit row survives, flagged disabled.
    listed = client.get("/tokens", headers=headers).json()
    assert [t["disabled"] for t in listed if t["id"] == minted["id"]] == [True]

    # The revoked token is rejected on its next use.
    response = client.get(
        "/me", headers={"Authorization": f"Bearer {minted['token']}"}
    )
    assert response.status_code == 401

    # Idempotent re-revoke.
    assert client.delete(f"/tokens/{minted['id']}", headers=headers).status_code == 204


def test_admin_machine_bearer_can_revoke_any(client):
    minted = client.post(
        "/tokens", json={}, headers=_clerk_headers(subject="user_a")
    ).json()
    assert client.delete(f"/tokens/{minted['id']}", headers=MACHINE).status_code == 204
    assert (
        client.get(
            "/me", headers={"Authorization": f"Bearer {minted['token']}"}
        ).status_code
        == 401
    )


# ---------------------------------------------------------------------------
# OpenAPI surface
# ---------------------------------------------------------------------------


def test_openapi_contains_tokens_paths(client):
    paths = client.app.openapi()["paths"]
    assert "post" in paths["/tokens"]
    assert "get" in paths["/tokens"]
    assert "delete" in paths["/tokens/{token_id}"]
