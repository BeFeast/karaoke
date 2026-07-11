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
    """#218 Enhanced LRC: surviving lines carry per-word ``<..>`` start tags plus
    a trailing line-end tag; the #149 low-confidence line drop is unchanged."""
    lrc = handler._word_timestamps_to_lrc(WORDS, TEXT, stride=STRIDE_MS)
    assert lrc == (
        "[00:01.00]<00:01.00>hello <00:01.60>there <00:02.00>\n"
        "[00:40.00]<00:40.00>closing <00:40.60>words <00:41.00>"
    )


def test_aligned_lrc_without_stride_keeps_all_lines():
    """No stride (caller didn't pass one) → no confidence check; every line is
    rendered with #218 word tags (the middle line is kept, not dropped)."""
    lrc = handler._word_timestamps_to_lrc(WORDS, TEXT)
    assert lrc == (
        "[00:01.00]<00:01.00>hello <00:01.60>there <00:02.00>\n"
        "[00:30.00]<00:30.00>missing <00:30.02>verse <00:30.04>line <00:30.06>\n"
        "[00:40.00]<00:40.00>closing <00:40.60>words <00:41.00>"
    )


def test_aligned_lrc_word_tag_time_format_matches_line_tag():
    """The inline ``<mm:ss.xx>`` word tags use the exact time shape of the
    ``[mm:ss.xx]`` line tag (2-digit centiseconds), per the #218 contract."""
    assert handler._fmt_word_timestamp(1.6) == "<00:01.60>"
    assert handler._fmt_word_timestamp(65.05) == "<01:05.05>"
    # bare form (no delimiters) shared by both tag kinds
    assert handler._fmt_lrc_time(1.6) == "00:01.60"
    assert handler._fmt_lrc_timestamp(1.6) == "[00:01.60]"


def test_aligned_lrc_missing_scores_never_drops():
    """Old aligner output without ``score`` keys is never filtered; the middle
    line survives and is rendered with #218 word tags."""
    words = [_wt(w["start"], w["end"]) for w in WORDS]
    lrc = handler._word_timestamps_to_lrc(words, TEXT, stride=STRIDE_MS)
    assert "[00:30.00]<00:30.00>missing <00:30.02>verse <00:30.04>line <00:30.06>" in lrc


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
    """More text words than aligned timestamps: the fully-aligned first line
    gets #218 word tags, while the tail lines (out of timestamps) stay plain
    (word-less) lines timed at the end of the last aligned word. Mixed
    word-tagged / plain lines are valid Enhanced LRC (#218 contract)."""
    words = [_wt(1.0, 1.5, -10.0), _wt(1.6, 2.0, -8.0)]
    lrc = handler._word_timestamps_to_lrc(words, TEXT, stride=STRIDE_MS)
    assert lrc == (
        "[00:01.00]<00:01.00>hello <00:01.60>there <00:02.00>\n"
        "[00:02.00]missing verse line\n"
        "[00:02.00]closing words"
    )


# ---------------------------------------------------------------------------
# _transcribe kwargs — repetition-collapse + music-tuned guards (#218)
# ---------------------------------------------------------------------------
class _FakeWhisperInfo:
    """Stand-in for faster-whisper's transcribe ``info`` (evidence job shape)."""

    language = "ru"
    language_probability = 0.86
    duration = 203.0


class _FakeWhisperWord:
    def __init__(self, start, end, word, probability):
        self.start = start
        self.end = end
        self.word = word
        self.probability = probability


class _FakeWhisperSegment:
    def __init__(self, start, end, text, words=None):
        self.start = start
        self.end = end
        self.text = text
        self.words = words


class _FakeWhisperModel:
    """Records the transcribe(...) call and returns a canned (segments, info)."""

    def __init__(self, segments=None):
        self.calls = []
        self._segments = segments or []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter(self._segments), _FakeWhisperInfo()


def test_transcribe_kwargs_repetition_collapse_guards(tmp_path, monkeypatch):
    """``_transcribe`` calls faster-whisper with the #218 anti-collapse kwargs:
    ``condition_on_previous_text=False``, tuned VAD params, the temperature
    fallback ladder and the music-tuned hallucination guards — while keeping
    ``word_timestamps``/``beam_size`` and auto language detection."""
    fake = _FakeWhisperModel()
    monkeypatch.setattr(handler, "_get_whisper", lambda: fake)
    wav = tmp_path / "vocals.wav"
    wav.write_bytes(b"\x00")

    lyrics_txt, lyrics_json = handler._transcribe(wav)

    assert len(fake.calls) == 1
    _audio, kwargs = fake.calls[0]
    # The main fix (repetition-collapse) + music-tuned guards.
    assert kwargs["condition_on_previous_text"] is False
    assert kwargs["vad_filter"] is True
    assert kwargs["vad_parameters"] == {
        "min_silence_duration_ms": 500,
        "speech_pad_ms": 400,
    }
    assert kwargs["temperature"] == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    assert kwargs["compression_ratio_threshold"] == 2.4
    assert kwargs["log_prob_threshold"] == -1.0
    assert kwargs["no_speech_threshold"] == 0.6
    assert kwargs["hallucination_silence_threshold"] == 2.0
    # Unchanged knobs: per-word timing, beam search, auto language detect.
    assert kwargs["word_timestamps"] is True
    assert kwargs["beam_size"] == 5
    assert "language" not in kwargs  # auto-detect, not forced
    # Empty transcript still yields a well-formed lyrics_json.
    assert lyrics_json["language"] == "ru"
    assert lyrics_json["language_probability"] == 0.86
    assert lyrics_json["segments"] == []
    assert lyrics_txt == ""


def test_transcribe_maps_segments_and_words(tmp_path, monkeypatch):
    """Segment/word mapping is preserved: each segment carries stripped text and
    a per-word list, and lyrics_txt joins the stripped segment texts."""
    seg = _FakeWhisperSegment(
        start=1.0,
        end=2.5,
        text=" hello there ",
        words=[
            _FakeWhisperWord(1.0, 1.5, "hello", 0.9),
            _FakeWhisperWord(1.6, 2.0, "there", 0.8),
        ],
    )
    fake = _FakeWhisperModel(segments=[seg])
    monkeypatch.setattr(handler, "_get_whisper", lambda: fake)
    wav = tmp_path / "vocals.wav"
    wav.write_bytes(b"\x00")

    lyrics_txt, lyrics_json = handler._transcribe(wav)

    assert lyrics_txt == "hello there"
    assert lyrics_json["segments"] == [
        {
            "start": 1.0,
            "end": 2.5,
            "text": " hello there ",
            "words": [
                {"start": 1.0, "end": 1.5, "word": "hello", "probability": 0.9},
                {"start": 1.6, "end": 2.0, "word": "there", "probability": 0.8},
            ],
        }
    ]
