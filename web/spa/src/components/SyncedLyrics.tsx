import { useEffect, useMemo, useRef, useState } from "react";
import { getLyricsLrc } from "../api";

// ─── LRC parsing ────────────────────────────────────────────────────────────
//
// LRC lines look like `[mm:ss.xx] text` with one or more timestamp tags on a
// single line (e.g. a refrain repeated at several times). We expand each tag
// into its own line, drop blank / metadata-only lines, and sort by time so the
// active-line lookup is a plain binary search.
//
// Enhanced LRC carries word-level offsets as inline `<mm:ss.xx>` tags. We strip
// those for the line text (word-level highlight is a documented nice-to-have,
// not the requirement) but keep the leading line timestamp intact.

export interface LyricLine {
  /** Line start time in seconds. */
  t: number;
  /** Display text with timestamp tags removed. */
  text: string;
}

// Leading line timestamp: [mm:ss], [mm:ss.xx], [mm:ss.xxx] (also tolerates a
// `:` fraction separator some encoders emit). Matched globally so repeated
// leading tags on one line each yield a line.
const LINE_TAG = /\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]/g;
// Inline word-level tags from Enhanced LRC, e.g. `<00:12.34>`; stripped for the
// line text since we render line-level highlight.
const WORD_TAG = /<\d{1,2}:\d{2}(?:[.:]\d{1,3})?>/g;

function tagToSeconds(min: string, sec: string, frac?: string): number {
  let t = Number(min) * 60 + Number(sec);
  if (frac) {
    // Normalise 2- or 3-digit fractions to a real fraction of a second.
    t += Number(frac) / 10 ** frac.length;
  }
  return t;
}

/**
 * Parse an LRC body into time-sorted lyric lines. Pure + side-effect free so
 * callers can memoize on the raw string.
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
    const text = raw.slice(lastTagEnd).replace(WORD_TAG, "").trim();
    if (!text) continue; // drop blank / no-text lines
    for (const t of stamps) lines.push({ t, text });
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

// ─── Component ───────────────────────────────────────────────────────────────

export interface SyncedLyricsProps {
  /** Share token (same-origin artifact base `/share/{token}/...`). */
  token: string;
  /** Live playhead in seconds; drives the active-line highlight. */
  currentTime: number;
  /** Plain-text lyrics, used for the fallback scroll box when no LRC exists. */
  plainLyrics: string | null;
  /** Seek the player to `time` seconds (click-to-seek). */
  onSeek: (time: number) => void;
  /** Skip the smooth-scroll work for reduced-motion users. */
  reducedMotion?: boolean;
}

export function SyncedLyrics({
  token,
  currentTime,
  plainLyrics,
  onSeek,
  reducedMotion = false,
}: SyncedLyricsProps) {
  // null = not resolved yet; "" or a body once the fetch settles. We track a
  // separate `resolved` flag so a 404 (→ null) doesn't look like "loading".
  const [lrc, setLrc] = useState<string | null>(null);
  const [resolved, setResolved] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setResolved(false);
    setLrc(null);
    getLyricsLrc(token)
      .then((body) => {
        if (cancelled) return;
        setLrc(body && body.trim() ? body : null);
        setResolved(true);
      })
      .catch(() => {
        if (cancelled) return;
        setLrc(null);
        setResolved(true);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Parse once per LRC body (not per frame).
  const lines = useMemo(() => (lrc ? parseLrc(lrc) : []), [lrc]);
  const hasSynced = lines.length > 0;

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
        <div className="synced-lyrics-prov">
          <span className="synced-lyrics-badge">synced</span>
          <span className="synced-lyrics-src">LRCLIB · click a line to seek</span>
        </div>
        <ol
          className="synced-lyrics-list"
          ref={listRef}
          aria-label="Synced lyrics"
        >
          {lines.map((line, i) => (
            <li
              key={`${line.t}-${i}`}
              ref={(el) => {
                lineRefs.current[i] = el;
              }}
              className={`synced-lyrics-line${i === active ? " is-active" : ""}`}
              aria-current={i === active ? "true" : undefined}
            >
              <button
                type="button"
                className="synced-lyrics-seek"
                onClick={() => onSeek(line.t)}
                title={`Seek to ${formatTimestamp(line.t)}`}
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
  // Until the LRC fetch resolves, show the plain box if we already have it so
  // the panel never flashes empty; once resolved with no synced lyrics we keep
  // the plain box. `resolved` is referenced so the intent is explicit.
  void resolved;
  if (plainLyrics) {
    return <pre className="lyrics-text">{plainLyrics}</pre>;
  }
  return (
    <div className="empty">
      <div>No lyrics available for this job.</div>
    </div>
  );
}

/** mm:ss for a line timestamp (provenance / tooltip only). */
function formatTimestamp(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

export default SyncedLyrics;
