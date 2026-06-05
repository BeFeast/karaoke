"""Tests for the multi-layer auth dependency."""
from __future__ import annotations

import base64
import dataclasses
import datetime as dt
import hashlib
import json
import os
import sqlite3
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from karaoke.api.auth import (
    AuthState,
    Owner,
    new_extension_token,
    token_hash,
)
from karaoke.config import reset_settings_for_tests


def _unverified_jwt(claims: dict) -> str:
    """Build an RS256-shaped JWT whose payload is the given claims (test-mode only)."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(b"sig").rstrip(b"=").decode()
    return f"{header}.{body}.{sig}"


def _signed_clerk_jwt(claims: dict, *, issuer: str = "https://clerk.test") -> tuple[str, str]:
    """Return (token, jwks_json) for a locally signed Clerk-style JWT."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    kid = "test-kid"
    token = jwt.encode(
        {"iss": issuer, **claims},
        key,
        algorithm="RS256",
        headers={"kid": kid},
    )
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = kid
    return token, json.dumps({"keys": [jwk]})


def _sqlite_path() -> str:
    """Translate KARAOKE_DATABASE_URL into a stdlib sqlite3 path."""
    raw = os.environ["KARAOKE_DATABASE_URL"]
    return raw.replace("sqlite+aiosqlite:///", "")


def _seed_extension_token(
    raw: str,
    *,
    owner_subject: str,
    owner_email: str | None = None,
    owner_display_name: str | None = None,
    disabled: bool = False,
) -> None:
    """Insert an ExtensionToken row directly via stdlib sqlite3."""
    conn = sqlite3.connect(_sqlite_path())
    try:
        conn.execute(
            """
            INSERT INTO extension_tokens
                (token_hash, owner_subject, owner_email, owner_display_name,
                 label, disabled, created_at, last_used_at)
            VALUES (?, ?, ?, ?, NULL, ?, ?, NULL)
            """,
            (
                token_hash(raw),
                owner_subject,
                owner_email,
                owner_display_name,
                1 if disabled else 0,
                dt.datetime.now(dt.UTC).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Trusted-LAN / machine-bearer
# ---------------------------------------------------------------------------


def test_anonymous_post_jobs_is_rejected_without_lan_trust(monkeypatch, client):
    monkeypatch.setenv("KARAOKE_TRUSTED_CIDRS", "")
    reset_settings_for_tests()
    response = client.post("/jobs", json={"url": "https://x"})
    assert response.status_code == 401


def test_trusted_lan_caller_can_create_job(client):
    # conftest grants testclient LAN trust.
    response = client.post("/jobs", json={"url": "https://example.com/song"})
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["owner_subject"] == "lan-default"
    assert payload["share_url"].startswith("http://test.local/share/")


def test_machine_bearer_token_is_accepted(client):
    response = client.post(
        "/jobs",
        json={"url": "https://example.com/song"},
        headers={"Authorization": "Bearer test-service-token"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["owner_subject"] == "lan-default"


def test_invalid_authorization_header_format_is_rejected(client):
    response = client.post(
        "/jobs",
        json={"url": "https://example.com/song"},
        headers={"Authorization": "NotBearer foo"},
    )
    assert response.status_code == 401


def test_unrecognised_bearer_token_is_rejected(client):
    response = client.post(
        "/jobs",
        json={"url": "https://example.com/song"},
        headers={"Authorization": "Bearer totally-bogus"},
    )
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Clerk JWT (test-mode escape hatch)
# ---------------------------------------------------------------------------


def test_clerk_jwt_in_test_mode_is_accepted(client):
    token = _unverified_jwt(
        {
            "sub": "user_clerk_abc",
            "email": "alice@example.com",
            "name": "Alice",
            "exp": int(time.time()) + 3600,
        }
    )
    response = client.post(
        "/jobs",
        json={"url": "https://example.com/song"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["owner_subject"] == "user_clerk_abc"


def test_clerk_jwt_is_verified_with_mocked_jwks(monkeypatch, client):
    issuer = "https://clerk.test"
    token, jwks = _signed_clerk_jwt(
        {
            "sub": "user_clerk_verified",
            "email": "verified@example.com",
            "name": "Verified User",
            "exp": int(time.time()) + 3600,
        },
        issuer=issuer,
    )
    monkeypatch.setenv("KARAOKE_AUTH_TEST_MODE", "false")
    monkeypatch.setenv("KARAOKE_CLERK_ISSUER", issuer)
    monkeypatch.setenv("KARAOKE_CLERK_JWKS_JSON", jwks)
    reset_settings_for_tests()

    response = client.post(
        "/jobs",
        json={"url": "https://example.com/song"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["owner_subject"] == "user_clerk_verified"


# ---------------------------------------------------------------------------
# Extension tokens
# ---------------------------------------------------------------------------


def test_extension_token_is_accepted(client):
    raw = new_extension_token()
    assert raw.startswith("ktx_")
    _seed_extension_token(
        raw,
        owner_subject="user_ext_42",
        owner_email="ext@example.com",
        owner_display_name="Ext User",
    )

    response = client.post(
        "/jobs",
        json={"url": "https://example.com/song"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["owner_subject"] == "user_ext_42"


def test_disabled_extension_token_is_rejected(client):
    raw = new_extension_token()
    _seed_extension_token(raw, owner_subject="user_ext_disabled", disabled=True)

    response = client.post(
        "/jobs",
        json={"url": "https://x"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert response.status_code == 401


def test_clerk_user_can_create_and_revoke_extension_token(client):
    clerk = _unverified_jwt(
        {
            "sub": "user_clerk_tokens",
            "email": "tokens@example.com",
            "exp": int(time.time()) + 3600,
        }
    )
    auth = {"Authorization": f"Bearer {clerk}"}

    created = client.post(
        "/settings/extension-tokens",
        json={"label": "Chrome"},
        headers=auth,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    raw = body["token"]
    assert raw.startswith("ktx_")

    submitted = client.post(
        "/jobs",
        json={"url": "https://example.com/from-extension"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert submitted.status_code == 201, submitted.text
    assert submitted.json()["owner_subject"] == "user_clerk_tokens"

    revoked = client.delete(f"/settings/extension-tokens/{body['id']}", headers=auth)
    assert revoked.status_code == 204, revoked.text

    rejected = client.post(
        "/jobs",
        json={"url": "https://example.com/revoked"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert rejected.status_code == 401


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_token_hash_matches_sha256():
    raw = "ktx_example"
    expected = hashlib.sha256(raw.encode()).hexdigest()
    assert token_hash(raw) == expected


def test_owner_dataclass_is_frozen():
    owner = Owner(subject="u1", state=AuthState.clerk_user)
    with pytest.raises(dataclasses.FrozenInstanceError):
        owner.subject = "u2"  # type: ignore[misc]
