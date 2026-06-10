from __future__ import annotations

from pathlib import Path

from karaoke import cli


def test_cli_run_invokes_local_pipeline(tmp_path, monkeypatch, capsys):
    calls = {}

    class Result:
        job_root = tmp_path / "job"

    def fake_run(url, config, settings, *, heartbeat):
        calls["url"] = url
        calls["config"] = config
        calls["settings"] = settings
        heartbeat("download", "ok")
        return Result()

    monkeypatch.setattr(cli, "run_local_pipeline", fake_run)

    rc = cli.main(
        [
            "run",
            "https://example.test/video",
            "--output-dir",
            str(tmp_path / "job"),
            "--whisper-model",
            "tiny",
        ]
    )

    assert rc == 0
    assert calls["url"] == "https://example.test/video"
    assert calls["config"].output_dir == tmp_path / "job"
    assert calls["config"].device == "cpu-local"
    assert calls["config"].whisper_model == "tiny"
    assert calls["settings"].device_mode == "cpu-local"
    out = capsys.readouterr().out
    assert "[download] ok" in out
    assert str(Path(tmp_path / "job")) in out
