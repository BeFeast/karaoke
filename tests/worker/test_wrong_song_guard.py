"""Wrong lyrics for the audio: the curated text must match what is sung.

Regression fixtures from production (tests/fixtures/lyrics/):

* job 144 — «Little Big — Конь» whose stems were a Creep job's (the July R2
  key collision, #250). The aligner "aligned" the «Конь» record onto Creep's
  vocal stem and the #241 coverage fallback shipped it: Russian words over an
  English song.
* job 148 — the real «Конь» TV cut: right text, studio timings that miss, a
  transcript that collapsed to two segments and writes ё as е.
* job 612 — an Italian song decoded to four Spanish words; sparse ASR
  cannot veto an otherwise usable reference.

The rules under test hold for any language: a record that already failed a
check (curated timing coverage, duration) needs the transcript's confirmation
when a transcript can judge it; a confident transcript in another language
than the title's script is still evidence; stems that are not the submitted
audio fail the job.
"""

from __future__ import annotations

import json
import re
import shutil
import wave
from pathlib import Path

import pytest

from karaoke.textnorm import bigram_agreement, fold_optional_letters
from karaoke.worker.lyrics import (
    SOURCE_FORCED_ALIGNED,
    SOURCE_LRCLIB_PLAIN,
    SOURCE_LRCLIB_SYNCED,
    LyricsResult,
    lrc_to_plain,
)
from karaoke.worker.lyrics_evidence import asr_is_sparse
from karaoke.worker.pipeline import (
    PipelineError,
    _check_stems_match_mix,
    _reference_language,
    _resolve_lyrics,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "lyrics"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _stage(tmp_path: Path, fx: dict, *, asr: dict | None = None) -> tuple[Path, Path, Path, Path]:
    """Lay the fixture out like a job: work/aligned.lrc (+ scores), work/lyrics.json,
    work/lyrics.txt (the transcript), and an empty exports dir."""
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    exports = tmp_path / "exports"
    exports.mkdir(exist_ok=True)
    aligned = work / "aligned.lrc"
    aligned.write_text(fx["aligned_lrc"], encoding="utf-8")
    (work / "aligned.scores.json").write_text(json.dumps(fx["aligned_scores"]), encoding="utf-8")
    doc = asr if asr is not None else fx["asr"]
    asr_json = work / "lyrics.json"
    asr_json.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    whisper_txt = work / "lyrics.txt"
    whisper_txt.write_text("\n".join(s["text"].strip() for s in doc["segments"]), encoding="utf-8")
    return exports, whisper_txt, aligned, asr_json


def _curated(fx: dict) -> LyricsResult:
    return LyricsResult(synced_lrc=fx["curated_lrc"], source="lrclib_search", match_variant="Конь")


def _shipped(exports: Path) -> str:
    """What the Stage shows, as plain words (LRC tags stripped)."""
    lrc = exports / "lyrics.lrc"
    if lrc.is_file():
        return lrc_to_plain(lrc.read_text(encoding="utf-8"))
    return (exports / "lyrics.txt").read_text(encoding="utf-8")


def _stretched(lrc: str, factor: float = 1.5, shift: float = 20.0) -> str:
    """The same lines on another timeline (t * factor + shift): a cut whose
    curated timings do not fit and that no constant offset (#293) repairs."""
    def stamp(match: re.Match[str]) -> str:
        t = (int(match[2]) * 60 + float(match[3])) * factor + shift
        return f"{match[1]}{int(t // 60):02d}:{t % 60:05.2f}{match[4]}"
    return re.sub(r"([\[<])(\d+):(\d+(?:\.\d+)?)([\]>])", stamp, lrc)


# -- job 144: another song's audio ------------------------------------------------


def test_job144_kon_lyrics_on_creep_audio_fall_to_the_transcript(tmp_path):
    fx = _fixture("job144_kon_on_creep.json")
    exports, whisper_txt, aligned, asr_json = _stage(tmp_path, fx)
    prov = _resolve_lyrics(_curated(fx), exports, whisper_txt, aligned, asr_json, lang=None)
    assert prov["lyrics_source"].startswith("whisper_asr")
    assert prov["lyrics_lrclib_rejected"].startswith("asr_disagreement")
    shipped = _shipped(exports)
    assert "конём" not in shipped and "When you were here before" in shipped


def test_job144_with_too_short_a_transcript_does_not_ship_the_alignment(tmp_path):
    """A deliberately truncated transcript can't judge the words. The
    incomplete alignment still can't provide timings for the full text."""
    fx = _fixture("job144_kon_on_creep.json")
    short = dict(fx["asr"], segments=fx["asr"]["segments"][:1])
    exports, whisper_txt, aligned, asr_json = _stage(tmp_path, fx, asr=short)
    prov = _resolve_lyrics(_curated(fx), exports, whisper_txt, aligned, asr_json, lang=None)
    assert prov["lyrics_source"] == SOURCE_LRCLIB_PLAIN
    assert not (exports / "lyrics.lrc").exists()
    assert prov["lyrics_quality"]["status"] == "needs_review"


def test_a_confident_transcript_in_another_language_still_judges_the_record(tmp_path):
    """The title's script says one language, whisper confidently heard another
    (p=0.98). That is evidence about the audio, not an ASR failure, so the
    guard runs and drops the record. Here the hint is forced to "he" to model a
    Hebrew title over another song's English audio."""
    fx = _fixture("job144_kon_on_creep.json")
    exports, whisper_txt, aligned, asr_json = _stage(tmp_path, fx)
    prov = _resolve_lyrics(_curated(fx), exports, whisper_txt, aligned, asr_json, lang="he")
    assert prov["lyrics_source"].startswith("whisper_asr")
    assert prov["lyrics_lrclib_rejected"].startswith("asr_disagreement")


def test_an_unsure_transcript_in_another_language_is_not_held_against_the_record(tmp_path):
    """#260 unchanged: a Hebrew vocal decoded as English at p=0.456 is an ASR
    failure. It neither rejects nor blocks the record; the coverage fallback
    behaves as before."""
    fx = _fixture("job144_kon_on_creep.json")
    unsure = dict(fx["asr"], language_probability=0.456)
    exports, whisper_txt, aligned, asr_json = _stage(tmp_path, fx, asr=unsure)
    prov = _resolve_lyrics(_curated(fx), exports, whisper_txt, aligned, asr_json, lang="he")
    assert prov["lyrics_source"] == SOURCE_FORCED_ALIGNED
    assert "lyrics_reference_agreement" not in prov


# -- job 148: the right song, collapsed transcript --------------------------------


def test_job148_kon_tv_cut_keeps_the_curated_lyrics(tmp_path):
    fx = _fixture("job148_kon_tv_cut.json")
    exports, whisper_txt, aligned, asr_json = _stage(tmp_path, fx)
    prov = _resolve_lyrics(_curated(fx), exports, whisper_txt, aligned, asr_json, lang=None)
    assert prov["lyrics_source"] in (SOURCE_LRCLIB_SYNCED, SOURCE_FORCED_ALIGNED)
    assert prov["lyrics_reference_agreement"] >= 0.3
    assert "Выйду ночью в поле с конём" in _shipped(exports)


def test_job148_confirmed_text_still_rides_the_coverage_fallback(tmp_path):
    """The right song whose curated timings miss (here stretched so they
    cannot fit): the transcript confirms the words, so the aligner's timing
    ships (#241) and the new confirmation rule does not get in the way."""
    fx = _fixture("job148_kon_tv_cut.json")
    exports, whisper_txt, aligned, asr_json = _stage(tmp_path, fx)
    curated = LyricsResult(synced_lrc=_stretched(fx["curated_lrc"]), source="lrclib_search")
    prov = _resolve_lyrics(curated, exports, whisper_txt, aligned, asr_json, lang=None)
    assert prov["lyrics_source"] == SOURCE_FORCED_ALIGNED
    assert prov["lyrics_align_reason"].startswith("align_coverage_low")
    assert "Выйду ночью в поле с конём" in _shipped(exports)


def test_e_with_diaeresis_is_the_same_word_for_the_guard():
    fx = _fixture("job148_kon_tv_cut.json")
    asr_text = " ".join(s["text"] for s in fx["asr"]["segments"])
    reference = "\n".join(line.split("]", 1)[1] for line in fx["curated_lrc"].splitlines())
    folded = bigram_agreement(asr_text, reference)
    assert folded is not None and folded >= 0.3
    # Against the full record the production guard measured 0.275 for this
    # transcript without folding «конем» / «конём», 0.94 with it.
    assert fold_optional_letters("Мы пойдём с конём") == "Мы пойдем с конем"
    assert fold_optional_letters("Ёлка й ñ") == "Елка й ñ"


@pytest.mark.parametrize("use_alignment", [False, True])
def test_job612_preserves_all_italian_lyrics_with_sparse_spanish_asr(tmp_path, use_alignment):
    fx = _fixture("job612_italian_sparse_asr.json")
    reference = fx["reference_text"]
    assert _reference_language(reference) == "it"
    exports, whisper_txt, aligned, asr_json = _stage(tmp_path, fx)
    (exports / "lyrics.lrc").write_text("[00:01.00]Stale lyrics")
    prov = _resolve_lyrics(
        LyricsResult(plain=reference, source="lrclib_search"), exports, whisper_txt,
        aligned if use_alignment else None, asr_json, lang="it",
    )
    assert prov["lyrics_source"] == SOURCE_LRCLIB_PLAIN
    assert prov["synced"] is False
    assert _shipped(exports) == reference
    assert prov["lyrics_quality"]["status"] == "needs_review"
    assert prov["lyrics_quality"]["counts"]["matched_words"] == 80
    assert "asr_transcript_sparse" in {issue["code"] for issue in prov["lyrics_quality"]["issues"]}


def test_sparse_asr_keeps_full_alignment_and_marks_it_for_review(tmp_path):
    fx = _fixture("job612_italian_sparse_asr.json")
    ref = "Soffia dolcemente\nUn venticello"
    fx["aligned_lrc"] = "[00:13.48]Soffia dolcemente\n[00:20.26]Un venticello"
    fx["aligned_scores"] = [None, None]
    exports, whisper_txt, aligned, asr_json = _stage(tmp_path, fx)
    prov = _resolve_lyrics(
        LyricsResult(plain=ref, source="lrclib_search"), exports, whisper_txt, aligned, asr_json, lang="it",
    )
    assert asr_is_sparse(fx["asr"], ref)  # Sparse by recording duration.
    assert prov["lyrics_source"] == SOURCE_FORCED_ALIGNED
    assert _shipped(exports) == ref
    assert prov["lyrics_quality"]["status"] == "needs_review"


def test_reference_language_abstains_for_ambiguous_text():
    assert _reference_language("la la la no no no") is None


# -- stems that are not the submitted audio ----------------------------------------


def _wav(path: Path, seconds: float, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(seconds * rate))
    return path


needs_ffprobe = pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")


@needs_ffprobe
def test_stems_of_another_recording_fail_the_job(tmp_path):
    """Job 144's numbers: a 203 s submission came back with 236.6 s stems."""
    mix = _wav(tmp_path / "mix.wav", 203)
    vocals = _wav(tmp_path / "vocals.wav", 236.6)
    inst = _wav(tmp_path / "no_vocals.wav", 236.6)
    with pytest.raises(PipelineError, match="another recording"):
        _check_stems_match_mix(mix, {"vocals": vocals, "instrumental": inst})


@needs_ffprobe
def test_stems_of_the_submitted_audio_pass(tmp_path):
    mix = _wav(tmp_path / "mix.wav", 203)
    _check_stems_match_mix(mix, {"vocals": _wav(tmp_path / "v.wav", 203.05),
                                 "instrumental": _wav(tmp_path / "i.wav", 202.9)})


def test_unmeasurable_audio_is_not_judged(tmp_path):
    mix = tmp_path / "mix.wav"
    mix.write_bytes(b"not audio")
    _check_stems_match_mix(mix, {"vocals": tmp_path / "missing.wav"})
