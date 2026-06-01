"""Per-job ephemeral cookies in the worker pipeline (issue #77).

Covers, without invoking real yt-dlp:
  - ``_ytdlp_aux_args`` writes a per-job blob to a ``0600`` temp, uses it as
    ``--cookies``, and deletes it on context exit;
  - per-job blob takes precedence over the central jar, and absence falls back;
  - ``_download_audio`` forwards the per-job blob;
  - the in-memory registry stash/pop/discard semantics.
"""
from __future__ import annotations

from pathlib import Path

from karaoke import config
from karaoke.worker import job_cookies, pipeline
from karaoke.worker.pipeline import _ytdlp_aux_args

_BLOB = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tperjob-val\n"
_CENTRAL = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tSID\tCENTRAL-val\n"


def _settings(**overrides) -> config.Settings:
    return config.Settings(**overrides)


def test_aux_args_writes_perjob_blob_0600_and_cleans_up():
    captured: Path | None = None
    with _ytdlp_aux_args(_settings(ytdlp_cookies_file=""), cookies_blob=_BLOB) as args:
        assert "--remote-components" in args  # EJS solver still emitted
        assert "--cookies" in args
        copy = Path(args[args.index("--cookies") + 1])
        captured = copy
        assert copy.is_file()
        # Cookies are secrets: the per-job temp must be owner-only.
        assert oct(copy.stat().st_mode)[-3:] == "600"
        assert copy.read_text() == _BLOB  # blob already ends with a newline
    # Temp removed on context exit (download done).
    assert captured is not None and not captured.exists()


def test_perjob_blob_takes_precedence_over_central_jar(tmp_path):
    central = tmp_path / "youtube-cookies.txt"
    central.write_text(_CENTRAL)
    s = _settings(ytdlp_cookies_file=str(central))
    with _ytdlp_aux_args(s, cookies_blob=_BLOB) as args:
        # Exactly one --cookies, pointing at the per-job blob, not the jar.
        assert args.count("--cookies") == 1
        copy = Path(args[args.index("--cookies") + 1])
        body = copy.read_text()
        assert "perjob-val" in body
        assert "CENTRAL-val" not in body
        assert copy != central


def test_no_blob_falls_back_to_central_jar(tmp_path):
    central = tmp_path / "youtube-cookies.txt"
    central.write_text(_CENTRAL)
    s = _settings(ytdlp_cookies_file=str(central))
    with _ytdlp_aux_args(s, cookies_blob=None) as args:
        copy = Path(args[args.index("--cookies") + 1])
        assert "CENTRAL-val" in copy.read_text()


def test_no_blob_no_jar_emits_no_cookies():
    with _ytdlp_aux_args(_settings(ytdlp_cookies_file=""), cookies_blob=None) as args:
        assert "--cookies" not in args


def test_blank_blob_is_ignored():
    with _ytdlp_aux_args(_settings(ytdlp_cookies_file=""), cookies_blob="   \n") as args:
        assert "--cookies" not in args


def test_download_passes_perjob_cookies_and_cleans_up(tmp_path, monkeypatch):
    dest = tmp_path / "source.audio"
    calls: list[list[str]] = []

    def fake_run(cmd, *, timeout=None):
        calls.append(cmd)
        dest.write_bytes(b"audio")

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_sleep", lambda s: None)
    pipeline._download_audio(
        "https://yt/x", dest, _settings(ytdlp_cookies_file=""), cookies_blob=_BLOB
    )
    cmd = calls[0]
    assert "--cookies" in cmd
    cookie_temp = Path(cmd[cmd.index("--cookies") + 1])
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
