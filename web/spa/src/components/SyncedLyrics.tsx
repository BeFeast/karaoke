import type { CSSProperties } from "react";

// ─── LRC parsing ────────────────────────────────────────────────────────────
//
// LRC lines look like `[mm:ss.xx] text` with one or more timestamp tags on a
// single line (e.g. a refrain repeated at several times). We expand each tag
// into its own line, drop blank / metadata-only lines, and sort by time so the
// active-line lookup is a plain binary search.
//
// Enhanced LRC carries word-level offsets as inline `<mm:ss.xx>` tags. We strip
// those from the line *text* (so display stays clean) but also parse them into
// per-word `words`/`end` timing so the wipe can fill word-by-word (#221). Plain
// lines carry no `<>` tags and keep `words`/`end` undefined — a per-line
// fallback, so mixed files (some enhanced, some plain) are valid.

export interface LyricWord {
  /** Word start in seconds. */
  t: number;
  /**
   * Word duration in seconds: the next word's start minus this word's start;
   * the last word uses the line's trailing end tag. `null` when unknown (last
   * word with no trailing end tag) — the consumer derives it from the next
   * line's start (stage-core.timeLines).
   */
  d: number | null;
  /** Clean word text (timing tags removed). */
  text: string;
}

export interface LyricLine {
  /** Line start time in seconds. */
  t: number;
  /** Display text with timestamp tags removed. */
  text: string;
  /** Per-word timings for Enhanced-LRC lines; undefined for plain lines. */
  words?: LyricWord[];
  /** Line sung-end in seconds from a trailing `<mm:ss.xx>` tag; else undefined. */
  end?: number;
  /**
   * Non-sung speech (a music video's dialogue, #283): the LRC text carried a
   * leading `[speech]` marker. Shown dimmed and labelled, never highlighted as
   * singing, and left out of the karaoke wipe (see `sungLines`).
   */
  speech?: boolean;
}

/** LRC text marker for non-sung speech lines (mirrors the server's SPEECH_MARKER). */
export const SPEECH_MARKER = "[speech]";

/** Only the sung lines — what the player / performance wipe may highlight. */
export function sungLines(lines: LyricLine[]): LyricLine[] {
  return lines.filter((line) => !line.speech);
}

