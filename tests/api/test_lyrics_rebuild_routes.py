"""Owner-only lyrics rebuild / pasted reference routes (#284)."""
import json

from .test_routes import _clerk_jwt, _seed_job


def _seed(settings, tmp_path, monkeypatch, *, owner="alice", with_stem=True, runpod=True):
    monkeypatch.setattr(settings, "artifact_root", tmp_path)
    if runpod:
        monkeypatch.setattr(settings, "runpod_api_key", "k")
        monkeypatch.setattr(settings, "runpod_endpoint_id", "ep")
    job_id, token = _seed_job("completed", owner_subject=owner)
    work = tmp_path / token / "work"
    export = tmp_path / token / "exports"
    work.mkdir(parents=True)
    export.mkdir(parents=True)
    if with_stem:
        (work / "vocals.wav").write_bytes(b"RIFF")
    (export / "metadata.json").write_text(json.dumps({"lyrics_source": "whisper_asr_synced", "track": "שיר"}))
    (export / "lyrics.txt").write_text("asr")
    calls = []
    import karaoke.api.routes as routes

    monkeypatch.setattr(routes, "schedule_rebuild", lambda sf, jid, st, **kw: calls.append((jid, kw)))
    return job_id, token, {"Authorization": f"Bearer {_clerk_jwt(owner)}"}, calls


def test_owner_can_start_a_rebuild(client, settings, tmp_path, monkeypatch):
    job, _, auth, calls = _seed(settings, tmp_path, monkeypatch)
    r = client.post(f"/jobs/{job}/lyrics-rebuild", headers=auth)
    assert r.status_code == 202 and r.json() == {"status": "started", "job_id": job}
    assert calls == [(job, {"reference_text": None, "requested_by": "alice"})]


def test_other_users_get_404(client, settings, tmp_path, monkeypatch):
    job, _, _, calls = _seed(settings, tmp_path, monkeypatch)
    r = client.post(f"/jobs/{job}/lyrics-rebuild", headers={"Authorization": f"Bearer {_clerk_jwt('bob')}"})
    assert r.status_code == 404 and calls == []


def test_rebuild_refused_without_stem_or_when_running(client, settings, tmp_path, monkeypatch):
    job, token, auth, calls = _seed(settings, tmp_path, monkeypatch, with_stem=False)
    assert client.post(f"/jobs/{job}/lyrics-rebuild", headers=auth).status_code == 409
    (tmp_path / token / "work" / "vocals.wav").write_bytes(b"RIFF")
    (tmp_path / token / "work" / ".lyrics-rebuild.lock").write_text("now")
    r = client.post(f"/jobs/{job}/lyrics-rebuild", headers=auth)
    assert r.status_code == 409 and "already running" in r.json()["detail"]
    payload = client.get(f"/share/{token}/lyrics", headers=auth).json()
    assert payload["rebuilding"] is True
    assert calls == []


def test_rebuild_refused_on_the_vast_backend(client, settings, tmp_path, monkeypatch):
    job, _, auth, _ = _seed(settings, tmp_path, monkeypatch, runpod=False)
    monkeypatch.setattr(settings, "runpod_api_key", "")
    assert client.post(f"/jobs/{job}/lyrics-rebuild", headers=auth).status_code == 409


def test_paste_reference_requires_confirmation_and_plain_lines(client, settings, tmp_path, monkeypatch):
    job, _, auth, calls = _seed(settings, tmp_path, monkeypatch)
    url = f"/jobs/{job}/lyrics-reference"
    assert client.post(url, json={"text": "a\nb", "confirm": False}, headers=auth).status_code == 422
    assert client.post(url, json={"text": "only one line", "confirm": True}, headers=auth).status_code == 422
    assert client.post(url, json={"text": "[00:01.00]<00:01.00>a\nb", "confirm": True}, headers=auth).status_code == 422
    r = client.post(url, json={"text": " שורה ראשונה \n\n שורה שנייה ", "confirm": True}, headers=auth)
    assert r.status_code == 202
    assert calls == [(job, {"reference_text": "שורה ראשונה\nשורה שנייה", "requested_by": "alice"})]
