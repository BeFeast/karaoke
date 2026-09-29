"""Meaning-based correction of a raw Hebrew ASR transcript by an LLM (#279, decision 10).

Only for lyrics with no reference anywhere (the ``whisper_asr*`` floor). The
model sees the transcript lines (and the song's name) and proposes word-level
edits — a word or a run of at most three words replaced by similar-sounding
words that fit the meaning. It cannot add or remove lines: an edit names the
exact words it replaces, and anything that does not match the line is
dropped. Edits are applied in place, keeping every LRC line and word tag.

Offline bench (13 Hebrew songs, raw ASR vs the hidden LRCLIB text): GPT-6.1
Sol cut word errors by 24 % (WER 0.436 -> 0.330); 244 edits improved the
text, 8 made it worse, none invented a line.

The LLM sits behind the LAN CLIProxyAPI gateway (OpenAI-compatible). Any
failure — gateway down, quota, timeout, malformed answer — leaves the
transcript as it is.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from karaoke.textnorm import normalize
from karaoke.worker.lyrics import LRC_TIMESTAMP_RE, LRC_WORD_TAG_RE, SPEECH_MARKER
from karaoke.worker.word_vote import Correction, apply_corrections, tokenize

_log = logging.getLogger(__name__)

# One edit replaces at most this many words, with at most this many.
_MAX_EDIT_WORDS = 3
# More than this share of the transcript's words edited = the model rewrote
# the song instead of correcting it (bench maximum: 0.31); nothing is applied.
_MAX_EDITED_SHARE = 0.5
_META_RE = re.compile(r"^\[[A-Za-z]+:[^\]]*\]$")
_UNSAFE_RE = re.compile(r"[\[\]<>]")

SYSTEM_PROMPT = """You correct automatic speech recognition (ASR) output of Hebrew song lyrics.
The transcript lines below are numbered. Typical ASR errors: a word replaced by a similar-sounding wrong word, one word split in two or two merged, a wrong one-letter prefix (ו ה ש ב ל כ מ), a nonsense word. Use the meaning and flow of the song to find and fix them.

Rules:
- Only replace a word or a short run of at most 3 words with words that SOUND similar to what was transcribed.
- Never add or remove lines. Never add words that were not sung.
- Song lyrics are not formal text: do not "fix" grammar, word order, slang, colloquial or non-standard spelling, or English/Arabic words if they are plausible as sung.
- If you are not confident, leave the text as is. Fewer, correct edits are much better than many.

Answer with JSON only: {"edits": [{"line": <number>, "from": "<exact text from that line>", "to": "<replacement>"}]}"""

# (lines, song) -> raw edit dicts, or raises
ProposeFn = Callable[[list[str], str | None], list[dict]]


@dataclass
class LlmCorrection:
    text: str
    proposed: int
    applied: list[dict]


class LlmCorrector:
    """OpenAI-compatible chat client asking for word-level edits."""

    def __init__(self, base_url: str, api_key: str, model: str, *, timeout: float = 240.0, http=None) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._http = http

    def propose(self, lines: list[str], song: str | None) -> list[dict]:
        body = "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))
        user = (f"Song: {song}\n\n" if song else "") + f"Transcript:\n{body}"
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        post = self._http.post if self._http is not None else httpx.post
        response = post(self.url, json=payload, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return parse_edits(response.json()["choices"][0]["message"]["content"])


def parse_edits(content: str) -> list[dict]:
    match = re.search(r"\{.*\}", content or "", re.S)
    if not match:
        raise ValueError("no JSON object in the answer")
    edits = json.loads(match[0]).get("edits")
    if not isinstance(edits, list):
        raise ValueError("answer has no edits list")
    return [e for e in edits if isinstance(e, dict)]


def _sung_lines(text: str) -> list[tuple[int, str]]:
    """``(line_no, plain words)`` of every sung line of an LRC or plain text —
    no metadata tags, no ``[speech]`` lines, no empty lines."""
    out: list[tuple[int, str]] = []
    for line_no, raw in enumerate(text.splitlines()):
        if _META_RE.match(raw.strip()):
            continue
        plain = " ".join(LRC_WORD_TAG_RE.sub(" ", LRC_TIMESTAMP_RE.sub(" ", raw)).split())
        if not plain or plain.startswith(SPEECH_MARKER):
            continue
        out.append((line_no, plain))
    return out


def edits_to_corrections(text: str, sent: list[tuple[int, str]], edits: list[dict], lang: str | None) -> tuple[list[Correction], list[dict]]:
    """Map the model's ``{line, from, to}`` edits onto token-level corrections
    of ``text``. An edit whose ``from`` words are not a run of that line's
    words (compared normalised) is dropped, as is anything that would add or
    remove a line's words wholesale."""
    tokens = tokenize(text, lang)
    by_line: dict[int, list[int]] = {}
    for index, token in enumerate(tokens):
        by_line.setdefault(token.line, []).append(index)
    corrections: list[Correction] = []
    applied: list[dict] = []
    taken: set[int] = set()
    for edit in edits:
        try:
            number = int(edit.get("line"))
        except (TypeError, ValueError):
            continue
        src, dst = str(edit.get("from") or "").strip(), str(edit.get("to") or "").strip()
        if not (1 <= number <= len(sent)) or not src or not dst or _UNSAFE_RE.search(dst):
            continue
        src_keys = [k for k in (normalize(w, lang) for w in src.split()) if k]
        dst_words = dst.split()
        if not src_keys or len(src_keys) > _MAX_EDIT_WORDS or len(dst_words) > _MAX_EDIT_WORDS:
            continue
        if src_keys == [k for k in (normalize(w, lang) for w in dst_words) if k]:
            continue
        ids = by_line.get(sent[number - 1][0], [])
        keys = [tokens[i].key for i in ids]
        for pos in range(len(keys) - len(src_keys) + 1):
            span = ids[pos: pos + len(src_keys)]
            if keys[pos: pos + len(src_keys)] == src_keys and not taken.intersection(span):
                taken.update(span)
                corrections.append(Correction(
                    start=span[0], length=len(span), old=tuple(tokens[i].surface for i in span),
                    replacement=tuple(dst_words), arbiter="llm",
                ))
                applied.append({"line": number, "from": src, "to": dst})
                break
    if tokens and sum(c.length for c in corrections) > _MAX_EDITED_SHARE * len(tokens):
        _log.warning("LLM correction discarded: %d of %d words edited", sum(c.length for c in corrections), len(tokens))
        return [], []
    return sorted(corrections, key=lambda c: c.start), applied


def correct_transcript(text: str, song: str | None, lang: str | None, propose: ProposeFn) -> LlmCorrection:
    """Ask ``propose`` for edits of ``text``'s sung lines and apply them.
    Raises whatever ``propose`` raises; the caller decides what a failure means."""
    sent = _sung_lines(text)
    if not sent:
        return LlmCorrection(text=text, proposed=0, applied=[])
    edits = propose([plain for _, plain in sent], song)
    corrections, applied = edits_to_corrections(text, sent, edits, lang)
    return LlmCorrection(text=apply_corrections(text, corrections, lang), proposed=len(edits), applied=applied)
