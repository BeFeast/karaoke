import { type CSSProperties, useEffect, useMemo, useRef } from "react";

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
    const bodyText = raw.slice(lastTagEnd);
    const text = bodyText.replace(WORD_TAG, "").trim();
    if (!text) continue; // drop blank / no-text lines
    const { words, end } = parseWordTags(bodyText);
    // Repeated leading tags share the same parsed words (like the server).
    for (const t of stamps) lines.push({ t, text, words, end });
  }
  lines.sort((a, b) => a.t - b.t);
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

// ─── Component ───────────────────────────────────────────────────────────────

export interface SyncedLyricsProps {
  /** Raw LRC body from the structured lyrics payload, or null when unsynced. */
  lrc: string | null;
  /**
   * Lyrics provenance from the structured payload (`lrclib_synced`,
   * `whisper_asr_synced`, …) — drives the derived badge so approximate ASR
   * timing isn't labelled as LRCLIB (#145).
   */
  source: string | null;
  /** Plain-text lyrics, used for the fallback scroll box when no LRC exists. */
  plainLyrics: string | null;
  /** Live playhead in seconds; drives the active-line highlight. */
  currentTime: number;
  /** Seek the player to `time` seconds (click-to-seek). */
  onSeek: (time: number) => void;
  /** Skip the smooth-scroll work for reduced-motion users. */
  reducedMotion?: boolean;
}

export function SyncedLyrics({
  lrc,
  source,
  plainLyrics,
  currentTime,
  onSeek,
  reducedMotion = false,
}: SyncedLyricsProps) {
  // Parse once per LRC body (not per frame).
  const lines = useMemo(() => (lrc ? parseLrc(lrc) : []), [lrc]);
  const hasSynced = lines.length > 0;
  const lyricsDirection = useMemo(() => detectLyricsDirection(lines), [lines]);

  // Active index from the playhead. Cheap binary search; the render below only
  // re-runs when React re-renders this component on a currentTime change.
  const active = useMemo(
    () => (hasSynced ? activeLineIndex(lines, currentTime) : -1),
    [lines, currentTime, hasSynced],
  );

  const lineRefs = useRef<Array<HTMLLIElement | null>>([]);
  const listRef = useRef<HTMLOListElement | null>(null);
  const lastScrolled = useRef(-1);

  // Auto-scroll the active line into view, but only when the active index
  // actually changes (not on every timeupdate frame).
  useEffect(() => {
    if (!hasSynced || active < 0) return;
    if (active === lastScrolled.current) return;
    lastScrolled.current = active;
    const el = lineRefs.current[active];
    const list = listRef.current;
    if (!el || !list) return;
    // Center the active line within the scroll box.
    const target = el.offsetTop - list.clientHeight / 2 + el.clientHeight / 2;
    list.scrollTo({
      top: Math.max(0, target),
      behavior: reducedMotion ? "auto" : "smooth",
    });
  }, [active, hasSynced, reducedMotion]);

  // ── Synced path ──────────────────────────────────────────────────────────
  if (hasSynced) {
    return (
      <div className="synced-lyrics" data-synced="true">
        {/* design's stage lyrics header (sections/stage.jsx:114) — the
            provenance segment stays DERIVED from the payload, never hardcoded */}
        <div className="synced-lyrics-prov">lyrics · synced · {sourceLabel(source)} · click to seek</div>
        <ol
          className="synced-lyrics-list"
          ref={listRef}
          aria-label="Synced lyrics"
          dir={lyricsDirection}
        >
          {lines.map((line, i) => (
            <li
              key={`${line.t}-${i}`}
              ref={(el) => {
                lineRefs.current[i] = el;
              }}
              className={`synced-lyrics-line${i === active ? " is-active" : i < active ? " is-sung" : ""}`}
              aria-current={i === active ? "true" : undefined}
            >
              <button
                type="button"
                className="synced-lyrics-seek"
                onClick={() => onSeek(line.t)}
                title={`Seek to ${formatTimestamp(line.t)}`}
                dir="auto"
              >
                {line.text}
              </button>
            </li>
          ))}
        </ol>
      </div>
    );
  }

  // ── Plain fallback ─────────────────────────────────────────────────────────
  if (plainLyrics) {
    return <PlainLyrics text={plainLyrics} className="lyrics-text" />;
  }
  return (
    <div style={{ textAlign: "center", padding: "32px 20px", color: "var(--muted)", fontSize: 12.5 }}>
      No lyrics available for this job.
    </div>
  );
}

/**
 * Badge label for the sync provenance. Whisper-segment timing is approximate,
 * so it's flagged as such instead of being passed off as LRCLIB (#145);
 * everything else (native LRCLIB synced, force-aligned LRCLIB text) is LRCLIB.
 */
export function sourceLabel(source: string | null): string {
  return source === "whisper_asr_synced" ? "ASR (approximate)" : "LRCLIB";
}

/** mm:ss for a line timestamp (provenance / tooltip only). */
function formatTimestamp(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default SyncedLyrics;
