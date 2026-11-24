// Pure model for the Stage lyrics (Sonereli UI/UX v1 step 7): line roles,
// count-in / instrumental cues and the follow anchor. No DOM, unit-tested.

import type { TimedLine } from "./stage-core";
import type { LyricLine } from "./SyncedLyrics";

export type LineRole = "active" | "next" | "ahead" | "past" | "speech";

/** Role of every line for the active index (-1 = before the first line). */
export function lineRoles(lines: LyricLine[], active: number): LineRole[] {
  let next = -1;
  for (let i = active + 1; i < lines.length; i++) {
    if (!lines[i].speech) {
      next = i;
      break;
    }
  }
  return lines.map((line, i) => {
    if (line.speech) return "speech";
    if (i === active) return "active";
    if (i === next) return "next";
    return i < active ? "past" : "ahead";
  });
}

export interface LyricCue {
  kind: "intro" | "instrumental";
  /** Index of the line the cue sits above. */
  before: number;
  /** When the cue's wait starts and ends (the line start), seconds. */
  start: number;
  end: number;
}

/** A music break this long gets its own "Instrumental · 0:15" cue. */
export const INSTRUMENTAL_MIN_S = 8;

/**
 * Cues above lines: the intro before the first sung line, and every
 * instrumental break of at least INSTRUMENTAL_MIN_S between two sung lines
 * (from the previous line's sung end to the next line's start).
 */
export function lyricCues(lines: LyricLine[], timed: TimedLine[]): LyricCue[] {
  const cues: LyricCue[] = [];
  let prev = -1;
  for (let i = 0; i < lines.length; i++) {
    if (lines[i].speech) continue;
    if (prev < 0) {
      cues.push({ kind: "intro", before: i, start: 0, end: lines[i].t });
    } else {
      const start = timed[prev].t + timed[prev].d;
      if (lines[i].t - start >= INSTRUMENTAL_MIN_S) {
        cues.push({ kind: "instrumental", before: i, start, end: lines[i].t });
      }
    }
    prev = i;
  }
  return cues;
}

/** Count-in state of a cue at `now`: three dots light up over the last 3 s. */
export function cueCountdown(cue: LyricCue, now: number): { active: boolean; remaining: number; lit: number } {
  const active = now >= cue.start && now < cue.end;
  const remaining = Math.max(0, cue.end - now);
  const lit = active && remaining <= 3 ? Math.min(3, Math.max(0, 4 - Math.ceil(remaining))) : 0;
  return { active, remaining, lit };
}

/** The active line's top sits at this share of the lyrics viewport. */
export const FOLLOW_ANCHOR = 0.28;

/** Space kept below a line that is too tall for the anchor. */
const FIT_MARGIN = 16;

/**
 * Where a line's top goes, as px from the viewport top: the anchor, unless
 * the line would then run past the bottom; a tall line moves up so it stays
 * whole on screen.
 */
export function anchorOffset(viewportHeight: number, lineHeight = 0): number {
  const anchor = viewportHeight * FOLLOW_ANCHOR;
  if (lineHeight <= viewportHeight - anchor - FIT_MARGIN) return anchor;
  return Math.max(8, viewportHeight - lineHeight - FIT_MARGIN);
}

/** scrollTop that puts a line whose top is `lineTop` on the anchor. */
export function anchorScrollTop(lineTop: number, viewportHeight: number, maxScroll: number, lineHeight = 0): number {
  return Math.max(0, Math.min(maxScroll, lineTop - anchorOffset(viewportHeight, lineHeight)));
}

/** The active line never shrinks below this to fit (spec: ≥ 20 px). */
export const MIN_ACTIVE_PX = 20;

/**
 * Font size that lets a line of `height` px at `fontPx` fit in `available` px.
 * Wrapped text height grows roughly with the square of the font size (more
 * rows, each taller), hence the square root.
 */
export function fitFontPx(fontPx: number, height: number, available: number, minPx = MIN_ACTIVE_PX): number {
  if (height <= available || height <= 0) return fontPx;
  return Math.max(minPx, Math.floor(fontPx * Math.sqrt(available / height)));
}

/** Room for the active line below the anchor. */
export function fitRoom(viewportHeight: number): number {
  return viewportHeight * (1 - FOLLOW_ANCHOR) - FIT_MARGIN;
}

/** Lyrics size presets (spec table, px); "perf" is Performance view. */
export type LyricsSize = "s" | "m" | "l" | "perf";
export const LYRICS_SIZES: LyricsSize[] = ["s", "m", "l"];
/** Performance view never fits its active line below this. */
export const PERF_MIN_ACTIVE_PX = 24;
const SIZE_KEY = "sonereli-lyrics-size";

export function loadLyricsSize(): LyricsSize {
  try {
    const value = localStorage.getItem(SIZE_KEY);
    if (value === "s" || value === "m" || value === "l") return value;
  } catch {
    /* storage unavailable */
  }
  return "m";
}

export function saveLyricsSize(size: LyricsSize): void {
  try {
    localStorage.setItem(SIZE_KEY, size);
  } catch {
    /* storage unavailable */
  }
}
