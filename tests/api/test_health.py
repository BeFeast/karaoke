"""Tests for the public ``GET /health`` liveness + version endpoint.

The release pipeline (#99) verifies a deploy by polling ``/health`` until it
reports the exact version just built, so the endpoint must surface the running
package version (``importlib.metadata`` driven, from ``pyproject.toml``).
"""
from __future__ import annotations

import karaoke


def test_health_reports_ok_and_version(client):
    response = client.get("/health")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body
    # The reported version is the running package version (deploy-truth).
    assert body["version"] == karaoke.__version__


def test_health_version_is_xyz_shaped(client):
    body = client.get("/health").json()
    # X.Y.Z (or the editable/uninstalled "0.0.0+unknown" fallback) — two dots.
    assert body["version"].count(".") == 2


def test_health_is_public_no_auth(client):
    """``/health`` must be reachable without any auth header (meta tag)."""
    response = client.get("/health")
    assert response.status_code == 200


def test_app_openapi_version_matches_package(client):
    """The FastAPI app version (OpenAPI doc) tracks the package version too."""
    assert client.app.version == karaoke.__version__
