"""CPU-local fallback for karaoke GPU stages.

This is the last-resort path used when the ephemeral GPU provider is
unavailable or over budget. It keeps the same result contract as the GPU
clients so the pipeline finalization path stays shared.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

from karaoke.worker.vast_client import GpuJobResult


class CpuLocalError(RuntimeError):
    """Any local CPU separation/transcription failure."""


def _run(cmd: list[str], *, timeout: int | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise CpuLocalError(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stderr:\n{proc.stderr[-2000:]}"
        )
    return proc


class CpuLocalClient:
    """Run Demucs + faster-whisper on the coordinator CPU.

    Slow by design, but useful as a bounded fallback when no paid GPU runtime
    can be used. Python dependencies are supplied through ``uv run --with`` so
    they do not become coordinator runtime dependencies for the normal path.
    """

    def __init__(self, settings) -> None:
        self.settings = settings

    def run(
        self,
        mix_wav: Path,
        work_dir: Path,
        *,
        align_text: str | None = None,
        align_lang: str | None = None,
    ) -> GpuJobResult:
        _ = (align_text, align_lang)
        work_dir.mkdir(parents=True, exist_ok=True)

        vocals_path, instrumental_path = self._run_demucs(mix_wav, work_dir)
        lyrics_txt_path, lyrics_json_path = self._run_whisper(vocals_path, work_dir)

        return GpuJobResult(
            vast_instance_id="cpu-local",
            vast_cost=0.0,
            gpu_model="cpu-local",
            vocals_path=vocals_path,
            instrumental_path=instrumental_path,
            lyrics_txt_path=lyrics_txt_path,
            lyrics_json_path=lyrics_json_path,
            aligned_lrc_path=None,
        )

    def _run_demucs(self, mix_wav: Path, work_dir: Path) -> tuple[Path, Path]:
        out_dir = work_dir / "cpu-demucs"
        _run(
            [
                "uv", "run",
                "--with", "demucs",
                "--with", "torch",
                "python", "-m", "demucs.separate",
                "-n", "htdemucs",
                "--two-stems", "vocals",
                "-o", str(out_dir),
                str(mix_wav),
            ],
            timeout=int(getattr(self.settings, "vast_max_instance_seconds", 1800) or 1800),
        )
        base = out_dir / "htdemucs" / mix_wav.stem
        demucs_vocals = base / "vocals.wav"
        demucs_inst = base / "no_vocals.wav"
        if not demucs_vocals.is_file() or not demucs_inst.is_file():
            raise CpuLocalError(f"Demucs output missing under {base}")

        vocals_path = work_dir / "vocals.wav"
        instrumental_path = work_dir / "instrumental.wav"
        vocals_path.write_bytes(demucs_vocals.read_bytes())
        instrumental_path.write_bytes(demucs_inst.read_bytes())
        return vocals_path, instrumental_path

    def _run_whisper(self, vocals_path: Path, work_dir: Path) -> tuple[Path, Path]:
        lyrics_txt_path = work_dir / "lyrics.txt"
        lyrics_json_path = work_dir / "lyrics.json"
        script = textwrap.dedent(
            """
            import json
            import sys
            from faster_whisper import WhisperModel

            wav, txt_out, json_out = sys.argv[1:4]
            model = WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
            segments, info = model.transcribe(wav, vad_filter=True)
            rows = []
            lines = []
            for seg in segments:
                text = (seg.text or "").strip()
                if not text:
                    continue
                lines.append(text)
                rows.append({"start": seg.start, "end": seg.end, "text": text})
            with open(txt_out, "w", encoding="utf-8") as fh:
                fh.write("\\n".join(lines))
            with open(json_out, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "language": getattr(info, "language", None),
                        "segments": rows,
                    },
                    fh,
                    ensure_ascii=False,
                    indent=2,
                )
            """
        ).strip()
        _run(
            [
                "uv", "run",
                "--with", "faster-whisper",
                "python", "-c", script,
                str(vocals_path),
                str(lyrics_txt_path),
                str(lyrics_json_path),
            ],
            timeout=int(getattr(self.settings, "vast_max_instance_seconds", 1800) or 1800),
        )
        if not lyrics_txt_path.is_file() or not lyrics_json_path.is_file():
            raise CpuLocalError(f"Whisper output missing under {work_dir}")
        return lyrics_txt_path, lyrics_json_path
