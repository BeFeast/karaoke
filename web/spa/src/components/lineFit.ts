// KARAOKE — Performance overlay line-fit math (#166). Real LRC lines can be
// far longer than the design's demo lyrics (proto/perf.jsx assumes lines that
// fit at the 150% scale); the live Pantera test clipped the active line at
// the right viewport edge. These pure helpers decide, per line, between a
// proportional font downscale and a balanced two-row wrap — the component
// (Perf.tsx) measures the DOM and applies the result; no DOM in here so the
// policy is bun-testable.

/** Below this fraction of the base scale a downscale reads worse than a wrap. */
export const FIT_FLOOR = 0.6;

export interface LineFit {
  /**
   * Font-scale multiplier that makes the line fit, ≤ base (never upscales).
   * Always the exact-fit value — even when `wrap` is set — so callers that
   * cannot wrap (single giant word) still have a no-clip fallback.
   */
  scale: number;
  /** True when the exact-fit scale falls below FIT_FLOOR × base — wrap instead. */
  wrap: boolean;
}

/**
 * Fit a measured natural line width into the available width.
 *
 * `naturalWidth` is the line's single-row width as rendered at `base` scale
 * (measure via scrollWidth/getBoundingClientRect on a nowrap element);
 * `available` is min(92vw, container content width). Degenerate measurements
 * (0/NaN — detached node, hidden overlay) fit nothing, so they keep the base
 * scale rather than collapsing the line.
 */
export function fitLineScale(naturalWidth: number, available: number, base = 1): LineFit {
  if (!(naturalWidth > 0) || !(available > 0)) return { scale: base, wrap: false };
  const fit = Math.min(1, available / naturalWidth);
  return { scale: base * fit, wrap: fit < FIT_FLOOR };
}

/**
 * Split a line into two visually balanced rows at the whitespace nearest the
 * character midpoint. Returns null when there is no interior whitespace to
 * split on (single giant word) — the caller falls back to pure downscale.
 */
export function balancedSplit(text: string): [string, string] | null {
  const t = text.trim();
  const mid = t.length / 2;
  let best = -1;
  for (let i = 1; i < t.length - 1; i++) {
    if (/\s/.test(t[i]) && (best === -1 || Math.abs(i - mid) < Math.abs(best - mid))) best = i;
  }
  if (best === -1) return null;
  const head = t.slice(0, best).trimEnd();
  const tail = t.slice(best + 1).trimStart();
  if (!head || !tail) return null;
  return [head, tail];
}

/**
 * Progressive-fill widths for a wrapped line. The wipe assumes time maps
 * linearly over the line's characters (same assumption as the single-row
 * fill), so a sung% of the whole line becomes: head row fills over its share
 * of the characters first, then the tail row. The dropped separator space
 * counts toward the total so the head finishes exactly at the row boundary.
 * Returns [head%, tail%], each clamped to 0–100.
 */
export function splitFill(sung: number, headLen: number, tailLen: number): [number, number] {
  const total = headLen + 1 + tailLen;
  const chars = (Math.max(0, Math.min(100, sung)) / 100) * total;
  const head = headLen > 0 ? Math.max(0, Math.min(100, (chars / headLen) * 100)) : 100;
  const tail = tailLen > 0 ? Math.max(0, Math.min(100, ((chars - headLen - 1) / tailLen) * 100)) : 0;
  return [head, tail];
}
