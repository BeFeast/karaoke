"""Unit tests for the RunPod handler's stem-mapping helpers (#98) and the
force-aligned LRC synthesis with its per-line confidence filter (#149).

``docker/runpod/handler.py`` lives outside the installed ``karaoke`` package and
imports every heavy/GPU dependency lazily (torch, audio_separator,
faster_whisper, ctc_forced_aligner, runpod are all imported *inside* functions),
so loading the module here pulls only the stdlib — no GPU deps, no
``importorskip`` needed. We load it by path with importlib. The aligner output
is mocked as plain word-timestamp dicts — the same shape
``ctc_forced_aligner.postprocess_results`` returns.
"""
import importlib.util
from pathlib import Path

import pytest

_HANDLER_PATH = Path(__file__).resolve().parents[2] / "docker" / "runpod" / "handler.py"


def _load_handler():
    spec = importlib.util.spec_from_file_location(
        "karaoke_runpod_handler", _HANDLER_PATH
    )
    assert spec and spec.loader, f"cannot load handler from {_HANDLER_PATH}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


handler = _load_handler()


def test_pick_stem_matches_default_audio_separator_names():
    paths = [
        Path("song_(Vocals)_model_bs_roformer.wav"),
        Path("song_(Instrumental)_model_bs_roformer.wav"),
    ]
    assert handler._pick_stem(paths, "vocal").name.startswith("song_(Vocals)")
    assert handler._pick_stem(paths, "instrument").name.startswith("song_(Instrumental)")


def test_pick_stem_matches_deterministic_names():
    paths = [Path("/tmp/out/vocals.wav"), Path("/tmp/out/instrumental.wav")]
    assert handler._pick_stem(paths, "vocal") == Path("/tmp/out/vocals.wav")
    assert handler._pick_stem(paths, "instrument") == Path("/tmp/out/instrumental.wav")


def test_pick_stem_returns_none_on_no_match():
    # an unrelated stem (e.g. a drums/other output) and the empty case
    assert handler._pick_stem([Path("drums.wav"), Path("other.wav")], "vocal") is None
    assert handler._pick_stem([], "instrument") is None


class _FakeSeparator:
    """Stand-in for audio_separator.Separator: writes the given basenames into
    whatever output_dir is set, and returns them (as audio-separator does)."""

    def __init__(self, basenames):
        self._basenames = basenames
        self.output_dir = None
        self.model_instance = None

    def separate(self, audio_file_path, custom_output_names=None):
        out = Path(self.output_dir)
        for name in self._basenames:
            (out / name).write_bytes(b"\x00")
        return list(self._basenames)


def test_run_separation_maps_vocals_and_instrumental(tmp_path, monkeypatch):
    monkeypatch.setattr(
        handler,
        "_get_separator",
        lambda: _FakeSeparator(["vocals.wav", "instrumental.wav"]),
    )
    inp = tmp_path / "in.wav"
    inp.write_bytes(b"\x00")
    out_dir = tmp_path / "out"
    vocals, instrumental = handler._run_separation(inp, out_dir)
    assert vocals.name == "vocals.wav" and vocals.exists()
    assert instrumental.name == "instrumental.wav" and instrumental.exists()


def test_run_separation_raises_on_unrecognized_stems(tmp_path, monkeypatch):
    # separator yields no vocals/instrumental-named output -> must raise, not
    # return a wrong/empty tuple.
    monkeypatch.setattr(
        handler, "_get_separator", lambda: _FakeSeparator(["drums.wav"])
    )
    inp = tmp_path / "in.wav"
    inp.write_bytes(b"\x00")
    with pytest.raises(RuntimeError, match="missing vocals/instrumental"):
        handler._run_separation(inp, tmp_path / "out")


# ---------------------------------------------------------------------------
# force-aligned LRC synthesis + per-line confidence filter (#149)
# ---------------------------------------------------------------------------
STRIDE_MS = 20.0  # MMS emission frame stride used by the fixtures below


def _wt(start: float, end: float, score: float | None = None) -> dict:
    """One ctc-forced-aligner word-timestamp dict (postprocess_results shape)."""
    d: dict = {"text": "w", "start": start, "end": end}
    if score is not None:
        d["score"] = score
    return d


# "hello there" / "missing verse line" / "closing words": the middle line is
# the canonical-text verse absent from this audio edit — monotonic CTC squeezes
# its words into ~1-frame spans with deeply negative summed log-probs, while
# the surrounding (actually sung) lines average ≈ -0.4/frame.
TEXT = "hello there\nmissing verse line\nclosing words"
WORDS = [
    _wt(1.0, 1.5, -10.0),
    _wt(1.6, 2.0, -8.0),
    _wt(30.0, 30.02, -30.0),
    _wt(30.02, 30.04, -30.0),
    _wt(30.04, 30.06, -30.0),
    _wt(40.0, 40.5, -12.0),
    _wt(40.6, 41.0, -10.0),
]


def test_aligned_lrc_drops_low_confidence_line():
    lrc = handler._word_timestamps_to_lrc(WORDS, TEXT, stride=STRIDE_MS)
    assert lrc == "[00:01.00]hello there\n[00:40.00]closing words"


def test_aligned_lrc_without_stride_keeps_all_lines():
    """No stride (caller didn't pass one) → no confidence check, pre-#149 shape."""
    lrc = handler._word_timestamps_to_lrc(WORDS, TEXT)
    assert lrc == (
        "[00:01.00]hello there\n[00:30.00]missing verse line\n[00:40.00]closing words"
    )


def test_aligned_lrc_missing_scores_never_drops():
    """Old aligner output without ``score`` keys is never filtered."""
    words = [_wt(w["start"], w["end"]) for w in WORDS]
    lrc = handler._word_timestamps_to_lrc(words, TEXT, stride=STRIDE_MS)
    assert "missing verse line" in lrc


def test_aligned_lrc_all_lines_dropped_returns_empty():
    """A wholly-garbage alignment yields "" — the handler then omits
    ``aligned_lrc`` and the coordinator falls back to the Whisper floor."""
    words = [_wt(10.0 + i * 0.02, 10.02 + i * 0.02, -40.0) for i in range(7)]
    assert handler._word_timestamps_to_lrc(words, TEXT, stride=STRIDE_MS) == ""


def test_aligned_lrc_threshold_env_override(monkeypatch):
    """KARAOKE_ALIGN_MIN_AVG_LOGPROB tunes the floor without a code change."""
    monkeypatch.setenv("KARAOKE_ALIGN_MIN_AVG_LOGPROB", "-0.1")
    strict = _load_handler()
    # Even the confidently-aligned lines (≈ -0.4/frame) fall below -0.1.
    assert strict._word_timestamps_to_lrc(WORDS, TEXT, stride=STRIDE_MS) == ""


def test_aligned_lrc_tokenization_drift_keeps_tail_lines():
    """More text words than aligned timestamps: tail lines are kept, timed at
    the end of the last aligned word (pre-#149 behavior preserved)."""
    words = [_wt(1.0, 1.5, -10.0), _wt(1.6, 2.0, -8.0)]
    lrc = handler._word_timestamps_to_lrc(words, TEXT, stride=STRIDE_MS)
    assert lrc == (
        "[00:01.00]hello there\n[00:02.00]missing verse line\n[00:02.00]closing words"
    )
