"""Tests for the public ``GET /config`` endpoint used by the Submitter SPA."""
from __future__ import annotations

import os

from karaoke.config import Settings, get_settings, reset_settings_for_tests


def test_config_default_has_clerk_disabled(client):
    """With no publishable key configured, ``clerk_enabled`` is False."""
    response = client.get("/config")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body.keys()) == {
        "clerk_publishable_key",
        "clerk_enabled",
        "public_base_url",
    }
    # conftest sets no KARAOKE_CLERK_PUBLISHABLE_KEY -> empty -> disabled.
    assert body["clerk_publishable_key"] == ""
    assert body["clerk_enabled"] is False
    # conftest pins the public base URL.
    assert body["public_base_url"] == "http://test.local"


def test_config_reports_clerk_enabled_when_key_set(client):
    """A configured publishable key flips ``clerk_enabled`` to True."""

    def override() -> Settings:
        return Settings(
            clerk_publishable_key="pk_test_abc123",
            clerk_spa_enabled=True,
            public_base_url="https://karaoke.example",
        )

    client.app.dependency_overrides[get_settings] = override
    try:
        response = client.get("/config")
    finally:
        client.app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["clerk_publishable_key"] == "pk_test_abc123"
    assert body["clerk_enabled"] is True
    assert body["public_base_url"] == "https://karaoke.example"


def test_config_is_public_no_auth(client):
    """``/config`` must be reachable without any auth header (meta tag)."""
    response = client.get("/config")
    assert response.status_code == 200


def test_config_key_set_but_gate_off_stays_disabled(client):
    """A publishable key present but clerk_spa_enabled False -> LAN mode."""
    from karaoke.config import Settings, get_settings

    def override() -> Settings:
        return Settings(
            clerk_publishable_key="pk_test_xyz",
            clerk_spa_enabled=False,
            public_base_url="https://karaoke.example",
        )

    client.app.dependency_overrides[get_settings] = override
    try:
        body = client.get("/config").json()
    finally:
        client.app.dependency_overrides.pop(get_settings, None)
    assert body["clerk_publishable_key"] == "pk_test_xyz"
    assert body["clerk_enabled"] is False


def test_pot_provider_base_url_default_and_env_override():
    """The bgutil PO-token provider base URL defaults to the documented sidecar
    and is overridable via ``KARAOKE_POT_PROVIDER_BASE_URL`` (issue #68)."""
    assert Settings().pot_provider_base_url == "http://karaoke-pot:4416"

    os.environ["KARAOKE_POT_PROVIDER_BASE_URL"] = "http://other-host:9999"
    reset_settings_for_tests()
    try:
        assert get_settings().pot_provider_base_url == "http://other-host:9999"
    finally:
        os.environ.pop("KARAOKE_POT_PROVIDER_BASE_URL", None)
        reset_settings_for_tests()
