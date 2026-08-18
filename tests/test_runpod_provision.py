"""Tests for scripts/runpod_provision.py — the stale-worker-flush GATE.

A flush (``workersMax`` bounce to 0 and back) must fire ONLY when the template
image actually changes (repoint or fresh create), and NEVER on an idempotent
no-op re-run. All HTTP is monkeypatched; the network is never touched and the
drain loop never really sleeps.

The script lives under ``scripts/`` (not on the package import path), so it is
loaded by file path via importlib.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "runpod_provision.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("runpod_provision", _SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def prov(monkeypatch):
    mod = _load_module()
    # Never really sleep; report the pool as already drained so the flush loop
    # exits on its first iteration.
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(mod, "_endpoint_worker_count", lambda *_a, **_k: 0)
    monkeypatch.setenv("RUNPOD_API_KEY", "rpa_test")
    return mod


def _install_fake_request(monkeypatch, mod, *, existing_image):
    """Fake ``_request`` that records calls and serves a template whose current
    imageName is ``existing_image`` (``None`` → template does not exist yet)."""
    calls: list[tuple[str, str, dict | None]] = []
    tmpl_id, ep_id = "tmpl_test", "ep_test"

    def fake_request(method, path, token, body=None):
        calls.append((method, path, body))
        if method == "GET" and path == "/templates":
            if existing_image is None:
                return {"templates": []}
            return {
                "templates": [
                    {"name": mod.TEMPLATE_NAME, "id": tmpl_id, "imageName": existing_image}
                ]
            }
        if method == "POST" and path == "/templates":
            return {"id": tmpl_id}
        if method == "GET" and path == "/endpoints":
            return {"endpoints": [{"name": mod.ENDPOINT_NAME, "id": ep_id}]}
        # PATCH/POST against a specific template/endpoint → benign ack.
        return {"id": tmpl_id if "templates" in path else ep_id}

    monkeypatch.setattr(mod, "_request", fake_request)
    return calls


def _flush_bounces(calls):
    """The single-key ``workersMax`` PATCHes against the endpoint, in order.

    This isolates the flush bounce from ``_ensure_endpoint``'s full-spec PATCH
    (which also carries a ``workersMax`` key but many others alongside it)."""
    return [
        body
        for (method, path, body) in calls
        if method == "PATCH"
        and path.startswith("/endpoints/")
        and isinstance(body, dict)
        and set(body.keys()) == {"workersMax"}
    ]


def test_no_flush_when_image_unchanged(prov, monkeypatch):
    calls = _install_fake_request(
        monkeypatch, prov, existing_image=prov.TEMPLATE_SPEC["imageName"]
    )
    assert prov.main() == 0
    assert _flush_bounces(calls) == [], "no-op re-run must not flush workers"


def test_flush_when_image_changed(prov, monkeypatch):
    calls = _install_fake_request(
        monkeypatch,
        prov,
        existing_image="ghcr.io/befeast/karaoke-runpod:cuda12.4-OLD",
    )
    assert prov.main() == 0
    assert _flush_bounces(calls) == [
        {"workersMax": 0},
        {"workersMax": prov.ENDPOINT_SPEC["workersMax"]},
    ]


def test_flush_when_template_created(prov, monkeypatch):
    # A brand-new template (none existing) is a new image for the endpoint.
    calls = _install_fake_request(monkeypatch, prov, existing_image=None)
    assert prov.main() == 0
    assert _flush_bounces(calls) == [
        {"workersMax": 0},
        {"workersMax": prov.ENDPOINT_SPEC["workersMax"]},
    ]


@pytest.mark.parametrize(
    "boom",
    [KeyboardInterrupt, RuntimeError],
    ids=["keyboard-interrupt", "request-exception"],
)
def test_flush_restores_workers_max_after_exception_mid_drain(
    prov, monkeypatch, boom
):
    """A flush dying mid-drain must still restore workersMax (incident #279:
    a died flush left the endpoint paused at workersMax=0 for ~a week)."""
    calls = _install_fake_request(monkeypatch, prov, existing_image=None)

    def raise_boom(*_a, **_k):
        raise boom()

    monkeypatch.setattr(prov, "_endpoint_worker_count", raise_boom)
    with pytest.raises(boom):
        prov._flush_workers("rpa_test", "ep_test")
    assert _flush_bounces(calls) == [
        {"workersMax": 0},
        {"workersMax": prov.ENDPOINT_SPEC["workersMax"]},
    ], "restore PATCH must fire even when the drain poll raises"


def test_flush_restore_failure_is_loud_and_nonzero(prov, monkeypatch, capsys):
    """If the restore PATCH itself fails, the operator gets an actionable
    stderr message (endpoint may be paused + exact curl) and a non-zero exit."""
    target = prov.ENDPOINT_SPEC["workersMax"]

    def fake_request(method, path, token, body=None):
        if body == {"workersMax": target}:
            # Exactly what the real ``_request`` does on HTTP/network errors.
            raise SystemExit(3)
        return {}

    monkeypatch.setattr(prov, "_request", fake_request)
    with pytest.raises(SystemExit) as excinfo:
        prov._flush_workers("rpa_test", "ep_test")
    assert excinfo.value.code == 3
    err = capsys.readouterr().err
    assert "MAY BE LEFT PAUSED" in err
    assert f'curl -X PATCH "{prov.API_BASE}/endpoints/ep_test"' in err
    assert f"-d '{{\"workersMax\": {target}}}'" in err
