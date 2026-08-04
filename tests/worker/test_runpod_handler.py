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


def test_aligned_lrc_emits_enhanced_word_tags():
    """Enhanced LRC (#218): every kept line = ``[start]`` + ``<start>word`` per
    word + one trailing ``<end>`` tag (last word's end). The low-confidence
    middle line is dropped whole (line-level, #149) — no partial/stray tags."""
    lrc, _scores = handler._word_timestamps_to_lrc(WORDS, TEXT, stride=STRIDE_MS)
    assert lrc == (
        "[00:01.00]<00:01.00>hello <00:01.60>there <00:02.00>\n"
        "[00:40.00]<00:40.00>closing <00:40.60>words <00:41.00>"
    )
    # the absent canonical verse is gone as a whole line — not a stray tag.
    assert "missing" not in lrc and "verse" not in lrc


def test_aligned_lrc_drops_low_confidence_line():
    lrc, _scores = handler._word_timestamps_to_lrc(WORDS, TEXT, stride=STRIDE_MS)
    # two kept lines, middle (low-confidence) line dropped whole.
    assert lrc.splitlines() == [
        "[00:01.00]<00:01.00>hello <00:01.60>there <00:02.00>",
        "[00:40.00]<00:40.00>closing <00:40.60>words <00:41.00>",
    ]


def test_aligned_lrc_without_stride_keeps_all_lines():
    """No stride (caller didn't pass one) → no confidence check; all three lines
    kept, each with Enhanced-LRC word tags + trailing end tag."""
    lrc, _scores = handler._word_timestamps_to_lrc(WORDS, TEXT)
    assert lrc == (
        "[00:01.00]<00:01.00>hello <00:01.60>there <00:02.00>\n"
        "[00:30.00]<00:30.00>missing <00:30.02>verse <00:30.04>line <00:30.06>\n"
        "[00:40.00]<00:40.00>closing <00:40.60>words <00:41.00>"
    )


def test_aligned_lrc_missing_scores_never_drops():
    """Old aligner output without ``score`` keys is never filtered — the middle
    line survives, carrying its per-word Enhanced-LRC tags."""
    words = [_wt(w["start"], w["end"]) for w in WORDS]
    lrc, _scores = handler._word_timestamps_to_lrc(words, TEXT, stride=STRIDE_MS)
    assert (
        "[00:30.00]<00:30.00>missing <00:30.02>verse <00:30.04>line <00:30.06>"
        in lrc.splitlines()
    )


def test_aligned_lrc_all_lines_dropped_returns_empty():
    """A wholly-garbage alignment yields "" — the handler then omits
    ``aligned_lrc`` and the coordinator falls back to the Whisper floor."""
    words = [_wt(10.0 + i * 0.02, 10.02 + i * 0.02, -40.0) for i in range(7)]
    assert handler._word_timestamps_to_lrc(words, TEXT, stride=STRIDE_MS)[0] == ""


def test_aligned_lrc_threshold_env_override(monkeypatch):
    """KARAOKE_ALIGN_MIN_AVG_LOGPROB tunes the floor without a code change."""
    monkeypatch.setenv("KARAOKE_ALIGN_MIN_AVG_LOGPROB", "-0.1")
    strict = _load_handler()
    # Even the confidently-aligned lines (≈ -0.4/frame) fall below -0.1.
    assert strict._word_timestamps_to_lrc(WORDS, TEXT, stride=STRIDE_MS)[0] == ""


def test_aligned_lrc_tokenization_drift_disables_word_tags():
    """Timestamp count ≠ text word count (preprocess/romanize token drift) →
    the positional word↔timestamp mapping is untrustworthy anywhere, so NO
    word tags are emitted at all: every line stays plain, timed at its first
    aligned word (tail lines with no words left keep the pre-#149 behavior,
    timed at the end of the last aligned word). Consumers fall back to the
    linear line wipe."""
    words = [_wt(1.0, 1.5, -10.0), _wt(1.6, 2.0, -8.0)]
    lrc, _scores = handler._word_timestamps_to_lrc(words, TEXT, stride=STRIDE_MS)
    assert lrc == (
        "[00:01.00]hello there\n"
        "[00:02.00]missing verse line\n"
        "[00:02.00]closing words"
    )
    assert "<" not in lrc


