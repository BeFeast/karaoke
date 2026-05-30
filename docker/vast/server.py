"""HTTP server for karaoke vast.ai GPU worker.

Exposes Demucs separation + faster-whisper transcription over HTTP so the
coordinator (devbox) can SSH-tunnel-forward 8000 and POST work to it.

Endpoints:
- GET  /health  -> liveness probe + GPU status
- POST /demucs  -> multipart "file" wav input; returns zip of vocals.wav + instrumental.wav
- POST /whisper -> multipart "file" wav input; returns zip of lyrics.txt + lyrics.json

Listens on 0.0.0.0:8000. Single-process; vast.ai instances are per-job.
"""
from __future__ import annotations

import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("karaoke-vast")
logging.basicConfig(
    level=os.environ.get("KARAOKE_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

HOST = "0.0.0.0"
PORT = int(os.environ.get("KARAOKE_VAST_PORT", "8000"))

# Single global lock — one job at a time on a single-GPU vast.ai instance.
_GPU_LOCK = threading.Lock()
_WHISPER_MODEL = None
_WHISPER_LOCK = threading.Lock()


def _gpu_available() -> bool:
    try:
        import torch  # type: ignore

        return bool(torch.cuda.is_available())
    except Exception:  # pragma: no cover
        return False


def _get_whisper():
    """Lazy-load the faster-whisper large-v3-turbo model on the GPU."""
    global _WHISPER_MODEL
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            from faster_whisper import WhisperModel  # type: ignore

            device = "cuda" if _gpu_available() else "cpu"
            compute_type = "float16" if device == "cuda" else "int8"
            LOG.info("loading faster-whisper large-v3-turbo on %s/%s", device, compute_type)
            _WHISPER_MODEL = WhisperModel(
                "large-v3-turbo", device=device, compute_type=compute_type
            )
    return _WHISPER_MODEL


def _parse_multipart(rfile, content_type: str, content_length: int) -> Optional[bytes]:
    """Minimal multipart/form-data parser — extracts the first file part body."""
    if not content_type or "multipart/form-data" not in content_type:
        return None
    # Find boundary
    boundary = None
    for piece in content_type.split(";"):
        piece = piece.strip()
        if piece.startswith("boundary="):
            boundary = piece.split("=", 1)[1].strip().strip('"')
            break
    if not boundary:
        return None
    body = rfile.read(content_length)
    sep = b"--" + boundary.encode()
    parts = body.split(sep)
    for part in parts:
        # Each non-empty part: \r\nheaders\r\n\r\nbody\r\n
        if not part or part in (b"--\r\n", b"--"):
            continue
        part = part.lstrip(b"\r\n")
        hdr_end = part.find(b"\r\n\r\n")
        if hdr_end < 0:
            continue
        headers = part[:hdr_end].decode("utf-8", errors="replace")
        if "filename=" not in headers and "name=\"file\"" not in headers:
            # Still accept the first part with a body if no file marker.
            pass
        body_bytes = part[hdr_end + 4 :]
        # Strip trailing CRLF before next boundary.
        if body_bytes.endswith(b"\r\n"):
            body_bytes = body_bytes[:-2]
        return body_bytes
    return None


def _run_demucs(input_wav: Path, out_dir: Path) -> tuple[Path, Path]:
    """Run Demucs htdemucs and return (vocals.wav, instrumental.wav)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "/opt/karaoke-venv/bin/python",
        "-m",
        "demucs.separate",
        "-n",
        "htdemucs",
        "--two-stems",
        "vocals",
        "-o",
        str(out_dir),
        str(input_wav),
    ]
    LOG.info("demucs cmd: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"demucs failed rc={proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    # demucs writes to <out>/htdemucs/<stem>/{vocals,no_vocals}.wav
    stem = input_wav.stem
    base = out_dir / "htdemucs" / stem
    vocals = base / "vocals.wav"
    instrumental = base / "no_vocals.wav"
    if not vocals.exists() or not instrumental.exists():
        # Fall back: search for any vocals/no_vocals under out_dir.
        cand_v = list(out_dir.rglob("vocals.wav"))
        cand_i = list(out_dir.rglob("no_vocals.wav"))
        if cand_v and cand_i:
            vocals, instrumental = cand_v[0], cand_i[0]
        else:
            raise RuntimeError(
                f"demucs output missing under {out_dir}; found: "
                f"{[p.as_posix() for p in out_dir.rglob('*.wav')]}"
            )
    return vocals, instrumental


def _zip_files(files: list[tuple[str, Path]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in files:
            zf.write(path, arcname=arcname)
    return buf.getvalue()


def _zip_bytes(items: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in items:
            zf.writestr(arcname, data)
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    server_version = "karaoke-vast/0.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        LOG.info("%s - " + fmt, self.address_string(), *args)

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_zip(self, body: bytes, filename: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._send_json(
                200,
                {
                    "status": "ok",
                    "gpu": _gpu_available(),
                    "service": "karaoke-vast",
                },
            )
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/demucs":
            self._handle_demucs()
        elif self.path == "/whisper":
            self._handle_whisper()
        else:
            self._send_json(404, {"error": "not found"})

    # ---- handlers ----------------------------------------------------------

    def _read_file(self) -> Optional[bytes]:
        ctype = self.headers.get("Content-Type", "")
        clen = int(self.headers.get("Content-Length", "0") or 0)
        if clen <= 0:
            return None
        if "multipart/form-data" in ctype:
            return _parse_multipart(self.rfile, ctype, clen)
        # Fallback: raw body upload.
        return self.rfile.read(clen)

    def _handle_demucs(self) -> None:
        data = self._read_file()
        if not data:
            self._send_json(400, {"error": "empty body / no file"})
            return
        with _GPU_LOCK:
            with tempfile.TemporaryDirectory(prefix="kar-demucs-") as tmp:
                tmp_path = Path(tmp)
                in_wav = tmp_path / "input.wav"
                in_wav.write_bytes(data)
                out_dir = tmp_path / "out"
                try:
                    vocals, instrumental = _run_demucs(in_wav, out_dir)
                except Exception as exc:  # noqa: BLE001
                    LOG.exception("demucs failed")
                    self._send_json(500, {"error": str(exc)})
                    return
                payload = _zip_files(
                    [
                        ("vocals.wav", vocals),
                        ("instrumental.wav", instrumental),
                    ]
                )
        self._send_zip(payload, "demucs.zip")

    def _handle_whisper(self) -> None:
        data = self._read_file()
        if not data:
            self._send_json(400, {"error": "empty body / no file"})
            return
        with _GPU_LOCK:
            with tempfile.TemporaryDirectory(prefix="kar-whisper-") as tmp:
                tmp_path = Path(tmp)
                in_wav = tmp_path / "input.wav"
                in_wav.write_bytes(data)
                try:
                    model = _get_whisper()
                    segments_iter, info = model.transcribe(
                        str(in_wav),
                        beam_size=5,
                        vad_filter=True,
                        word_timestamps=True,
                    )
                    segments = []
                    text_lines: list[str] = []
                    for seg in segments_iter:
                        seg_dict = {
                            "start": seg.start,
                            "end": seg.end,
                            "text": seg.text,
                        }
                        if seg.words:
                            seg_dict["words"] = [
                                {
                                    "start": w.start,
                                    "end": w.end,
                                    "word": w.word,
                                    "probability": w.probability,
                                }
                                for w in seg.words
                            ]
                        segments.append(seg_dict)
                        if seg.text:
                            text_lines.append(seg.text.strip())
                    lyrics_json = {
                        "language": info.language,
                        "language_probability": info.language_probability,
                        "duration": info.duration,
                        "segments": segments,
                    }
                except Exception as exc:  # noqa: BLE001
                    LOG.exception("whisper failed")
                    self._send_json(500, {"error": str(exc)})
                    return
        body = _zip_bytes(
            [
                ("lyrics.txt", "\n".join(text_lines).encode("utf-8")),
                ("lyrics.json", json.dumps(lyrics_json, ensure_ascii=False, indent=2).encode("utf-8")),
            ]
        )
        self._send_zip(body, "whisper.zip")


def main() -> int:
    LOG.info("starting karaoke-vast HTTP server on %s:%s (gpu=%s)", HOST, PORT, _gpu_available())
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        LOG.info("shutting down")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
