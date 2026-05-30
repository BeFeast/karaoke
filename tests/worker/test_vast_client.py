"""Unit tests for the vast.ai worker client.

Everything is MOCKED — no vast.ai REST call, no ssh, no tunnel, no httpx hits
the network. We exercise the offer filter, the daily budget cap, and (most
importantly) that the instance is ALWAYS destroyed in ``finally`` on both the
success path and when the GPU call raises.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from karaoke.config import Settings
from karaoke.worker import vast_client as vc
from karaoke.worker.vast_client import (
    GpuJobResult,
    VastBudgetError,
    VastClient,
    VastError,
    _select_offers,
)


def _settings(**over) -> Settings:
    base = dict(
        vast_api_key="k-test",
        vast_image="ghcr.io/befeast/karaoke-vast:cuda12.4",
        vast_max_price_per_hour=1.0,
        vast_max_job_cost=0.35,
        vast_min_cuda=12.4,
        vast_instance_ready_timeout=5,
        vast_offer_attempts=3,
        vast_daily_cost_cap=5.0,
    )
    base.update(over)
    return Settings(**base)


def _offer(**over) -> dict:
    base = dict(
        id=1,
        host_id=10,
        dph_total=0.30,
        cuda_max_good=12.6,
        reliability=0.99,
        gpu_name="RTX 4090",
        inet_down=1000,
    )
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# (a) _select_offers filtering
# ---------------------------------------------------------------------------
def test_select_offers_filters_low_cuda_and_wrong_gpu(monkeypatch):
    settings = _settings()
    offers = [
        _offer(id=1, gpu_name="RTX 4090", cuda_max_good=12.6),   # keep
        _offer(id=2, gpu_name="RTX 4090", cuda_max_good=12.1),   # drop: cuda < 12.4
        _offer(id=3, gpu_name="GTX 1080 Ti", cuda_max_good=12.8),  # drop: gpu mismatch
        _offer(id=4, gpu_name="A100", cuda_max_good=12.9),       # drop: not in karaoke allowlist
        _offer(id=5, gpu_name="L40S", cuda_max_good=12.5),       # keep
        _offer(id=6, gpu_name="RTX 4090", cuda_max_good=12.6, reliability=0.5),  # drop: reliability
        _offer(id=7, gpu_name="RTX 4090", cuda_max_good=12.6, dph_total=99.0),   # drop: price
    ]

    def fake_vast(api_key, method, path, payload=None, timeout=45):
        assert path == "/bundles/"
        return {"offers": offers}

    monkeypatch.setattr(vc, "_vast", fake_vast)
    kept = _select_offers(
        "k-test",
        max_price=settings.vast_max_price_per_hour,
        gpu_regex=settings.vast_gpu_regex,
        min_cuda=settings.vast_min_cuda,
    )
    kept_ids = {o["id"] for o in kept}
    assert kept_ids == {1, 5}, kept_ids
    # Cheapest first ordering preserved.
    assert kept[0]["dph_total"] <= kept[-1]["dph_total"]


def test_select_offers_raises_when_none_match(monkeypatch):
    monkeypatch.setattr(
        vc, "_vast",
        lambda *a, **k: {"offers": [_offer(cuda_max_good=11.0)]},
    )
    with pytest.raises(VastError, match="no Vast offer matched"):
        _select_offers("k", max_price=1.0, gpu_regex=_settings().vast_gpu_regex, min_cuda=12.4)


# ---------------------------------------------------------------------------
# (b) daily budget cap → job refused before provisioning
# ---------------------------------------------------------------------------
def test_daily_cap_refuses_before_provisioning(monkeypatch, tmp_path):
    # prior spend already at the cap → run() must refuse and never provision.
    settings = _settings(vast_daily_cost_cap=1.0, vast_max_job_cost=0.35)
    prior_micros = 1_000_000  # $1.00 already spent today == cap.

    create_calls: list = []

    def boom_vast(*a, **k):
        create_calls.append(a)
        raise AssertionError("must not call vast API once cap is reached")

    monkeypatch.setattr(vc, "_select_offers", lambda *a, **k: [_offer()])
    monkeypatch.setattr(vc, "_ensure_local_ssh_key", lambda: (Path("/dev/null"), "pub"))

    client = VastClient(
        settings,
        prior_24h_cost_micros=prior_micros,
        vast_call=boom_vast,
    )
    with pytest.raises(VastBudgetError, match="daily vast cost cap"):
        client.run(tmp_path / "mix.wav", tmp_path / "work")
    assert create_calls == []


def test_daily_cap_projection_refuses_when_next_job_would_breach(monkeypatch, tmp_path):
    # spent $4.80 of $5.00 cap; a $0.35 max-job would breach → refuse.
    settings = _settings(vast_daily_cost_cap=5.0, vast_max_job_cost=0.35)
    client = VastClient(settings, prior_24h_cost_micros=4_800_000)
    with pytest.raises(VastBudgetError):
        client.run(tmp_path / "mix.wav", tmp_path / "work")


# ---------------------------------------------------------------------------
# success-path scaffolding (mock tunnel + GPU zips)
# ---------------------------------------------------------------------------
class _FakeForward:
    def __init__(self, host, port, key_path, remote_port=8000):
        self.base_url = "http://127.0.0.1:55555"

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def _make_post_zip(work_writes):
    """Return a post_zip stub that 'unpacks' the right files per endpoint."""

    def post_zip(base_url, endpoint, wav, out_dir, timeout):
        out_dir.mkdir(parents=True, exist_ok=True)
        if endpoint == "/demucs":
            names = ["vocals.wav", "instrumental.wav"]
        elif endpoint == "/whisper":
            names = ["lyrics.txt", "lyrics.json"]
        else:  # pragma: no cover
            raise AssertionError(endpoint)
        paths = []
        for n in names:
            p = out_dir / n
            p.write_bytes(b"FAKE-" + n.encode())
            paths.append(p)
        work_writes.append(endpoint)
        return paths

    return post_zip


def _wire_ready(monkeypatch):
    """Patch the lifecycle so a single offer provisions + becomes ready cheaply."""
    monkeypatch.setattr(vc, "_select_offers", lambda *a, **k: [_offer(id=42)])
    monkeypatch.setattr(vc, "_ensure_local_ssh_key", lambda: (Path("/tmp/key"), "pub"))
    monkeypatch.setattr(
        vc, "_wait_for_ssh",
        lambda *a, **k: ("1.2.3.4", 2222),
    )
    monkeypatch.setattr(vc, "_wait_remote_ready", lambda *a, **k: None)


def _create_returns(instance_id):
    def fake_vast(api_key, method, path, payload=None, timeout=45):
        if path == "/ssh/":
            return {}
        if path.startswith("/asks/"):
            return {"new_contract": instance_id}
        if path.startswith("/instances/") and method == "POST":
            return {}
        return {}

    return fake_vast


# ---------------------------------------------------------------------------
# (c) destroy-in-finally on the SUCCESS path
# ---------------------------------------------------------------------------
def test_destroy_called_in_finally_on_success(monkeypatch, tmp_path):
    settings = _settings()
    _wire_ready(monkeypatch)
    destroyed: list[int] = []
    work_writes: list[str] = []

    client = VastClient(
        settings,
        prior_24h_cost_micros=0,
        vast_call=_create_returns(777),
        destroy_fn=lambda api_key, iid: destroyed.append(iid),
        forward_cls=_FakeForward,
        post_zip=_make_post_zip(work_writes),
    )
    result = client.run(tmp_path / "mix.wav", tmp_path / "work")

    assert isinstance(result, GpuJobResult)
    assert result.vast_instance_id == 777
    assert result.gpu_model == "RTX 4090"
    assert result.vocals_path.name == "vocals.wav"
    assert result.instrumental_path.name == "instrumental.wav"
    assert result.lyrics_txt_path.name == "lyrics.txt"
    assert result.lyrics_json_path.name == "lyrics.json"
    # Both GPU endpoints were called in one instance window.
    assert work_writes == ["/demucs", "/whisper"]
    # THE safety property: instance destroyed exactly once in finally.
    assert destroyed == [777]


# ---------------------------------------------------------------------------
# (c) destroy-in-finally when the GPU call RAISES
# ---------------------------------------------------------------------------
def test_destroy_called_in_finally_when_gpu_call_raises(monkeypatch, tmp_path):
    settings = _settings()
    _wire_ready(monkeypatch)
    destroyed: list[int] = []

    def exploding_post_zip(base_url, endpoint, wav, out_dir, timeout):
        out_dir.mkdir(parents=True, exist_ok=True)
        raise VastError("simulated /demucs HTTP 500")

    client = VastClient(
        settings,
        prior_24h_cost_micros=0,
        vast_call=_create_returns(999),
        destroy_fn=lambda api_key, iid: destroyed.append(iid),
        forward_cls=_FakeForward,
        post_zip=exploding_post_zip,
    )
    with pytest.raises(VastError, match="simulated /demucs"):
        client.run(tmp_path / "mix.wav", tmp_path / "work")
    # Even though the GPU call blew up, the instance was still destroyed.
    assert destroyed == [999]


# ---------------------------------------------------------------------------
# real _post_zip unpacking (no network: feed it a fake httpx response)
# ---------------------------------------------------------------------------
def test_post_zip_unpacks_returned_zip(monkeypatch, tmp_path):
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("vocals.wav", b"v")
        zf.writestr("instrumental.wav", b"i")
    payload = buf.getvalue()

    class _Resp:
        status_code = 200
        content = payload
        text = ""

    def fake_post(url, files=None, timeout=None):
        return _Resp()

    monkeypatch.setattr(vc.httpx, "post", fake_post)
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"x")
    out = vc._post_zip("http://x", "/demucs", wav, tmp_path / "out", 60)
    names = sorted(p.name for p in out)
    assert names == ["instrumental.wav", "vocals.wav"]


def test_post_zip_raises_on_non_200(monkeypatch, tmp_path):
    class _Resp:
        status_code = 500
        content = b""
        text = "boom"

    monkeypatch.setattr(vc.httpx, "post", lambda *a, **k: _Resp())
    wav = tmp_path / "in.wav"
    wav.write_bytes(b"x")
    with pytest.raises(VastError, match="HTTP 500"):
        vc._post_zip("http://x", "/demucs", wav, tmp_path / "out", 60)