# ---------------------------------------------------------------------------
# _transcribe: music-tuned faster-whisper kwargs (#218)
# ---------------------------------------------------------------------------
class _FakeWord:
    def __init__(self, start, end, word, probability):
        self.start = start
        self.end = end
        self.word = word
        self.probability = probability


class _FakeSegment:
    def __init__(self, start, end, text, words=None):
        self.start = start
        self.end = end
        self.text = text
        self.words = words


class _FakeInfo:
    def __init__(self, language, language_probability, duration):
        self.language = language
        self.language_probability = language_probability
        self.duration = duration


class _RecordingWhisper:
    """Stand-in for faster_whisper.WhisperModel: records transcribe() /
    detect_language() calls and returns minimal shapes. ``detect`` configures
    the probe result — a (language, probability) pair or an Exception."""

    def __init__(self, detect=("en", 0.3)):
        self.calls = []
        self.detect_calls = []
        self._detect = detect

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        seg = _FakeSegment(
            0.0, 1.0, " hello", [_FakeWord(0.0, 0.5, "hello", 0.9)]
        )
        return iter([seg]), _FakeInfo("ru", 0.86, 1.0)

    def detect_language(self, audio, **kwargs):
        self.detect_calls.append((audio, kwargs))
        if isinstance(self._detect, Exception):
            raise self._detect
        lang, prob = self._detect
        return lang, prob, [(lang, prob)]


