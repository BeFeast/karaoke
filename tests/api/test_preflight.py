"""``GET /preflight`` — offline yt-dlp extractor matching (issue #180)."""
from __future__ import annotations

import pytest

from karaoke.config import reset_settings_for_tests


def _preflight(client, url: str) -> dict:
    response = client.get("/preflight", params={"url": url})
    assert response.status_code == 200, response.text
    return response.json()


def test_youtube_watch_url_is_supported(client):
    payload = _preflight(client, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert payload["supported"] is True
    assert payload["generic_only"] is False
    # Don't pin yt-dlp's exact IE_NAME casing — just require a dedicated
    # (non-Generic) youtube extractor.
    assert payload["extractor"]
    assert payload["extractor"].lower() != "generic"
    assert "youtube" in payload["extractor"].lower()


def test_plain_web_page_is_generic_only(client):
    payload = _preflight(client, "https://example.com/some/page")
    assert payload == {"supported": False, "extractor": None, "generic_only": True}


@pytest.mark.parametrize("url", ["chrome://settings", "not-a-url", ""])
def test_invalid_url_is_a_verdict_not_an_error(client, url):
    payload = _preflight(client, url)
    assert payload == {"supported": False, "extractor": None, "generic_only": False}


def test_missing_url_param_is_a_verdict_not_an_error(client):
    response = client.get("/preflight")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "supported": False,
        "extractor": None,
        "generic_only": False,
    }


def test_preflight_creates_no_job_rows(client):
    # Read-only contract: a preflight check must leave no trace in /jobs
    # (trusted-LAN caller sees ALL jobs, so an empty list is conclusive).
    _preflight(client, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    _preflight(client, "https://example.com/some/page")
    response = client.get("/jobs")
    assert response.status_code == 200
    assert response.json() == []


def test_anonymous_caller_without_lan_trust_is_rejected(monkeypatch, client):
    # Same require_owner dependency as /jobs (cf. tests/api/test_auth.py).
    monkeypatch.setenv("KARAOKE_TRUSTED_CIDRS", "")
    reset_settings_for_tests()
    response = client.get(
        "/preflight", params={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
    )
    assert response.status_code == 401
