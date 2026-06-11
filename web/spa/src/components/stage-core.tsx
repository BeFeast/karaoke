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

export interface TimedLine {
  /** Line start in seconds (from the LRC timestamp). */
  t: number;
  /** Derived sing duration in seconds (LRC carries no durations). */
  d: number;
  text: string;
}

// LRC gives start timestamps only. A line "sings" until the next one starts,
// except across instrumental breaks: an interval longer than MAX_LINE_S reads
// as a break, so the line keeps a typical sung length and the remainder
// becomes the gap the setlist bulbs count down. The last line gets the time
// to track end when known, capped the same way.
const MAX_LINE_S = 10;
const BREAK_LINE_S = 5;

export function timeLines(
  lines: { t: number; text: string }[],
  duration: number | null,
): TimedLine[] {
  return lines.map((line, i) => {
    const next = lines[i + 1];
    const interval = next
      ? next.t - line.t
      : duration != null && duration > line.t
        ? duration - line.t
        : BREAK_LINE_S;
    const d = interval > MAX_LINE_S ? BREAK_LINE_S : Math.max(0.5, interval);
    return { t: line.t, d, text: line.text };
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

// which lyric line is current, and its wipe % (core.jsx:79-88, verbatim logic)
export function lyricState(lines: TimedLine[], pos: number): LyricState {
  let idx = -1;
  for (let i = 0; i < lines.length; i++) if (pos >= lines[i].t) idx = i;
  const cur = idx >= 0 ? lines[idx] : null;
  const sung = cur ? Math.min(1, (pos - cur.t) / cur.d) : 0;
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
  const drag = (e: React.PointerEvent) => {
    e.preventDefault();
    const move = (ev: { clientY: number }) => {
      const track = trackRef.current;
      if (!track) return;
      const r = track.getBoundingClientRect();
      const pct = 100 - ((ev.clientY - r.top) / r.height) * 100;
      onChange(Math.round(Math.max(0, Math.min(100, pct))));
    };
    move(e);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const shown = ducked ? Math.min(value, 8) : value;
  return (
    <div style={{ display: "grid", justifyItems: "center", gap: 6, userSelect: "none" }}>
      <span className="m-mono" style={{ fontSize: 10, letterSpacing: "0.1em", color }}>{label}</span>
      <div ref={trackRef} onPointerDown={drag} style={{ position: "relative", width: 34, height: 132, display: "flex", justifyContent: "center", cursor: "ns-resize", touchAction: "none" }}>
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