def test_transcribe_passes_music_tuned_kwargs(monkeypatch, tmp_path):
    """#218: repetition-collapse fix + music-tuned VAD / hallucination guards
    are passed through to faster-whisper, and auto language detection is kept."""
    fake = _RecordingWhisper()
    monkeypatch.setattr(handler, "_get_whisper", lambda: fake)
    wav = tmp_path / "vocals.wav"
    wav.write_bytes(b"\x00")

    lyrics_txt, lyrics_json = handler._transcribe(wav)

    assert len(fake.calls) == 1
    _, kwargs = fake.calls[0]
    # the main fix: never condition on prior text (repetition-collapse).
    assert kwargs["condition_on_previous_text"] is False
    # kept knobs.
    assert kwargs["beam_size"] == 5
    assert kwargs["word_timestamps"] is True
    # tuned VAD for long intra-line gaps on a separated vocal stem.
    assert kwargs["vad_filter"] is True
    assert kwargs["vad_parameters"]["min_silence_duration_ms"] == 500
    assert kwargs["vad_parameters"]["speech_pad_ms"] == 400
    # temperature fallback ladder + music hallucination guards.
    assert kwargs["temperature"] == [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    assert kwargs["compression_ratio_threshold"] == 2.4
    assert kwargs["log_prob_threshold"] == -1.0
    assert kwargs["no_speech_threshold"] == 0.6
    assert kwargs["hallucination_silence_threshold"] == 2.0
    # no hint → language=None keeps auto-detect, hardened over several
    # windows so one anglophone adlib can't lock the whole file (#260) —
    # and no detection probe runs.
    assert kwargs["language"] is None
    assert kwargs["language_detection_segments"] == 4
    assert fake.detect_calls == []
    # sane output shape passes through.
    assert lyrics_txt == "hello"
    assert lyrics_json["language"] == "ru"
    assert lyrics_json["segments"][0]["words"][0]["word"] == "hello"


def test_transcribe_hint_wins_on_low_confidence_detection(monkeypatch, tmp_path):
    """#260: with a hint and a LOW-confidence probe (the job-187 shape:
    ``en`` at p<0.6), the hint decides — the fix for a Hebrew stem decoded as
    transliterated-Latin gibberish."""
    fake = _RecordingWhisper(detect=("en", 0.456))
    monkeypatch.setattr(handler, "_get_whisper", lambda: fake)
    wav = tmp_path / "vocals.wav"
    wav.write_bytes(b"\x00")

    handler._transcribe(wav, language="he")

    assert len(fake.detect_calls) == 1
    _, probe_kwargs = fake.detect_calls[0]
    assert probe_kwargs["language_detection_segments"] == 4
    assert len(fake.calls) == 1
    _, kwargs = fake.calls[0]
    assert kwargs["language"] == "he"


def test_transcribe_confident_detection_overrides_hint(monkeypatch, tmp_path):
    """#260 review: the hint comes from the TITLE script; a translated-lyrics
    upload (native-script title over foreign audio) must not be forced into
    the title's language when the audio clearly says otherwise."""
    fake = _RecordingWhisper(detect=("en", 0.92))
    monkeypatch.setattr(handler, "_get_whisper", lambda: fake)
    wav = tmp_path / "vocals.wav"
    wav.write_bytes(b"\x00")

    handler._transcribe(wav, language="he")

    _, kwargs = fake.calls[0]
    assert kwargs["language"] == "en"


def test_transcribe_probe_failure_keeps_hint(monkeypatch, tmp_path):
    """The detection probe is best-effort — an error keeps the hint."""
    fake = _RecordingWhisper(detect=RuntimeError("no cuda"))
    monkeypatch.setattr(handler, "_get_whisper", lambda: fake)
    wav = tmp_path / "vocals.wav"
    wav.write_bytes(b"\x00")

    handler._transcribe(wav, language="he")

    _, kwargs = fake.calls[0]
    assert kwargs["language"] == "he"


def test_handler_event_passes_whisper_lang_to_transcribe(monkeypatch):
    """End-to-end payload wiring (#260): the ``whisper_lang`` key of
    ``event["input"]`` reaches ``_transcribe(language=...)`` — pinning the key
    name the coordinator's runpod_client sends."""
    import base64 as _b64

    recorded = {}

    def fake_transcribe(target, language=None):
        recorded["language"] = language
        return "txt", {"language": language or "xx", "segments": []}

    monkeypatch.setattr(handler, "_transcribe", fake_transcribe)
    monkeypatch.setattr(handler, "_gpu_model_name", lambda: "cpu")

    out = handler.handler(
        {
            "input": {
                "audio_base64": _b64.b64encode(b"RIFFxxxx").decode(),
                "mode": "whisper",
                "whisper_lang": "he",
            }
        }
    )
    assert recorded["language"] == "he"
    assert out["lyrics_txt"] == "txt"


def test_vad_veto_drops_line_on_instrumental():
    """#247: a line whose aligned window barely overlaps voiced audio is
    dropped; lines inside voiced regions survive. VAD=None -> no veto."""
    text = "sung line\nghost line"
    words = [
        _wt(1.0, 1.5, -0.4),
        _wt(1.6, 2.0, -0.4),
        _wt(30.0, 30.6, -0.5),
        _wt(30.7, 31.2, -0.5),
    ]
    voiced = [(0.5, 3.0)]  # only the first window is sung
    lrc, scores = handler._word_timestamps_to_lrc(
        words, text, stride=STRIDE_MS, voiced=voiced
    )
    assert "sung line" in lrc.replace("<", " ").replace(">", " ") or "sung" in lrc
    assert "ghost" not in lrc
    assert len(scores) == 1
    # no VAD info -> both lines kept
    lrc2, _ = handler._word_timestamps_to_lrc(words, text, stride=STRIDE_MS, voiced=None)
    assert "ghost" in lrc2


def test_voiced_overlap_math():
    regions = [(10.0, 20.0), (30.0, 35.0)]
    assert handler._voiced_overlap(12.0, 18.0, regions) == 1.0
    assert handler._voiced_overlap(0.0, 10.0, regions) == 0.0
    assert abs(handler._voiced_overlap(15.0, 25.0, regions) - 0.5) < 1e-9


def test_vad_veto_ignores_internal_instrumental_gap():
    """A genuinely sung line with a long instrumental gap BETWEEN its words
    (or an absorbed-silence span) is kept: overlap is per-word, so the gap
    never enters the denominator."""
    text = "gapped line"
    words = [_wt(1.0, 1.5, -0.4), _wt(20.0, 20.5, -0.4)]  # 18.5 s apart
    voiced = [(0.8, 1.7), (19.8, 20.7)]  # each word sits in voice
    lrc, _ = handler._word_timestamps_to_lrc(words, text, stride=STRIDE_MS, voiced=voiced)
    assert "gapped" in lrc
