"""Lyrics-only rebuild of a finished job (#284) — fake GPU, fake sources."""
from __future__ import annotations

import datetime as dt
import json

import pytest
import pytest_asyncio

SUNG = [
    (56.7, "תדע זה פשע"), (57.94, "מישהו צריך להתערב"), (60.1, "כל יום בתשע אתה רואה לי את הלב"),
    (64.8, "מפחיד להתאהב אוהב שזה כואב"), (72.4, "מאמי תיראה מה נהיה ממני"),
]


def _lrc(lines):
    return "\n".join(f"[{int(t) // 60:02d}:{t % 60:05.2f}]{x}" for t, x in lines)


def _asr_json(lines, heard=lambda s: s):
    return json.dumps({"language": "he", "model": "ivrit-ai/whisper-large-v3-turbo-ct2", "segments": [
        {"start": t, "end": t + 1.5, "text": heard(x),
         "words": [{"word": w, "start": t + i * 0.3, "end": t + i * 0.3 + 0.25, "probability": 0.95}
                   for i, w in enumerate(heard(x).split())]}
        for t, x in lines
    ]}, ensure_ascii=False)


@pytest_asyncio.fixture
async def seeded(tmp_path, monkeypatch):
    import karaoke.worker.pipeline as pipeline
    from karaoke.config import Settings
    from karaoke.db.models import Artifact, Base, Job, JobStatus
    from karaoke.db.session import create_engine_and_sessionmaker
    from karaoke.worker.canonical import CanonicalResolver
    from karaoke.worker.genius import GeniusSource
    from karaoke.worker.web_reference import WebReferenceFinder

    url = f"sqlite+aiosqlite:///{tmp_path / 'rb.db'}"
    engine, factory = create_engine_and_sessionmaker(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as session:
        job = Job(job_token="tok-rb", owner_subject="owner", source_url="https://example.com/v",
                  status=JobStatus.completed, progress=100, title="נועה קירל - לאב סונג",
                  completed_at=dt.datetime.now(dt.UTC) - dt.timedelta(days=30), vast_cost_micros=10000)
        session.add(job)
        await session.flush()
        session.add(Artifact(job_id=job.id, kind="lyrics", relative_path="tok-rb/exports/lyrics.txt", size_bytes=3))
        await session.commit()
        job_id = job.id
    work, exports = tmp_path / "tok-rb" / "work", tmp_path / "tok-rb" / "exports"
    work.mkdir(parents=True)
    exports.mkdir(parents=True)
    (work / "vocals.wav").write_bytes(b"RIFF")
    (exports / "lyrics.txt").write_text("asr")
    (exports / "metadata.json").write_text(json.dumps({
        "title": "נועה קירל - לאב סונג", "artist": "נועה קירל", "track": "לאב סונג", "duration": 227,
        "lyrics_source": "whisper_asr_synced", "synced": False, "lyrics_quality": {"status": "needs_review"},
        "vast_cost": 0.01,
    }, ensure_ascii=False))
    monkeypatch.setattr(pipeline, "_CANONICAL_RESOLVER", CanonicalResolver(search=lambda q: []))
    monkeypatch.setattr(pipeline, "_GENIUS_SOURCE", GeniusSource(http=lambda *a: (404, "")))
    monkeypatch.setattr(pipeline, "_WEB_REFERENCES", WebReferenceFinder(ytm_lyrics=lambda v: None, http=lambda *a: (404, "")))
    settings = Settings(database_url=url, runpod_api_key="k", runpod_endpoint_id="ep", artifact_root=str(tmp_path))
    yield factory, job_id, settings, work, exports
    await engine.dispose()


def _fake_gpu(monkeypatch, calls, *, aligned=None, raw=None):
    from karaoke.worker.runpod_client import RunpodClient
    from karaoke.worker.vast_client import GpuJobResult

    def run(self, stem, out_dir, *, align_text=None, align_lang=None, whisper_lang=None, mode="both"):
        calls.append({"mode": mode, "stem": stem.name, "align_text": align_text, "whisper_lang": whisper_lang})
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "lyrics.json").write_text(_asr_json(SUNG, lambda s: s.replace("בתשע", "בתשה")), encoding="utf-8")
        (out_dir / "lyrics.txt").write_text("\n".join(x for _, x in SUNG), encoding="utf-8")
        a = None
        if aligned is not None:
            a = out_dir / "aligned.lrc"
            a.write_text(aligned, encoding="utf-8")
        if raw is not None:
            (out_dir / "aligned.raw.lrc").write_text(raw, encoding="utf-8")
        return GpuJobResult(vast_instance_id="rp-1", vast_cost=0.004, gpu_model="fake", vocals_path=stem,
                            instrumental_path=stem, lyrics_txt_path=out_dir / "lyrics.txt",
                            lyrics_json_path=out_dir / "lyrics.json", aligned_lrc_path=a)

    monkeypatch.setattr(RunpodClient, "run", run)


