"""Title normalization and ``Artist - Title`` parsing.

YouTube (and other ``yt-dlp`` sources) wrap the real song title in a lot of
upload noise — ``(Official Video)``, ``[4K]``, ``(Lyrics)``, ``(Remastered
2011)`` and friends. For the lyrics-lookup foundation (Track 1) we want a
stable ``(artist, track)`` pair, so this module:

  * strips that noise out of a raw title, and
  * splits a cleaned ``"Artist - Title"`` string into its two halves,
    folding ``feat.`` / ``ft.`` credits onto the track side.

Everything here is pure string work with no I/O, so it is trivially unit
testable and safe to import anywhere (no worker deps).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Noise stripping
# ---------------------------------------------------------------------------

# Phrases that, when they make up (most of) a bracketed/parenthesized group,
# mark that group as upload noise rather than part of the real title. Matched
# case-insensitively as whole words so "Live" never eats "Lively".
_NOISE_WORDS: tuple[str, ...] = (
    "official video",
    "official music video",
    "official audio",
    "official lyric video",
    "official lyrics video",
    "official visualizer",
    "official visualiser",
    "official",
    "music video",
    "lyric video",
    "lyrics video",
    "lyrics",
    "lyric",
    "audio",
    "visualizer",
    "visualiser",
    "video",
    "mv",
    "hd",
    "hq",
    "4k",
    "8k",
    "1080p",
    "720p",
    "remaster",
    "remastered",
    "remasterd",
    "explicit",
    "clean",
    "free download",
    "free dl",
    "lyrics on screen",
    "with lyrics",
    # Hebrew upload noise (#260): "official clip", "with lyrics", "lyrics",
    # "karaoke". Longer phrases must precede their substrings — the joined
    # alternation is first-match ("עם מילים" before "מילים").
    "קליפ רשמי",
    "עם מילים",
    "מילים",
    "קריוקי",
    "color coded",
    "color coded lyrics",
    "monstercat release",
    "ncs release",
    "full album",
    "full version",
    "extended version",
    "extended mix",
    "radio edit",
    "visual",
)

# A noise group is "<phrase>" optionally followed by trailing fluff such as a
# year, "version", "edition", "hd", etc. e.g. "(Remastered 2011)",
# "(Official Video HD)", "(Lyrics / Lyric Video)".
_NOISE_PHRASE = "|".join(re.escape(w) for w in _NOISE_WORDS)
_NOISE_GROUP_RE = re.compile(
    rf"""
    \s*
    (?P<open>[\(\[\{{])             # opening bracket
    \s*
    (?:                             # the bracketed content must be noise-only:
        (?:\d{{4}}[\s,/&|+\-]*)?     #   optional leading year, e.g. "2009 Remaster"
        (?:{_NOISE_PHRASE})         #   a noise phrase
        [\s,/&|+\-]*                #   optional separators
        (?:\d{{2,4}}|version|edition|mix|edit|remix|cut|ver\.?|hd|hq|4k|8k)?  # trailing fluff
        [\s,/&|+\-]*
    )+
    \s*
    [\)\]\}}]                       # closing bracket
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Trailing/leading bare (un-bracketed) noise tags, e.g. "... | Official Video"
# or a leading "MV| ...". We only strip these when delimited by a pipe so we
# never chew into a legitimate title.
_BARE_PIPE_NOISE_RE = re.compile(
    rf"\s*\|\s*(?:{_NOISE_PHRASE})\s*(?:\d{{2,4}})?\s*$",
    re.IGNORECASE,
)

# Trailing bare dash-delimited noise tail, e.g. "Artist - Song - עם מילים HD"
# or "Artist - Song - Lyrics HD" (#260). Stripped only when the remaining head
# still contains a dash separator (see normalize_title) so a real track that is
# literally a noise word — "עדן חסון - מילים", "אייל גולן - קריוקי" — keeps its
# track segment. Construction constraints (review of #260):
#   * the phrase loop is an ATOMIC group `(?>…)` — the noise alternation is
#     overlapping ("lyric"/"lyric video"/…), and a backtrackable nested
#     quantifier over it is exponential (a ~300-char crafted title froze the
#     event loop for a minute; titles are user-controlled and length-uncapped);
#   * each phrase must end at a separator or the end of the string, so two
#     noise words fused inside one real word ("Audiovisual" = audio+visual)
#     never chain into a false noise tail;
#   * the separator class includes the dash characters so any depth of stacked
#     noise tails ("… - Lyrics - Lyrics") strips in ONE match — keeping
#     normalize_title idempotent within its bounded loop.
_TRAILING_DASH_NOISE_RE = re.compile(
    rf"\s+[-‐‑־–—]\s*(?>(?:{_NOISE_PHRASE})(?:[\s,/&|+‐‑־–—-]+|$))+$",
    re.IGNORECASE,
)
# Any artist/track dash separator (mirrors the dash classes of _SPLIT_RES).
_ANY_DASH_SEP_RE = re.compile(r"\s+[-‐‑־]\s+|\s*[–—]\s*")

