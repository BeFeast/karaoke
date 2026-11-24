"""Exercise the v2 provisioner without contacting or modifying RunPod."""
from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "runpod_provision.py"


@pytest.fixture
def prov(monkeypatch):
    spec = importlib.util.spec_from_file_location("runpod_provision", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("RUNPOD_API_KEY", "test-authorization")
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "test-endpoint")
    monkeypatch.setattr(module, "_endpoint_worker_count", lambda *_: 0)
    return module


def endpoint(prov, **updates):
    return {
        "image": prov.IMAGE,
        "gpu": {"pools": ["ADA_24"], "excludedTypes": [prov.REQUIRED_GPU_EXCLUSIONS["ADA_24"]]},
        "workers": {"min": 1, "max": 3, "idleTimeout": 5},
        "env": {"HANDLER_SETTING": "fixture-value"},
        **updates,
    }


def install_request(monkeypatch, prov, response):
    calls = []

    def request(method, path, token, body=None):
        calls.append((method, path, body))
        return response

    monkeypatch.setattr(prov, "_request", request)
    return calls


def test_requires_existing_endpoint_id(prov, monkeypatch, capsys):
    monkeypatch.delenv("RUNPOD_ENDPOINT_ID")
    calls = install_request(monkeypatch, prov, {})
    assert prov.main() == 2
    assert calls == []
    assert "Create and configure the endpoint in RunPod first" in capsys.readouterr().err


def test_no_writes_when_image_already_matches(prov, monkeypatch):
    calls = install_request(monkeypatch, prov, endpoint(prov))
    assert prov.main() == 0
    assert calls == [("GET", "/serverless/test-endpoint", None)]


def test_image_patch_preserves_other_settings_and_restores_warm_worker_bounds(prov, monkeypatch):
    calls = install_request(monkeypatch, prov, endpoint(prov, image="example.invalid/worker:old"))
    assert prov.main() == 0
    path = "/serverless/test-endpoint"
    assert calls == [
        ("GET", path, None),
        ("PATCH", path, {"image": prov.IMAGE}),
        ("PATCH", path, {"workers": {"min": 0, "max": 0}}),
        ("PATCH", path, {"workers": {"min": 1, "max": 3}}),
    ]


def test_missing_mig_exclusion_fails_before_writing(prov, monkeypatch, capsys):
    data = endpoint(prov, image="example.invalid/worker:old", gpu={"pools": ["ADA_24"]})
    calls = install_request(monkeypatch, prov, data)
    with pytest.raises(SystemExit) as exc:
        prov.main()
    assert exc.value.code == 3
    assert calls == [("GET", "/serverless/test-endpoint", None)]
    assert "missing required Blackwell MIG exclusions" in capsys.readouterr().err


@pytest.mark.parametrize("boom", [KeyboardInterrupt, RuntimeError])
def test_flush_restores_worker_bounds_after_interrupted_drain(prov, monkeypatch, boom):
    calls = install_request(monkeypatch, prov, {})

    def interrupt(*_):
        raise boom()

    monkeypatch.setattr(prov, "_endpoint_worker_count", interrupt)
    with pytest.raises(boom):
        prov._flush_workers("test-authorization", "test-endpoint", {"min": 1, "max": 3})
    path = "/serverless/test-endpoint"
    assert calls == [
        ("PATCH", path, {"workers": {"min": 0, "max": 0}}),
        ("PATCH", path, {"workers": {"min": 1, "max": 3}}),
    ]


def test_flush_restore_failure_has_v2_recovery_command(prov, monkeypatch, capsys):
    def request(method, path, token, body=None):
        if body == {"workers": {"min": 1, "max": 3}}:
            raise SystemExit(3)

    monkeypatch.setattr(prov, "_request", request)
    with pytest.raises(SystemExit) as exc:
        prov._flush_workers("test-authorization", "test-endpoint", {"min": 1, "max": 3})
    assert exc.value.code == 3
    err = capsys.readouterr().err
    assert "MAY BE LEFT PAUSED" in err
    assert "https://api.runpod.io/v2/serverless/$RUNPOD_ENDPOINT_ID" in err
    assert "test-authorization" not in err


def test_management_api_rejects_error_without_printing_response(prov, monkeypatch, capsys):
    def failing_open(req, timeout):
        assert req.full_url == "https://api.runpod.io/v2/serverless/test-endpoint"
        assert req.get_header("Authorization") == "Bearer test-authorization"
        raise urllib.error.HTTPError(
            req.full_url, 403, "Forbidden", {}, io.BytesIO(b'{"env":"provider-private-marker"}'),
        )

    monkeypatch.setattr(prov._HTTP, "open", failing_open)
    with pytest.raises(SystemExit) as exc:
        prov._request("GET", "/serverless/test-endpoint", "test-authorization")
    assert exc.value.code == 3
    err = capsys.readouterr().err
    assert "HTTP 403" in err
    assert "provider-private-marker" not in err
    assert "test-authorization" not in err
