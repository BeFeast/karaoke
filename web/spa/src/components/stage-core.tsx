// KARAOKE — Stage room core widgets (Marquee port, #154).
// Adapted from design/claude-export/proto/core.jsx (the vendored copy is the
// byte-for-byte diff baseline). Commit N+1 strips the prototype's simulation
// layer — SONG, usePlayback, pBars, fmtTime — leaving the visual components
// and lyric math, typed and wired to real data:
//   * lyricState (core.jsx:79-88): logic verbatim over real LRC lines; line
//     durations don't exist in LRC, so timeLines derives them first.
//   * ProtoFader (core.jsx:133-164): geometry/transitions verbatim; the rgba
//     thumb shadow routed through var(--shadow-sm) (guard 2 / audit item 4).
//   * ProtoWave (core.jsx:103-130) is NOT ported as a component — its visual
//     contract (duet colors from --vox/--inst, click-to-seek) restyles the
//     wavesurfer container in player/KaraokePlayer.tsx instead; a second
//     waveform here would violate the visual-only wavesurfer rule.

import { useRef } from "react";
import { useActiveTouchStart } from "../lib/touchGuards";
import { railPct } from "../player/engineMath";
import type { LyricLine } from "./SyncedLyrics";

const TOUCH_DRAG_GUARDS = {
  touchAction: "none",
  userSelect: "none",
  WebkitUserSelect: "none",
  WebkitTouchCallout: "none",
} as const;

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

// draggable vertical fader (core.jsx:133-164)
export function ProtoFader({
  label,
  color,
  value,
  onChange,
  ducked,
}: {
  label: string;
  color: string;
  /** Fader position, 0–100. */
  value: number;
  onChange: (value: number) => void;
  ducked?: boolean;
}) {
  const trackRef = useRef<HTMLDivElement | null>(null);
  const moveToClientY = (clientY: number) => {
    const track = trackRef.current;
    if (!track) return;
    const r = track.getBoundingClientRect();
    const pct = 100 - ((clientY - r.top) / r.height) * 100;
    onChange(Math.round(Math.max(0, Math.min(100, pct))));
  };
  const drag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "touch") return;
    e.preventDefault();
    const move = (ev: { clientY: number }) => moveToClientY(ev.clientY);
    moveToClientY(e.clientY);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  useActiveTouchStart(trackRef, (e) => {
    e.preventDefault();
    const touch = e.touches[0];
    if (!touch) return;
    moveToClientY(touch.clientY);
    const move = (ev: TouchEvent) => {
      ev.preventDefault();
      const next = ev.touches[0] ?? ev.changedTouches[0];
      if (next) moveToClientY(next.clientY);
    };
    const up = () => {
      window.removeEventListener("touchmove", move);
      window.removeEventListener("touchend", up);
      window.removeEventListener("touchcancel", up);
    };
    window.addEventListener("touchmove", move, { passive: false });
    window.addEventListener("touchend", up);
    window.addEventListener("touchcancel", up);
  });
  const shown = ducked ? Math.min(value, 8) : value;
  return (
    <div style={{ display: "grid", justifyItems: "center", gap: 6, userSelect: "none" }}>
      <span className="m-mono" style={{ fontSize: 10, letterSpacing: "0.1em", color }}>{label}</span>
      <div ref={trackRef} onPointerDown={drag} style={{ position: "relative", width: 34, height: 132, display: "flex", justifyContent: "center", cursor: "ns-resize", ...TOUCH_DRAG_GUARDS }}>
        <div style={{ width: 4, borderRadius: 2, background: "var(--border)", position: "relative" }}>
          <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: shown + "%", background: color, borderRadius: 2, opacity: ducked ? 0.45 : 0.85, transition: "height .12s" }}></div>
          <div style={{
            position: "absolute", left: "50%", bottom: shown + "%", transform: "translate(-50%, 50%)",
            width: 28, height: 14, borderRadius: 4, background: "var(--fg)",
            border: "1px solid var(--bg)", boxShadow: "var(--shadow-sm)", transition: "bottom .12s",
          }}></div>
        </div>
      </div>
      <span className="m-mono" style={{ fontSize: 10, color: ducked ? color : "var(--muted)" }}>{ducked ? "duck" : shown + "%"}</span>
    </div>
  );
}

// perf.jsx:75 / m-perf.jsx:42 — the karaoke ↔ full-voice rail. The export's
// raw mid-stop literals resolve as oklab mixes of the duet pair (audit item 4).
export const BLEND_GRADIENT =
  "linear-gradient(90deg, var(--inst), color-mix(in oklab, var(--inst) 72%, var(--vox)) 45%, color-mix(in oklab, var(--inst) 28%, var(--vox)) 55%, var(--vox))";

// phone blend rail (m-perf.jsx:36-49) — the design's stated mobile fader
// alternative (the phone perf screen has no vertical faders). Extracted
// verbatim from Perf's phone branch (#156) so the phone console (#185)
// reuses the SAME token-resolved recipe; Perf's rendering is unchanged.
export function BlendRail({
  vox,
  onVox,
  reducedMotion,
}: {
  /** Vocal level, 0–100. */
  vox: number;
  onVox: (pct: number) => void;
  reducedMotion: boolean;
}) {
  // perf.jsx:24-35 drag mechanics; position→percent is the pure railPct.
  const railRef = useRef<HTMLDivElement | null>(null);
  const moveToClientX = (rail: HTMLElement, clientX: number) => {
    const r = rail.getBoundingClientRect();
    onVox(railPct(clientX, r.left, r.width));
  };
  const drag = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType === "touch") return;
    const rail = e.currentTarget;
    const move = (ev: { clientX: number }) => moveToClientX(rail, ev.clientX);
    moveToClientX(rail, e.clientX);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  useActiveTouchStart(railRef, (e) => {
    e.preventDefault();
    const touch = e.touches[0];
    const rail = railRef.current;
    if (!touch || !rail) return;
    moveToClientX(rail, touch.clientX);
    const move = (ev: TouchEvent) => {
      ev.preventDefault();
      const next = ev.touches[0] ?? ev.changedTouches[0];
      if (next) moveToClientX(rail, next.clientX);
    };
    const up = () => {
      window.removeEventListener("touchmove", move);
      window.removeEventListener("touchend", up);
      window.removeEventListener("touchcancel", up);
    };
    window.addEventListener("touchmove", move, { passive: false });
    window.addEventListener("touchend", up);
    window.addEventListener("touchcancel", up);
  });
  return (
    <div>
      <div className="m-mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--muted)", marginBottom: 8 }}>
        <span className="m-stem inst">karaoke</span>
        <span style={{ color: "var(--fg-soft)" }}>vox {vox}%</span>
        <span className="m-stem vox">full voice</span>
      </div>
      <div ref={railRef} onPointerDown={drag} style={{ position: "relative", height: 28, display: "flex", alignItems: "center", cursor: "ew-resize", ...TOUCH_DRAG_GUARDS }}>
        <div style={{ position: "absolute", left: 0, right: 0, height: 6, borderRadius: 3, background: BLEND_GRADIENT }}></div>
        <span style={{ position: "absolute", left: vox + "%", top: "50%", transform: "translate(-50%,-50%)", width: 26, height: 26, borderRadius: "50%", background: "var(--fg)", border: "4px solid var(--bg)", boxShadow: "var(--shadow-sm)", transition: reducedMotion ? undefined : "left .1s" }}></span>
      </div>
    </div>
  );
}