@pytest.mark.asyncio
async def test_rebuild_finds_lrclib_and_swaps_exports(seeded, monkeypatch):
    from sqlalchemy import select

    import karaoke.worker.pipeline as pipeline
    from karaoke.db.models import Artifact, Job
    from karaoke.worker.lyrics import LyricsSource
    from karaoke.worker.lyrics_rebuild import rebuild_lyrics

    factory, job_id, settings, work, exports = seeded
    synced = _lrc(SUNG)
    plain = "\n".join(x for _, x in SUNG)
    monkeypatch.setattr(pipeline, "_LYRICS_SOURCE", LyricsSource(
        http=lambda m, u, p: (200, {"syncedLyrics": synced, "plainLyrics": plain}) if "/api/get" in u else (200, []),
        retry_delays=(0,)))
    calls = []
    _fake_gpu(monkeypatch, calls, aligned=synced)
    summary = await rebuild_lyrics(factory, job_id, settings, requested_by="owner")
    assert summary["source_before"] == "whisper_asr_synced" and summary["source_after"] == "lrclib_synced"
    assert calls == [{"mode": "lyrics", "stem": "vocals.wav", "align_text": plain, "whisper_lang": "he"}]
    assert (exports / "lyrics.lrc").read_text(encoding="utf-8").startswith("[00:56.70]")
    meta = json.loads((exports / "metadata.json").read_text(encoding="utf-8"))
    assert meta["lyrics_source"] == "lrclib_synced" and meta["title"] == "נועה קירל - לאב סונג"
    assert meta["lyrics_rebuild"]["reason"] == "rebuild" and meta["lyrics_rebuild"]["by"] == "owner"
    assert meta["whisper_model"] == "ivrit-ai/whisper-large-v3-turbo-ct2" and meta["vast_cost"] == 0.01
    backup = work / meta["lyrics_rebuild"]["backup"].split("work/", 1)[1]
    assert json.loads((backup / "metadata.json").read_text())["lyrics_source"] == "whisper_asr_synced"
    assert not (work / ".lyrics-rebuild.lock").exists()
    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job.lyrics_rebuild_cost_micros == 4000 and job.lyrics_rebuilt_at is not None
        assert job.stage_note is None and job.status == "completed"
        kinds = sorted(a.kind for a in (await session.scalars(select(Artifact).where(Artifact.job_id == job_id))).all())
        assert kinds == ["lyrics", "lyrics_lrc"]
    assert await pipeline._prior_24h_cost_micros(factory) == 4000  # counted in the daily cap


@pytest.mark.asyncio
async def test_pasted_reference_wins_in_its_span(seeded, monkeypatch):
    import karaoke.worker.pipeline as pipeline
    from karaoke.worker.lyrics import LyricsSource
    from karaoke.worker.lyrics_rebuild import rebuild_lyrics

    factory, job_id, settings, work, exports = seeded
    monkeypatch.setattr(pipeline, "_LYRICS_SOURCE", LyricsSource(http=lambda *a: (404, {}), retry_delays=(0,)))
    paste = "תדע זה פשע\nמישהו צריך להתערב"
    raw = "[00:56.70]תדע זה פשע\n[00:57.94]מישהו צריך להתערב"
    calls = []
    _fake_gpu(monkeypatch, calls, raw=raw)
    summary = await rebuild_lyrics(factory, job_id, settings, reference_text=paste, requested_by="owner")
    assert summary["source_after"] == "manual_reference", summary
    assert calls[0]["align_text"] == paste and calls[0]["mode"] == "lyrics"
    lines = (exports / "lyrics.lrc").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "[00:56.70]תדע זה פשע" and lines[1] == "[00:57.94]מישהו צריך להתערב"
    assert not any("בתשה" in ln for ln in lines)  # 60.1 s is inside the pasted span
    assert any("מפחיד" in ln for ln in lines[2:])  # uncovered spans stay transcript
    meta = json.loads((exports / "metadata.json").read_text(encoding="utf-8"))
    assert meta["lyrics_rebuild"]["reason"] == "reference_paste" and meta["lyrics_policy"] == "reference_wins_in_covered_spans"


@pytest.mark.asyncio
async def test_failed_rebuild_keeps_exports_and_records_the_note(seeded, monkeypatch):
    import karaoke.worker.pipeline as pipeline
    from karaoke.db.models import Job
    from karaoke.worker.lyrics import LyricsSource
    from karaoke.worker.lyrics_rebuild import rebuild_lyrics
    from karaoke.worker.runpod_client import RunpodClient, RunpodError

    factory, job_id, settings, work, exports = seeded
    monkeypatch.setattr(pipeline, "_LYRICS_SOURCE", LyricsSource(http=lambda *a: (404, {}), retry_delays=(0,)))

    def boom(self, *a, **k):
        raise RunpodError("endpoint on fire")

    monkeypatch.setattr(RunpodClient, "run", boom)
    before = (exports / "metadata.json").read_text(encoding="utf-8")
    summary = await rebuild_lyrics(factory, job_id, settings)
    assert "endpoint on fire" in summary["error"]
    assert (exports / "metadata.json").read_text(encoding="utf-8") == before
    assert not (work / ".lyrics-rebuild.lock").exists()
    async with factory() as session:
        job = await session.get(Job, job_id)
        assert job.stage_note.startswith("Lyrics rebuild failed") and job.status == "completed"


def test_rebuild_rederives_metadata_parsed_by_an_older_title_parser():
    from karaoke.worker.lyrics_rebuild import _current_source_meta

    old = {"artist": "עדן בן זקן כולם באילת", "track": "עם מילים HD", "duration": 199}
    assert _current_source_meta(old, "עדן בן זקן כולם באילת - עם מילים HD")["track"] == "עדן בן זקן כולם באילת"
    credit = {"artist": "נועה קירל", "track": "לאב סונג (Prod. By Nuri)", "duration": 227}
    assert _current_source_meta(credit, "נועה קירל - לאב סונג (Prod. By Nuri)")["track"] == "לאב סונג"
    fine = {"artist": "Radiohead", "track": "Creep", "duration": 237}
    assert _current_source_meta(fine, "Radiohead - Creep") == {**fine, "album": None}
