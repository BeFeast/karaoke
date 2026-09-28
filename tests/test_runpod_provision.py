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


# ---------------------------------------------------------------------------
# #299: live-endpoint mode (RUNPOD_ENDPOINT_ID) — template per image tag,
# only templateId is PATCHed, Blackwell MIG exclusions are kept/added
# ---------------------------------------------------------------------------
def _live_fakes(monkeypatch, mod, *, endpoint_template, templates, gpu_ids):
    calls: list[tuple] = []

    def fake_request(method, path, token, body=None):
        calls.append((method, path, body))
        if method == "GET" and path == "/templates":
            return templates
        if method == "POST" and path == "/templates":
            return {"id": "tmpl-new"}
        if method == "GET" and path.startswith("/endpoints/"):
            return {"id": "ep-live", "templateId": endpoint_template, "workersMax": 3}
        return {}

    graphql_calls: list[str] = []

    def fake_graphql(token, query):
        graphql_calls.append(query)
        if query.startswith("mutation"):
            return {"saveEndpoint": {"id": "ep-live"}}
        return {"myself": {"endpoints": [{
            "id": "ep-live", "name": "karaoke-poc-2", "templateId": endpoint_template,
            "gpuIds": gpu_ids, "workersMax": 3, "workersMin": 0, "idleTimeout": 5,
            "scalerType": "QUEUE_DELAY", "scalerValue": 4,
        }]}}

    monkeypatch.setattr(mod, "_request", fake_request)
    monkeypatch.setattr(mod, "_graphql", fake_graphql)
    monkeypatch.setenv("RUNPOD_ENDPOINT_ID", "ep-live")
    return calls, graphql_calls


_WITH_EXCLUSIONS = "ADA_24,AMPERE_24," + ",".join(
    "-" + g for g in (
        "NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 1g.24gb",
        "NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb",
    )
)


def test_live_mode_creates_tag_template_repoints_and_flushes(prov, monkeypatch):
    calls, gql = _live_fakes(monkeypatch, prov, endpoint_template="tmpl-old", templates=[], gpu_ids=_WITH_EXCLUSIONS)
    assert prov.main() == 0
    post = [c for c in calls if c[0] == "POST" and c[1] == "/templates"][0]
    assert post[2]["name"] == "karaoke-" + prov.TEMPLATE_SPEC["imageName"].rsplit(":", 1)[-1].replace("cuda12.4-", "")
    patches = [c for c in calls if c[0] == "PATCH" and c[1] == "/endpoints/ep-live"]
    assert patches[0][2] == {"templateId": "tmpl-new"}  # nothing but the template
    assert {"workersMax": 0} in [p[2] for p in patches]
    assert not any(q.startswith("mutation") for q in gql)


def test_live_mode_is_a_noop_on_the_same_image(prov, monkeypatch):
    name = "karaoke-" + prov.TEMPLATE_SPEC["imageName"].rsplit(":", 1)[-1].replace("cuda12.4-", "")
    templates = [{"id": "tmpl-cur", "name": name, "imageName": prov.TEMPLATE_SPEC["imageName"]}]
    calls, _ = _live_fakes(monkeypatch, prov, endpoint_template="tmpl-cur", templates=templates, gpu_ids=_WITH_EXCLUSIONS)
    assert prov.main() == 0
    assert not [c for c in calls if c[0] == "PATCH"]


def test_live_mode_adds_missing_mig_exclusions(prov, monkeypatch):
    _, gql = _live_fakes(monkeypatch, prov, endpoint_template="tmpl-old", templates=[], gpu_ids="ADA_24,AMPERE_24")
    assert prov.main() == 0
    mutation = [q for q in gql if q.startswith("mutation")][0]
    assert "-NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 1g.24gb" in mutation
    assert "-NVIDIA RTX PRO 6000 Blackwell Server Edition MIG 2g.48gb" in mutation
    assert '"ADA_24,AMPERE_24,' in mutation
