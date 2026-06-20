"""``GET /preflight`` — offline yt-dlp extractor matching (issues #180, #192)."""
from __future__ import annotations

import pytest

from karaoke.api.preflight import match_url
from karaoke.config import reset_settings_for_tests


def _preflight(client, url: str) -> dict:
    response = client.get("/preflight", params={"url": url})
    assert response.status_code == 200, response.text
    return response.json()


def test_youtube_watch_url_is_supported(client):
    payload = _preflight(client, "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert payload["supported"] is True
    assert payload["generic_only"] is False
    # A watch URL is a confident single track — the only auto-submit path (#192).
    assert payload["single_media"] is True
    # Don't pin yt-dlp's exact IE_NAME casing — just require a dedicated
    # (non-Generic) youtube extractor.
    assert payload["extractor"]
    assert payload["extractor"].lower() != "generic"
    assert "youtube" in payload["extractor"].lower()


def test_youtube_home_is_supported_but_not_single_media(client):
    # The home feed matches a dedicated youtube:* extractor, so it is
    # `supported`, but it is a container (recommended feed) — `single_media`
    # must be False so the extension demotes it to confirm instead of silently
    # minting a job (#192).
    payload = _preflight(client, "https://www.youtube.com/")
    assert payload["supported"] is True
    assert payload["single_media"] is False


# Container YouTube URLs: each matches a dedicated youtube:* extractor
# (`supported=True`) but returns a feed / channel / playlist / search, not a
# single video — so `single_media` must be False (#192).
@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/feed/recommended",
        "https://www.youtube.com/@YouTube",
        "https://www.youtube.com/playlist?list=PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI",
        "https://www.youtube.com/results?search_query=karaoke",
    ],
)
def test_youtube_containers_are_supported_but_not_single_media(client, url):
    payload = _preflight(client, url)
    assert payload["supported"] is True
    assert payload["single_media"] is False


def test_plain_web_page_is_generic_only(client):
    payload = _preflight(client, "https://example.com/some/page")
    assert payload == {
        "supported": False,
        "extractor": None,
        "generic_only": True,
        "single_media": False,
    }


@pytest.mark.parametrize("url", ["chrome://settings", "not-a-url", ""])
def test_invalid_url_is_a_verdict_not_an_error(client, url):
    payload = _preflight(client, url)
    assert payload == {
        "supported": False,
        "extractor": None,
        "generic_only": False,
        "single_media": False,
    }


def test_missing_url_param_is_a_verdict_not_an_error(client):
    response = client.get("/preflight")
    assert response.status_code == 200, response.text
    assert response.json() == {
        "supported": False,
        "extractor": None,
        "generic_only": False,
        "single_media": False,
    }


def test_match_url_single_media_classification():
    # Direct unit-level acceptance (#192): only a watch URL is single-media;
    # the home feed and the other containers are not.
    assert match_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ").single_media is True
    home = match_url("https://www.youtube.com/")
    assert home.supported is True
    assert home.single_media is False


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