# Collapses leftover empty bracket pairs and repeated separators.
_EMPTY_BRACKETS_RE = re.compile(r"[\(\[\{]\s*[\)\]\}]")
_MULTISPACE_RE = re.compile(r"\s{2,}")
# Strip dangling separators / quotes left at the very ends after cleanup.
_EDGE_JUNK_RE = re.compile(r"^[\s\-–—_~|/,.\"'“”‘’]+|[\s\-–—_~|/,.\"'“”‘’]+$")

# feat. / ft. / featuring credit, e.g. "Song (feat. X)", "Song ft Y", "Song
# featuring Z". Captured so callers can move it onto the track side.
_FEAT_RE = re.compile(
    r"""
    \s*
    [\(\[]?                                 # optional opening bracket
    \s*
    (?:feat\.?|ft\.?|featuring|w\.?/)       # the credit keyword
    \s+
    (?P<artist>[^)\]]+?)                    # the featured artist(s)
    \s*
    [\)\]]?                                 # optional closing bracket
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def normalize_title(raw: str | None) -> str:
    """Strip upload noise from a raw video title.

    Removes ``(Official Video)``-style groups, trailing ``| Official Audio``
    tags, empty leftover brackets, and dangling separators. ``feat.`` credits
    are intentionally preserved (they belong to the track). Returns ``""`` for
    empty / noise-only input.
    """
    if not raw:
        return ""
    text = raw.strip()

    # Iterate: removing one noise group can reveal/adjoin another.
    for _ in range(6):
        new = _NOISE_GROUP_RE.sub(" ", text)
        new = _BARE_PIPE_NOISE_RE.sub("", new)
        # Drop a trailing dash-delimited noise-only segment, but only while an
        # artist/track dash remains in the head — otherwise "Artist - מילים"
        # (a song literally named after a noise word) would lose its track.
        m = _TRAILING_DASH_NOISE_RE.search(new)
        if m and _ANY_DASH_SEP_RE.search(new[: m.start()]):
            new = new[: m.start()]
        new = _EMPTY_BRACKETS_RE.sub(" ", new)
        if new == text:
            break
        text = new

    text = _MULTISPACE_RE.sub(" ", text)
    text = _EDGE_JUNK_RE.sub("", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Artist / track splitting
# ---------------------------------------------------------------------------

# Separators between artist and title, in priority order. All SPACED dash
# classes live in ONE alternation so the LEFTMOST spaced dash wins regardless
# of class — separate class-priority patterns mis-split mixed-dash titles like
# "עדן בן זקן – כולם באילת - עם מילים" at the second dash (#260). Every dash
# in that first pattern (ASCII -, U+2010 ‐, U+2011 ‑, Hebrew maqaf ־, en/em
# dash) requires surrounding whitespace so we never split a dash-joined word —
# "Spider-Man", "Jay–Z", year ranges "1971–1996". An UNSPACED en/em dash is
# only a last-resort separator ("Artist–Title" with no spaced dash anywhere),
# kept as a lower-priority fallback exactly so an in-word en dash can never
# shadow a later spaced separator.
_SPLIT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\s+[-‐‑־–—]\s+"),  # "Artist - Title" (any spaced dash)
    re.compile(r"\s*[–—]\s*"),      # "Artist–Title" (unspaced en/em dash)
    re.compile(r"\s+[:]\s+"),       # "Artist : Title"
)


@dataclass(frozen=True, slots=True)
class ParsedTitle:
    """Result of parsing a (normalized) title into artist + track."""

    artist: str | None
    track: str | None


def _fold_feat(track: str) -> str:
    """Keep a trailing ``feat./ft.`` credit attached to the track, normalized
    to a parenthesized ``(feat. X)`` form when it was bare."""
    m = _FEAT_RE.search(track)
    if not m:
        return track.strip()
    head = track[: m.start()].strip()
    artist = m.group("artist").strip(" )]")
    if not head:
        return track.strip()
    return f"{head} (feat. {artist})"


def parse_artist_track(title: str | None) -> ParsedTitle:
    """Split a ``"Artist - Title"`` string into ``(artist, track)``.

    The title is normalized first (noise stripped). Splitting prefers the
    spaced-hyphen form, falling back to en/em dash and colon. Only the first
    separator is used, so ``"A - B - C"`` becomes ``artist="A"``,
    ``track="B - C"``. ``feat.``/``ft.`` credits are folded onto the track.

    When no separator is present, ``artist`` is ``None`` and the whole cleaned
    string is returned as ``track`` (best-effort — better a track than nothing
    for lyrics lookup).
    """
    cleaned = normalize_title(title)
    if not cleaned:
        return ParsedTitle(artist=None, track=None)

    for sep in _SPLIT_RES:
        parts = sep.split(cleaned, maxsplit=1)
        if len(parts) == 2:
            artist = parts[0].strip()
            track = parts[1].strip()
            if artist and track:
                return ParsedTitle(artist=artist, track=_fold_feat(track))

    # No artist/title separator — treat the whole thing as the track.
    return ParsedTitle(artist=None, track=_fold_feat(cleaned))


# ---------------------------------------------------------------------------
# Track-name cleanup ladder (fallback LRCLIB queries, #230)
# ---------------------------------------------------------------------------

# A track that OPENS with a quoted phrase (guillemets / straight / curly / low
# quotes): keep only the quoted phrase and drop the trailing upload chatter.
# e.g. «Конь». Голубой Ургант. Фрагмент выпуска … → Конь
# NOTE: Hebrew gershayim ״ is deliberately NOT a quote class here — it doubles
# as the in-word acronym marker (צה״ל), so treating it as a closing quote
# truncates quoted heads at the first acronym (#260 review).
_LEADING_QUOTED_RE = re.compile(
    r'^\s*[«"“„‹‘]\s*(?P<inner>[^«»"“”„‹›‘’]+?)\s*[»"”“›’]'
)

# First sentence-terminating boundary: a "." followed by whitespace/end, "//",
# or "|". Everything from there on is cut when a usable head remains.
_SENTENCE_CUT_RE = re.compile(r"\.\s|\.$|//|\|")
# A "." boundary is an in-title abbreviation (not a sentence end) when the
# token before it is 1-2 LETTERS ("Pt.", "Mr.", "Dr."). Digits don't count —
# "…Pt. 2." ends a real sentence. Cutting at an abbreviation would emit a
# truncated query that can fuzzy-match the wrong record.
_ABBREV_TOKEN_RE = re.compile(r"(?:^|\s)([^\W\d_]{1,2})\.$", re.UNICODE)


def track_cleanup_variants(track: str | None) -> list[str]:
    """Progressively cleaned track-name variants for a *fallback* LRCLIB lookup.

    Applied only when the parsed ``(artist, track)`` already missed (see
    :mod:`karaoke.worker.lyrics`). The steps are cumulative — each operates on
    the output of the previous one — and a variant is emitted only when a step
    actually changed the text:

      1. If the track opens with a quoted phrase, keep only that phrase:
         ``«Конь». Голубой Ургант. …`` → ``Конь``.
      2. Cut everything from the first sentence-terminating ``.`` / ``//`` /
         ``|`` when at least two characters remain before it:
         ``Конь. Голубой Ургант`` → ``Конь``.

    The result never includes the original ``track`` and is de-duplicated
    (case-insensitively), so the caller issues at most one HTTP query per
    genuinely new variant. Returns ``[]`` when nothing changes.
    """
    text = (track or "").strip()
    if not text:
        return []

    variants: list[str] = []
    seen = {text.lower()}

    m = _LEADING_QUOTED_RE.match(text)
    if m:
        inner = m.group("inner").strip()
        if inner and inner.lower() not in seen:
            variants.append(inner)
            seen.add(inner.lower())
            text = inner

    for m in _SENTENCE_CUT_RE.finditer(text):
        head = text[: m.start()].strip()
        # A "." boundary whose preceding token is 1-2 chars is an in-title
        # abbreviation ("Song Pt. 2", "Mr. Brightside") — not a sentence end;
        # try the next boundary instead of emitting a truncated query.
        if m.group().startswith(".") and _ABBREV_TOKEN_RE.search(f"{head}."):
            continue
        # >= 3 chars: a shorter head is an artifact, not a usable track name.
        if len(head) >= 3 and head.lower() not in seen:
            variants.append(head)
            seen.add(head.lower())
        break

    return variants


def derive_metadata(info: dict | None) -> dict[str, object | None]:
    """Best-effort ``(artist, track, album, duration)`` from a yt-dlp info dict.

    Prefers the structured fields yt-dlp extracts for music
    (``artist``/``track``/``album``/``duration``). When ``artist`` or
    ``track`` is missing, falls back to parsing the (normalized) ``title``.
    Always returns all four keys; values are ``None`` when unknown.
    ``duration`` is coerced to a non-negative ``int`` of seconds.
    """
    info = info or {}

    def _clean_str(value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    artist = _clean_str(info.get("artist")) or _clean_str(info.get("creator"))
    track = _clean_str(info.get("track"))
    album = _clean_str(info.get("album"))

    if not artist or not track:
        parsed = parse_artist_track(_clean_str(info.get("title")))
        artist = artist or parsed.artist
        track = track or parsed.track

    duration: int | None = None
    raw_duration = info.get("duration")
    if raw_duration is not None:
        try:
            duration = int(float(raw_duration))
        except (TypeError, ValueError):
            duration = None
        if duration is not None and duration < 0:
            duration = None

    return {"artist": artist, "track": track, "album": album, "duration": duration}
