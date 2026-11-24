// KARAOKE — Stage lyric timing math (Marquee port, #154; the fader widgets
// left with the Sonereli Stage, UI/UX v1 step 11).
// Adapted from design/claude-export/proto/core.jsx (lyricState, core.jsx:79-88):
// LRC carries line starts only, so timeLines derives the sung durations.

import type { LyricLine } from "./SyncedLyrics";

export interface TimedWord {
  /** Word start in seconds. */
  t: number;
  /** Resolved word span in seconds (>= 0; the character-wipe pace unit). */
  d: number;
  text: string;
}

export interface TimedLine {
  /** Line start in seconds (from the LRC timestamp). */
  t: number;
  /** Derived sing duration in seconds (LRC carries no durations). */
  d: number;
  text: string;
  /**
   * Resolved per-word spans for Enhanced-LRC lines (undefined for plain lines).
   * Present → the wipe fills word-by-word; absent → today's linear line wipe.
   */
  words?: TimedWord[];
  /** Line sung-end in seconds when known (drives `d` via min(end - t, interval)). */
  end?: number;
}

// LRC gives start timestamps only. A line "sings" until the next one starts,
// except across instrumental breaks: an interval longer than MAX_LINE_S reads
// as a break, so the line keeps a typical sung length and the remainder
// becomes the gap the setlist bulbs count down. The last line gets the time
// to track end when known, capped the same way.
const MAX_LINE_S = 10;
const BREAK_LINE_S = 5;

// Resolve a parsed line's word tags into concrete spans. A word sings until the
// next word starts; the last word uses its own `d` (from the line's end tag)
// when known, else the line's sung end, else the next line's start, else the
// track end — the consumer-derived rule (next word -> line end -> next line).
function resolveWords(
  line: LyricLine,
  next: LyricLine | undefined,
  duration: number | null,
): TimedWord[] | undefined {
  const ws = line.words;
  if (!ws || ws.length === 0) return undefined;
  const lineEnd =
    line.end != null
      ? line.end
      : next
        ? next.t
        : duration != null && duration > line.t
          ? duration
          : line.t + BREAK_LINE_S;
  // No word may sing past the point the line stops being current: a trailing
  // end tag (or a stray word tag) landing after the next line's start would
  // leave the wipe mid-word when the next line takes over.
  const cap = next != null ? Math.min(lineEnd, next.t) : lineEnd;
  return ws.map((w, i) => {
    const nextStart = i + 1 < ws.length ? ws[i + 1].t : lineEnd;
    const raw = w.d != null ? w.d : nextStart - w.t;
    const capped = Math.min(raw, cap - w.t);
    // Clamp to a non-negative span; a degenerate/zero span fills instantly.
    return { t: w.t, d: capped > 0 ? capped : 0, text: w.text };
  });
}

export function timeLines(lines: LyricLine[], duration: number | null): TimedLine[] {
  return lines.map((line, i) => {
    const next = lines[i + 1];
    const interval = next
      ? next.t - line.t
      : duration != null && duration > line.t
        ? duration - line.t
        : BREAK_LINE_S;
    // With a known sung end the line sings exactly until `end` (never past the
    // next line); word-less lines keep the capped-interval heuristic verbatim.
    const d =
      line.end != null
        ? Math.max(0.5, Math.min(line.end - line.t, interval))
        : interval > MAX_LINE_S
          ? BREAK_LINE_S
          : Math.max(0.5, interval);
    return { t: line.t, d, text: line.text, words: resolveWords(line, next, duration), end: line.end };
  });
}

export interface LyricState {
  idx: number;
  cur: TimedLine | null;
  /** Wipe fill of the current line, 0–100. */
  sung: number;
  next: TimedLine | null;
  prev: TimedLine | null;
  /** Seconds until the next line starts. */
  gap: number;
  /** True between the current line's sung end and the next line. */
  inGap: boolean;
}

/**
 * Character-weighted sung fraction (0–1) of a word-timed line at playhead
 * `pos`. The line is modelled as its words joined by single separators; the
 * fill advances through each word's characters at that word's own pace
 * ((pos − t) / d), so the wipe reaches every word exactly at its start `t`,
 * runs at the sung word's speed, and holds at the last word's boundary across
 * an intra-line gap (between a word's end and the next word's start). Order is
 * logical (first word first); the visual-start direction is CSS's job (the
 * `.w-fill` `inset-inline-start` anchor flips under `dir=rtl`, #215), so this
 * math is identical for LTR and RTL lines.
 */
export function wordSungFraction(words: TimedWord[], pos: number): number {
  let total = 0;
  for (let i = 0; i < words.length; i++) total += words[i].text.length + (i > 0 ? 1 : 0);
  if (total <= 0) return 0;
  // Characters of every span already fully sung (words + their separators).
  let filled = 0;
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    const sep = i + 1 < words.length ? 1 : 0;
    if (pos >= w.t + w.d) {
      // Fully sung (covers a zero-span word once pos reaches its start).
      filled += w.text.length + sep;
      continue;
    }
    if (pos >= w.t) {
      // In progress (w.d > 0 here) — linear over this word's characters.
      return (filled + w.text.length * ((pos - w.t) / w.d)) / total;
    }
    return filled / total; // not started — hold at the previous boundary
  }
  return 1; // past the last word
}

// which lyric line is current, and its wipe % (core.jsx:79-88 logic; word-timed
// lines fill piecewise via wordSungFraction, word-less lines stay linear).
export function lyricState(lines: TimedLine[], pos: number): LyricState {
  let idx = -1;
  for (let i = 0; i < lines.length; i++) if (pos >= lines[i].t) idx = i;
  const cur = idx >= 0 ? lines[idx] : null;
  let sung = 0;
  if (cur) {
    sung =
      cur.words && cur.words.length > 0
        ? wordSungFraction(cur.words, pos)
        : Math.min(1, (pos - cur.t) / cur.d);
  }
  const next = lines[idx + 1] || null;
  const gap = next ? next.t - pos : 0; // seconds until next line starts
  const inGap = cur ? pos > cur.t + cur.d : true;
  return { idx, cur, sung: sung * 100, next, prev: lines[idx - 1] || null, gap, inGap };
}
