"""Cache-Control headers on the /app SPA mount (issue #122).

The shared ``client`` fixture is deliberately not used: it builds the app
before a test body could point ``KARAOKE_SPA_DIST_PATH`` at the fake dist.
The autouse fixture in ``conftest.py`` already resets the settings cache, so
setting the env var and then calling ``create_app()`` inside the test (or a
test-owned fixture) is sufficient.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from karaoke.api.app import create_app

IMMUTABLE = "public, max-age=31536000, immutable"


def _build_fake_dist(tmp_path: Path) -> Path:
    """A minimal Vite-shaped dist: index.html, a root file, a hashed asset."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>karaoke</title>")
    (dist / "favicon.ico").write_bytes(b"\x00")
    (dist / "assets" / "index-abc123.js").write_text("console.log('karaoke')")
    return dist


@pytest.fixture
def spa_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    """TestClient over an app whose /app mount serves the fake dist."""
    dist = _build_fake_dist(tmp_path)
    monkeypatch.setenv("KARAOKE_SPA_DIST_PATH", str(dist))
    app = create_app()
    with TestClient(app) as tc:
        yield tc


def test_index_responses_are_no_cache(spa_client: TestClient) -> None:
    """Both /app/ (html-mode directory fallback) and the explicit file."""
    for path in ("/app/", "/app/index.html"):
        resp = spa_client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["cache-control"] == "no-cache", path


def test_hashed_assets_are_immutable(spa_client: TestClient) -> None:
    resp = spa_client.get("/app/assets/index-abc123.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == IMMUTABLE


def test_other_root_files_default_to_no_cache(spa_client: TestClient) -> None:
    resp = spa_client.get("/app/favicon.ico")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache"


def test_conditional_request_yields_304_with_header(
    spa_client: TestClient,
) -> None:
    """ETag conditionals still 304, and the 304 carries the new header."""
    for path, expected in (
        ("/app/index.html", "no-cache"),
        ("/app/assets/index-abc123.js", IMMUTABLE),
    ):
        first = spa_client.get(path)
        etag = first.headers["etag"]
        resp = spa_client.get(path, headers={"If-None-Match": etag})
        assert resp.status_code == 304, path
        assert resp.headers["cache-control"] == expected, path


def test_missing_path_is_404(spa_client: TestClient) -> None:
    """html-mode without a 404.html: non-files still 404 (no Vite 404.html)."""
    resp = spa_client.get("/app/does-not-exist")
    assert resp.status_code == 404


def test_app_boots_without_dist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mount stays conditional — no dist dir, app boots, /app is just 404."""
    monkeypatch.setenv("KARAOKE_SPA_DIST_PATH", str(tmp_path / "missing"))
    app = create_app()
    with TestClient(app) as tc:
        assert tc.get("/health").status_code == 200
        assert tc.get("/app/").status_code == 404