// Leading line timestamp: [mm:ss], [mm:ss.xx], [mm:ss.xxx] (also tolerates a
// `:` fraction separator some encoders emit). Matched globally so repeated
// leading tags on one line each yield a line.
const LINE_TAG = /\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]/g;
// Inline word-level tags from Enhanced LRC, e.g. `<00:12.34>`; stripped for the
// line text (display stays clean). The capturing variant parses the same tags
// into word timings — kept as separate objects so the two never share the
// stateful `lastIndex` of a global regex.
const WORD_TAG = /<\d{1,2}:\d{2}(?:[.:]\d{1,3})?>/g;
const WORD_TAG_CAP = /<(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?>/g;
const RTL_STRONG = /[\u0590-\u08ff\ufb1d-\ufdff\ufe70-\ufeff]/u;
const LTR_STRONG = /\p{Script=Latin}|\p{Script=Greek}|\p{Script=Cyrillic}/u;
type LyricsDirection = "ltr" | "rtl" | "auto";

function tagToSeconds(min: string, sec: string, frac?: string): number {
  let t = Number(min) * 60 + Number(sec);
  if (frac) {
    // Normalise 2- or 3-digit fractions to a real fraction of a second.
    t += Number(frac) / 10 ** frac.length;
  }
  return t;
}

/**
 * Parse inline Enhanced-LRC `<mm:ss.xx>` word tags from a line body (the text
 * after the leading `[..]` line tag(s), word tags still present). Returns the
 * per-word timings and the line's sung `end`, or `{}` for a plain line and for
 * a malformed enhanced line that must degrade to today's linear line wipe.
 * Mirrors the server's `_parse_lrc_words` (routes.py) so client + API agree.
 */
function parseWordTags(body: string): { words?: LyricWord[]; end?: number } {
  WORD_TAG_CAP.lastIndex = 0;
  const tags: RegExpExecArray[] = [];
  let m: RegExpExecArray | null;
  while ((m = WORD_TAG_CAP.exec(body)) !== null) tags.push(m);
  if (tags.length === 0) return {}; // plain line — today's behavior
  // Words must be tag-led: any text before the first word tag is malformed.
  if (body.slice(0, tags[0].index).trim()) return {};
  const segments: { t: number; text: string }[] = [];
  for (let i = 0; i < tags.length; i++) {
    const stop = i + 1 < tags.length ? tags[i + 1].index : body.length;
    segments.push({
      t: tagToSeconds(tags[i][1], tags[i][2], tags[i][3]),
      text: body.slice(tags[i].index + tags[i][0].length, stop).trim(),
    });
  }
  // A trailing tag with no following word text is the line's sung end.
  let end: number | undefined;
  if (segments[segments.length - 1].text === "") {
    end = segments[segments.length - 1].t;
    segments.pop();
  }
  // Only a bare end tag, or an empty-text word mid-line -> degrade to plain.
  if (segments.length === 0 || segments.some((s) => s.text === "")) return {};
  const words: LyricWord[] = segments.map((s, i) => ({
    t: s.t,
    d:
      i + 1 < segments.length
        ? segments[i + 1].t - s.t
        : end != null
          ? end - s.t
          : null,
    text: s.text,
  }));
  return { words, end };
}

/**
 * Parse an LRC body into time-sorted lyric lines. Pure + side-effect free so
 * callers can memoize on the raw string. Enhanced-LRC `<mm:ss.xx>` word tags
 * are parsed into per-line `words`/`end` and stripped from the display text.
 */
export function parseLrc(body: string): LyricLine[] {
  const lines: LyricLine[] = [];
  for (const raw of body.split(/\r?\n/)) {
    // Collect every leading timestamp tag on this physical line.
    const stamps: number[] = [];
    LINE_TAG.lastIndex = 0;
    let m: RegExpExecArray | null;
    let lastTagEnd = 0;
    while ((m = LINE_TAG.exec(raw)) !== null) {
      // Only timestamps that sit at the run-up of the line (no text between
      // them) are line tags; once text appears we stop treating tags as stamps.
      if (m.index !== lastTagEnd) break;
      stamps.push(tagToSeconds(m[1], m[2], m[3]));
      lastTagEnd = LINE_TAG.lastIndex;
    }
    if (stamps.length === 0) continue; // metadata line (e.g. [ti:], [ar:]) or blank
    let bodyText = raw.slice(lastTagEnd);
    let text = bodyText.replace(WORD_TAG, "").trim();
    const speech = text.startsWith(SPEECH_MARKER);
    if (speech) {
      text = text.slice(SPEECH_MARKER.length).trim();
      bodyText = bodyText.replace(SPEECH_MARKER, "");
    }
    if (!text) continue; // drop blank / no-text lines
    if (speech) {
      for (const t of stamps) lines.push({ t, text, speech: true });
      continue;
    }
    const { words, end } = parseWordTags(bodyText);
    // Repeated leading tags share the same parsed words (like the server).
    for (const t of stamps) lines.push({ t, text, words, end });
  }
  lines.sort((a, b) => a.t - b.t);
  // Forced-aligned word timing regularly places the first word BEFORE the
  // curated line tag (median ~0.3 s on real jobs) — the tag is when the line
  // "belongs", the word tag is when the voice actually starts. Activating on
  // the tag makes the highlight lag the vocal, so pull a word-timed line's
  // start up to its first word (bounded: within 2 s, mirroring the server
  // merge drift guard). Repeated-stamp copies share one words array, so only
  // the earliest copy (the one the aligner actually timed) is adjusted. The
  // start stays STRICTLY after the previous line's (small epsilon) so the
  // previous line remains selectable by the last-start-≤-pos lookup.
  const adjusted = new Set<LyricWord[]>();
  for (let i = 0; i < lines.length; i++) {
    const ln = lines[i];
    const ws = ln.words;
    if (!ws || ws.length === 0 || adjusted.has(ws)) continue;
    adjusted.add(ws);
    const w0 = ws[0].t;
    if (w0 >= ln.t || ln.t - w0 > 2) continue;
    const floor = i > 0 ? lines[i - 1].t + 0.01 : 0;
    ln.t = Math.min(ln.t, Math.max(w0, floor));
  }
  return lines;
}

/**
 * Binary search for the index of the active line at time `t`: the last line
 * whose start is <= t. Returns -1 before the first line. O(log n), called on
 * every timeupdate.
 */
export function activeLineIndex(lines: LyricLine[], t: number): number {
  let lo = 0;
  let hi = lines.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (lines[mid].t <= t) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

/**
 * Direction of a single text run from its first strong-direction character:
 * "rtl" for a leading Hebrew/Arabic (etc.) strong char, "ltr" for a leading
 * Latin/Greek/Cyrillic one, "auto" when the run has no strong-direction
 * character (leaving the browser's bidi fallback in charge). Resolving per
 * line — not per block — is what keeps a mixed-script surface correct: an
 * English intro must not force the later Hebrew/Arabic lines to LTR (#214).
 */
export function detectTextDirection(text: string): LyricsDirection {
  for (const char of text) {
    if (RTL_STRONG.test(char)) return "rtl";
    if (LTR_STRONG.test(char)) return "ltr";
  }
  return "auto";
}

/**
 * Pick the scroll container direction from the first strong-direction lyric
 * character across all lines. Returning "auto" keeps the browser bidi fallback
 * for lyrics with no obvious strong script in the parsed text.
 */
export function detectLyricsDirection(lines: LyricLine[]): LyricsDirection {
  for (const line of lines) {
    const dir = detectTextDirection(line.text);
    if (dir !== "auto") return dir;
  }
  return "auto";
}

/**
 * Plain (non-synced) lyrics rendered one block per source line, each carrying
 * its own first-strong direction. A single `dir` on the whole block would let
 * the block's first strong character force every later line's alignment and
 * edge punctuation to the wrong side, so mixed-script lyrics (an English intro
 * before Hebrew/Arabic verses) must resolve direction per line (#214). Blank
 * source lines keep their height via a non-breaking space.
 */
export function PlainLyrics({
  text,
  className,
  style,
}: {
  text: string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div className={className} style={style}>
      {text.split(/\r?\n/).map((line, i) => (
        <div key={i} dir={detectTextDirection(line)}>
          {line || " "}
        </div>
      ))}
    </div>
  );
}

/**
 * Badge label for the sync provenance. Whisper-segment timing is approximate,
 * so it's flagged as such instead of being passed off as LRCLIB (#145);
 * everything else (native LRCLIB synced, force-aligned LRCLIB text) is LRCLIB.
 */
export function sourceLabel(source: string | null): string {
  switch (source) {
    case "human_reviewed": return "Owner reviewed";
    case "whisper_asr_synced":
    case "whisper_asr": return "ASR (approximate)";
    case "forced_aligned": return "Vocal alignment";
    case "asr_reference_corrected": return "ASR + web reference";
    case "lrclib_plain": return "LRCLIB (untimed)";
    case "lrclib_synced": return "LRCLIB";
    default: return "Unknown source";
  }
}
