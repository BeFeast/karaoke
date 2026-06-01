"""Tests for the yt-dlp anti-bot wiring (issue #68).

Covers, without invoking real yt-dlp:
  - bgutil PO-token extractor-args construction (separate namespace, base-url
    normalization, opt-out when unset);
  - bot-check fingerprint detection;
  - the download stage's exponential backoff + retry behavior, by patching the
    ``_run`` subprocess wrapper and ``_sleep``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from karaoke import config
from karaoke.worker import pipeline
from karaoke.worker.pipeline import (
    PipelineError,
    _is_bot_check,
    _pot_base_url,
    _ytdlp_aux_args,
    _ytdlp_extractor_args,
)


# ---------------------------------------------------------------------------
# extractor-args construction
# ---------------------------------------------------------------------------
def _settings(**overrides) -> config.Settings:
    return config.Settings(**overrides)


def test_extractor_args_always_sets_player_client_when_no_pot():
    s = _settings(pot_provider_base_url="")
    args = _ytdlp_extractor_args(s)
    assert args == ["--extractor-args", f"youtube:player_client={pipeline._PLAYER_CLIENTS}"]


def test_extractor_args_adds_bgutil_base_url_as_separate_flag():
    s = _settings(pot_provider_base_url="http://karaoke-pot:4416")
    args = _ytdlp_extractor_args(s)
    # Two distinct --extractor-args flags: different yt-dlp namespaces can't be
    # merged into one string.
    assert args == [
        "--extractor-args", f"youtube:player_client={pipeline._PLAYER_CLIENTS}",
        "--extractor-args", "youtubepot-bgutilhttp:base_url=http://karaoke-pot:4416",
    ]
    assert args.count("--extractor-args") == 2


def test_extractor_args_strips_trailing_slash():
    s = _settings(pot_provider_base_url="http://karaoke-pot:4416/")
    args = _ytdlp_extractor_args(s)
    assert args[-1] == "youtubepot-bgutilhttp:base_url=http://karaoke-pot:4416"


def test_pot_base_url_handles_none_settings_and_blank():
    assert _pot_base_url(None) == ""
    assert _pot_base_url(_settings(pot_provider_base_url="   ")) == ""
    assert _pot_base_url(_settings(pot_provider_base_url="http://x:1/")) == "http://x:1"


def test_default_pot_base_url_points_at_documented_sidecar():
    # The default targets the documented sidecar service name + port.
    assert _settings().pot_provider_base_url == "http://karaoke-pot:4416"


# ---------------------------------------------------------------------------
# EJS solver + cookies aux-args (issue #68)
# ---------------------------------------------------------------------------
def test_aux_args_default_emits_remote_components_only():
    # Default settings, no cookies file on disk → just the EJS solver flag.
    with _ytdlp_aux_args(_settings(ytdlp_cookies_file="")) as args:
        assert args == ["--remote-components", "ejs:github"]


def test_aux_args_none_settings_falls_back_to_ejs_github():
    with _ytdlp_aux_args(None) as args:
        assert args == ["--remote-components", "ejs:github"]


def test_aux_args_empty_remote_components_omits_flag():
    with _ytdlp_aux_args(_settings(ytdlp_remote_components="", ytdlp_cookies_file="")) as args:
        assert args == []


def test_aux_args_injects_writable_cookies_copy_and_cleans_up(tmp_path):
    src = tmp_path / "youtube-cookies.txt"
    src.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tFOO\tbar\n")
    s = _settings(ytdlp_cookies_file=str(src))

    captured: Path | None = None
    with _ytdlp_aux_args(s) as args:
        assert "--remote-components" in args and "ejs:github" in args
        assert "--cookies" in args
        copy = Path(args[args.index("--cookies") + 1])
        captured = copy
        # A *copy*, never the source secret (yt-dlp writes the jar back on close).
        assert copy != src
        assert copy.is_file()
        assert copy.read_text() == src.read_text()
    # Temp copy removed on context exit; the source is untouched.
    assert captured is not None and not captured.exists()
    assert src.is_file()


def test_aux_args_skips_cookies_when_file_missing_or_empty(tmp_path):
    missing = tmp_path / "nope.txt"
    with _ytdlp_aux_args(_settings(ytdlp_cookies_file=str(missing))) as args:
        assert "--cookies" not in args

    empty = tmp_path / "empty.txt"
    empty.write_text("")
    with _ytdlp_aux_args(_settings(ytdlp_cookies_file=str(empty))) as args:
        assert "--cookies" not in args


def test_download_passes_remote_components_and_cookies(tmp_path, monkeypatch):
    src = tmp_path / "youtube-cookies.txt"
    src.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tA\t1\n")
    dest = tmp_path / "source.audio"
    rec = _RunRecorder(["ok"], dest)
    monkeypatch.setattr(pipeline, "_run", rec)
    monkeypatch.setattr(pipeline, "_sleep", lambda s: None)

    pipeline._download_audio("https://yt/x", dest, _settings(ytdlp_cookies_file=str(src)))
    cmd = rec.calls[0]
    assert "--remote-components" in cmd and "ejs:github" in cmd
    assert "--cookies" in cmd
    # The flag points at a writable copy, not the read-only mounted secret.
    assert cmd[cmd.index("--cookies") + 1] != str(src)


# ---------------------------------------------------------------------------
# bot-check fingerprinting
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "msg",
    [
        "ERROR: Sign in to confirm you're not a bot",
        "Sign in to confirm that you're not a bot. This helps protect ...",
        "ERROR: [youtube] confirm you're not a bot",
        "HTTP Error 429: Too Many Requests",
        "ERROR: Too Many Requests",
        "ERROR: This video is not available",
    ],
)
def test_is_bot_check_true(msg):
    assert _is_bot_check(msg) is True


@pytest.mark.parametrize(
    "msg",
    [
        "ERROR: Private video. Sign in if you've been granted access",
        "ERROR: Video unavailable",
        "ffmpeg: command not found",
        "",
        None,
    ],
)
def test_is_bot_check_false(msg):
    assert _is_bot_check(msg) is False


# ---------------------------------------------------------------------------
# download backoff / retry
# ---------------------------------------------------------------------------
class _RunRecorder:
    """Patches ``_run`` to a scripted sequence of outcomes and records calls.

    Each item in ``outcomes`` is either an exception to raise or the sentinel
    ``"ok"`` meaning the call succeeds (and writes the dest file).
    """

    def __init__(self, outcomes, dest: Path):
        self._outcomes = list(outcomes)
        self._dest = dest
        self.calls: list[list[str]] = []

    def __call__(self, cmd, *, timeout=None):
        self.calls.append(cmd)
        outcome = self._outcomes.pop(0)
        if outcome == "ok":
            self._dest.write_bytes(b"audio-bytes")
            return None
        raise outcome


def _bot_err() -> PipelineError:
    return PipelineError(
        "command failed (1): yt-dlp ...\nstderr:\nERROR: Sign in to confirm you're not a bot"
    )


def _other_err() -> PipelineError:
    return PipelineError("command failed (1): yt-dlp ...\nstderr:\nERROR: Private video")


def test_download_succeeds_first_try(tmp_path, monkeypatch):
    dest = tmp_path / "source.audio"
    rec = _RunRecorder(["ok"], dest)
    sleeps: list[float] = []
    monkeypatch.setattr(pipeline, "_run", rec)
    monkeypatch.setattr(pipeline, "_sleep", lambda s: sleeps.append(s))

    out = pipeline._download_audio("https://yt/x", dest, _settings())
    assert out == dest
    assert len(rec.calls) == 1
    assert sleeps == []  # no backoff when it works immediately


def test_download_retries_bot_check_then_succeeds(tmp_path, monkeypatch):
    dest = tmp_path / "source.audio"
    rec = _RunRecorder([_bot_err(), _bot_err(), "ok"], dest)
    sleeps: list[float] = []
    monkeypatch.setattr(pipeline, "_run", rec)
    monkeypatch.setattr(pipeline, "_sleep", lambda s: sleeps.append(s))

    out = pipeline._download_audio("https://yt/x", dest, _settings())
    assert out == dest
    assert len(rec.calls) == 3
    # First two failures each backed off using the documented schedule.
    assert sleeps == list(pipeline._BOT_CHECK_BACKOFF_S[:2])


def test_download_exhausts_backoff_then_raises_actionable(tmp_path, monkeypatch):
    dest = tmp_path / "source.audio"
    outcomes = [_bot_err()] * (len(pipeline._BOT_CHECK_BACKOFF_S) + 1)
    rec = _RunRecorder(outcomes, dest)
    sleeps: list[float] = []
    monkeypatch.setattr(pipeline, "_run", rec)
    monkeypatch.setattr(pipeline, "_sleep", lambda s: sleeps.append(s))

    with pytest.raises(PipelineError) as ei:
        pipeline._download_audio("https://yt/x", dest, _settings())
    msg = str(ei.value)
    # Surfaces an actionable error: this is per-video session auth, not an IP
    # ban, and the fix is logged-in cookies (issue #68).
    assert "bot-check" in msg
    assert "per-video" in msg
    assert "cookies" in msg.lower()
    assert "KARAOKE_YTDLP_COOKIES_FILE" in msg
    # Tried every attempt (initial + one per backoff step).
    assert len(rec.calls) == len(pipeline._BOT_CHECK_BACKOFF_S) + 1
    assert sleeps == list(pipeline._BOT_CHECK_BACKOFF_S)


def test_download_non_bot_error_raises_immediately_no_retry(tmp_path, monkeypatch):
    dest = tmp_path / "source.audio"
    rec = _RunRecorder([_other_err()], dest)
    sleeps: list[float] = []
    monkeypatch.setattr(pipeline, "_run", rec)
    monkeypatch.setattr(pipeline, "_sleep", lambda s: sleeps.append(s))

    with pytest.raises(PipelineError) as ei:
        pipeline._download_audio("https://yt/x", dest, _settings())
    # No retry, no backoff for a non-bot-check failure.
    assert len(rec.calls) == 1
    assert sleeps == []
    assert "bot-check" not in str(ei.value)


def test_download_empty_output_raises(tmp_path, monkeypatch):
    dest = tmp_path / "source.audio"

    def fake_run(cmd, *, timeout=None):
        # "succeeds" but writes nothing.
        return None

    monkeypatch.setattr(pipeline, "_run", fake_run)
    monkeypatch.setattr(pipeline, "_sleep", lambda s: None)
    with pytest.raises(PipelineError, match="produced no audio"):
        pipeline._download_audio("https://yt/x", dest, _settings())


def test_download_passes_pot_extractor_args_to_ytdlp(tmp_path, monkeypatch):
    dest = tmp_path / "source.audio"
    rec = _RunRecorder(["ok"], dest)
    monkeypatch.setattr(pipeline, "_run", rec)
    monkeypatch.setattr(pipeline, "_sleep", lambda s: None)

    pipeline._download_audio("https://yt/x", dest, _settings())
    cmd = rec.calls[0]
    assert "youtubepot-bgutilhttp:base_url=http://karaoke-pot:4416" in cmd
    assert f"youtube:player_client={pipeline._PLAYER_CLIENTS}" in cmd
