"""Favicon family: root serving + multi-resolution .ico integrity (issue #205).

Before #205 the only icon was a single 64×64 ``/app/favicon.png``; the bare
domain's ``/favicon.ico`` fell through to the API's JSON catch-all 404, and
there was no apple-touch / SVG / manifest asset. These tests pin:

* the favicon family is served at the *site root* (not just under ``/app``),
  with the right content types — never the JSON 404;
* the committed ``favicon.ico`` is a real multi-resolution (16/32/48) icon;
* the SSR ``/share`` page declares the favicon links.

The shared ``client`` fixture is intentionally not used for the root-serving
tests: it builds the app before a test could point ``KARAOKE_SPA_DIST_PATH`` at
a fake dist (see ``test_spa_headers.py``).
"""
from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from karaoke.api.app import create_app
from karaoke.api.spa_static import ROOT_ICON_ROUTES

#: The committed favicon family, rendered into web/spa/public by render-icons.mjs.
PUBLIC_DIR = Path(__file__).resolve().parents[2] / "web" / "spa" / "public"

#: Files the root routes serve (deduped — precomposed reuses apple-touch).
_ICON_FILES = sorted({filename for filename, _ in ROOT_ICON_ROUTES.values()})


def _build_fake_dist(tmp_path: Path) -> Path:
    """A Vite-shaped dist carrying the real committed favicon family."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>karaoke</title>")
    for filename in _ICON_FILES:
        (dist / filename).write_bytes((PUBLIC_DIR / filename).read_bytes())
    return dist


@pytest.fixture
def icon_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    dist = _build_fake_dist(tmp_path)
    monkeypatch.setenv("KARAOKE_SPA_DIST_PATH", str(dist))
    app = create_app()
    with TestClient(app) as tc:
        yield tc


@pytest.mark.parametrize("route_path", sorted(ROOT_ICON_ROUTES))
def test_root_icon_routes_serve_real_assets(
    icon_client: TestClient, route_path: str
) -> None:
    """Every favicon-family path resolves at the site root with its media type."""
    _, expected_type = ROOT_ICON_ROUTES[route_path]
    resp = icon_client.get(route_path)
    assert resp.status_code == 200, route_path
    assert resp.headers["content-type"] == expected_type, route_path
    assert resp.content, route_path
    # Not the JSON catch-all 404 that this issue is about.
    assert "application/json" not in resp.headers["content-type"], route_path


def test_favicon_ico_is_image_not_json(icon_client: TestClient) -> None:
    """The headline bug: GET /favicon.ico must be a real icon, not JSON 404."""
    resp = icon_client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/x-icon"
    # PNG-in-ICO entries start with the ICO directory header (type 1).
    reserved, icon_type, count = struct.unpack("<HHH", resp.content[:6])
    assert (reserved, icon_type) == (0, 1)
    assert count >= 3


def test_manifest_lists_icons(icon_client: TestClient) -> None:
    resp = icon_client.get("/site.webmanifest")
    assert resp.status_code == 200
    body = resp.json()
    sizes = {icon.get("sizes") for icon in body["icons"]}
    assert {"192x192", "512x512"} <= sizes


def test_missing_icon_file_is_404_not_500(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dist that exists but lacks an icon yields 404, never a 500."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>karaoke</title>")
    monkeypatch.setenv("KARAOKE_SPA_DIST_PATH", str(dist))
    app = create_app()
    with TestClient(app) as tc:
        assert tc.get("/favicon.ico").status_code == 404
        assert tc.get("/health").status_code == 200


def test_no_dist_does_not_register_root_icons(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without a built SPA the app still boots; root icons just 404 (no 500)."""
    monkeypatch.setenv("KARAOKE_SPA_DIST_PATH", str(tmp_path / "missing"))
    app = create_app()
    with TestClient(app) as tc:
        assert tc.get("/health").status_code == 200
        assert tc.get("/favicon.ico").status_code == 404


def test_committed_favicon_ico_is_multi_resolution() -> None:
    """The provenance/crispness criterion: .ico carries 16/32/48 PNG entries."""
    data = (PUBLIC_DIR / "favicon.ico").read_bytes()
    reserved, icon_type, count = struct.unpack("<HHH", data[:6])
    assert (reserved, icon_type) == (0, 1)
    assert count == 3
    sizes = []
    png_magic = b"\x89PNG\r\n\x1a\n"
    for i in range(count):
        off = 6 + i * 16
        width, height, *_rest, byte_count, offset = struct.unpack(
            "<BBBBHHII", data[off : off + 16]
        )
        sizes.append((width or 256, height or 256))
        assert data[offset : offset + 8] == png_magic, "ICO entry must be PNG payload"
        assert byte_count > 0
    assert sorted(sizes) == [(16, 16), (32, 32), (48, 48)]


def test_committed_favicon_svg_is_the_source_vector() -> None:
    """favicon.svg is the single-source mark.svg (same sign as the extension)."""
    repo_root = PUBLIC_DIR.parents[2]
    mark = (repo_root / "extension" / "chrome" / "icons" / "mark.svg").read_bytes()
    assert (PUBLIC_DIR / "favicon.svg").read_bytes() == mark


def test_share_page_declares_favicon(client: TestClient) -> None:
    """The SSR /share page links the favicon family (root-absolute)."""
    create = client.post("/jobs", json={"url": "https://example.com/x", "title": "T"})
    token = create.json()["job_token"]
    resp = client.get(f"/share/{token}", headers={"Accept": "text/html"})
    assert resp.status_code == 200
    body = resp.text
    assert 'rel="icon"' in body
    assert "/favicon.ico" in body
    assert 'rel="manifest"' in body
