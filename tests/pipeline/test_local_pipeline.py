from __future__ import annotations

import json
from pathlib import Path

import pytest

from karaoke.config import Settings
from karaoke.pipeline.local import (
    LocalPipelineConfig,
    _run_demucs_cpu,
    run_local_pipeline,
)
from karaoke.worker.pipeline import PipelineError


def test_local_pipeline_writes_prd_layout(tmp_path, monkeypatch):
    import karaoke.pipeline.local as local

    def fake_metadata(url, settings=None, **kwargs):
        return {
            "title": "Artist - Track",
            "artist": "Artist",
            "track": "Track",
            "duration": 123,
        }

    def fake_download(url, dest: Path, settings=None, **kwargs):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"raw")
        return dest

    def fake_to_wav(src: Path, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"wav")
        return dest

    def fake_wav_to_mp3(src: Path, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"mp3:" + src.name.encode())
        return dest

    def fake_demucs(source_wav: Path, stems_root: Path, config):
        stem_dir = stems_root / config.demucs_model / source_wav.stem
        stem_dir.mkdir(parents=True, exist_ok=True)
        vocals = stem_dir / "vocals.wav"
        no_vocals = stem_dir / "no_vocals.wav"
        vocals.write_bytes(b"vocals-wav")
        no_vocals.write_bytes(b"no-vocals-wav")
        return vocals, no_vocals

    monkeypatch.setattr(local, "_ytdlp_metadata", fake_metadata)
    monkeypatch.setattr(local, "_download_audio", fake_download)
    monkeypatch.setattr(local, "_to_wav", fake_to_wav)
    monkeypatch.setattr(local, "_wav_to_mp3", fake_wav_to_mp3)
    monkeypatch.setattr(local, "_run_demucs_cpu", fake_demucs)
    monkeypatch.setattr(local, "_transcribe_vocals", lambda vocals, config: "line one\nline two")

    seen: list[tuple[str, str]] = []
    result = run_local_pipeline(
        "https://example.test/video",
        LocalPipelineConfig(output_dir=tmp_path / "job", device="cpu-local"),
        Settings(device_mode="cpu-local", artifact_root=str(tmp_path / "job")),
        heartbeat=lambda stage, message: seen.append((stage, message)),
    )

    expected = [
        result.job_root / "source" / "source.mp3",
        result.job_root / "stems" / "htdemucs" / "source" / "vocals.mp3",
        result.job_root / "stems" / "htdemucs" / "source" / "no_vocals.mp3",
        result.job_root / "exports" / "karaoke.mp3",
        result.job_root / "exports" / "vocals.mp3",
        result.job_root / "lyrics" / "lyrics.txt",
        result.job_root / "logs" / "worker.log",
        result.job_root / "metadata.json",
    ]
    assert all(path.is_file() for path in expected)
    assert result.lyrics_txt.read_text(encoding="utf-8") == "line one\nline two\n"
    assert result.worker_log.read_text(encoding="utf-8")
    assert [stage for stage, _ in seen] == [
        "metadata",
        "download",
        "normalize",
        "separate",
        "transcribe",
        "export",
        "completed",
    ]

    metadata = json.loads(result.metadata_json.read_text(encoding="utf-8"))
    assert metadata["device"] == "cpu-local"
    assert "vast_instance_id" not in metadata
    assert "vast_cost" not in metadata
    assert metadata["artifacts"] == {
        "source": "source/source.mp3",
        "vocals_stem": "stems/htdemucs/source/vocals.mp3",
        "no_vocals_stem": "stems/htdemucs/source/no_vocals.mp3",
        "karaoke": "exports/karaoke.mp3",
        "vocals": "exports/vocals.mp3",
        "lyrics": "lyrics/lyrics.txt",
        "worker_log": "logs/worker.log",
    }


def test_local_pipeline_rejects_non_cpu_device(tmp_path):
    with pytest.raises(PipelineError, match="cpu-local"):
        run_local_pipeline(
            "https://example.test/video",
            LocalPipelineConfig(output_dir=tmp_path / "job", device="vast"),
            Settings(device_mode="vast"),
        )


def test_demucs_wrapper_uses_htdemucs_two_stem_cpu(tmp_path, monkeypatch):
    import karaoke.pipeline.local as local

    calls: list[list[str]] = []

    def fake_run_cmd(cmd, *, timeout):
        calls.append(cmd)
        stem_dir = tmp_path / "stems" / "htdemucs" / "source"
        stem_dir.mkdir(parents=True, exist_ok=True)
        (stem_dir / "vocals.wav").write_bytes(b"v")
        (stem_dir / "no_vocals.wav").write_bytes(b"i")

    monkeypatch.setattr(local, "_run_cmd", fake_run_cmd)
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")

    vocals, no_vocals = _run_demucs_cpu(
        source,
        tmp_path / "stems",
        LocalPipelineConfig(output_dir=tmp_path, device="cpu-local"),
    )

    assert calls == [
        [
            "demucs",
            "--two-stems",
            "vocals",
            "-n",
            "htdemucs",
            "--device",
            "cpu",
            "-o",
            str(tmp_path / "stems"),
            str(source),
        ]
    ]
    assert vocals.name == "vocals.wav"
    assert no_vocals.name == "no_vocals.wav"


def test_demucs_wrapper_requires_expected_stems(tmp_path, monkeypatch):
    import karaoke.pipeline.local as local

    monkeypatch.setattr(local, "_run_cmd", lambda cmd, *, timeout: None)
    source = tmp_path / "source.wav"
    source.write_bytes(b"wav")

    with pytest.raises(PipelineError, match="expected stems"):
        _run_demucs_cpu(
            source,
            tmp_path / "stems",
            LocalPipelineConfig(output_dir=tmp_path, device="cpu-local"),
        )
