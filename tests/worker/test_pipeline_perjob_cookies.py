"""Per-job ephemeral cookies in the worker pipeline (issue #77).

Per-job client-supplied cookies are the ONLY cookie path (#132 retired the
central jar). Covers, without invoking real yt-dlp:
  - ``_ytdlp_aux_args`` writes a per-job blob to a ``0600`` temp, uses it as
    the single ``--cookies``, and deletes it on context exit;
  - no blob → no ``--cookies`` arg at all;
  - ``_download_audio`` forwards the per-job blob;
  - the in-memory registry stash/pop/discard semantics.
"""
from __future__ import annotations

from pathlib import Path

from karaoke import config
from karaoke.worker import job_cookies, pipeline
from karaoke.worker.pipeline import _ytdlp_aux_args

_BLOB = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tperjob-val\n"


def _settings(**overrides) -> config.Settings:
    return config.Settings(**overrides)


def test_aux_args_writes_perjob_blob_0600_and_cleans_up():
    captured: Path | None = None
    with _ytdlp_aux_args(_settings(), cookies_blob=_BLOB) as args:
        assert "--remote-components" in args  # EJS solver still emitted
        # Exactly one --cookies, pointing at a per-job temp (never a shared jar).
        assert args.count("--cookies") == 1
        copy = Path(args[args.index("--cookies") + 1])
        captured = copy
        assert copy.is_file()
        assert copy.name.startswith("ytc-job-") and copy.name.endswith(".txt")
        # Cookies are secrets: the per-job temp must be owner-only.
        assert oct(copy.stat().st_mode)[-3:] == "600"
        assert copy.read_text() == _BLOB  # blob already ends with a newline
    # Temp removed on context exit (download done).
    assert captured is not None and not captured.exists()


def test_no_blob_emits_no_cookies():
    with _ytdlp_aux_args(_settings(), cookies_blob=None) as args:
        assert "--cookies" not in args


def test_blank_blob_is_ignored():
    with _ytdlp_aux_args(_settings(), cookies_blob="   \n") as args:
        assert "--cookies" not in args


def test_download_passes_perjob_cookies_and_cleans_up(tmp_path, monkeypatch):
    dest = tmp_path / "source.audio"
    calls: list[list[str]] = []

    def fake_run(cmd, *, timeout=None):
        calls.append(cmd)
        dest.write_bytes(b"audio")

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_sleep", lambda s: None)
    pipeline._download_audio("https://yt/x", dest, _settings(), cookies_blob=_BLOB)
    cmd = calls[0]
    assert cmd.count("--cookies") == 1
    cookie_temp = Path(cmd[cmd.index("--cookies") + 1])
    assert cookie_temp.name.startswith("ytc-job-") and cookie_temp.name.endswith(".txt")
    # Temp is cleaned up by the time the context exited (download finished).
    assert not cookie_temp.exists()


def test_registry_stash_pop_discard():
    job_cookies._PENDING.clear()
    job_cookies.stash(7, "blob7")
    assert job_cookies.pop(7) == "blob7"
    assert job_cookies.pop(7) is None  # popped → gone, not re-poppable
    job_cookies.stash(8, "blob8")
    job_cookies.discard(8)
    assert job_cookies.pop(8) is None
    assert job_cookies._PENDING == {}
