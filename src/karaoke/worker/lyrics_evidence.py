"""Independent ASR evidence used to select and verify curated lyrics."""

from karaoke.textnorm import bigram_agreement, tokens
from karaoke.worker.lyrics import is_degenerate_segment_text

_RETRY_MIN_AGREEMENT = 0.30
_ASR_LANGUAGE_CONFIDENT = 0.8


def transcript_text(asr: dict) -> str:
    segments = asr.get("segments")
    if not isinstance(segments, list):
        return ""
    return "\n".join(
        text.strip()
        for segment in segments
        if isinstance(segment, dict)
        and isinstance(text := segment.get("text"), str)
        and not is_degenerate_segment_text(text)
    )


def asr_is_sparse(asr: dict, reference: str) -> bool:
    if not isinstance(asr.get("segments"), list):
        return False
    count = len(tokens(transcript_text(asr), strip_parenthetical=True))
    reference_count = len(tokens(reference, strip_parenthetical=True))
    duration = asr.get("duration")
    # Count the whole recording, including silent gaps. Leave room for an
    # independent transcript of a cut or a stem that only partially decoded.
    return count * 5 < reference_count or (
        isinstance(duration, (int, float)) and duration >= 30 and count * 12 < duration
    )


def asr_language_confident(asr: dict) -> bool:
    try:
        # The decoder can report 1.0 when it was given an already chosen
        # language. Prefer the independent probe when that probe chose it.
        if asr.get("language_detected") == asr.get("language"):
            probability = asr.get("language_detected_probability", asr.get("language_probability"))
        else:
            probability = asr.get("language_probability")
        return float(probability) >= _ASR_LANGUAGE_CONFIDENT
    except (TypeError, ValueError):
        return False


def prefer_reference_retry(initial: dict, retry: dict, reference: str) -> bool:
    """Accept a corroborated retry or replace an inconclusive first decode."""
    if not transcript_text(retry).strip():
        return False
    if asr_is_sparse(initial, reference) or not asr_language_confident(initial):
        return True
    # A forced language can invent plausible words in that language. It
    # takes a substantial, corroborating retry to displace dense evidence.
    agreement = bigram_agreement(transcript_text(retry), reference, retry.get("language"))
    return not asr_is_sparse(retry, reference) and (
        agreement is not None and agreement >= _RETRY_MIN_AGREEMENT
    )
