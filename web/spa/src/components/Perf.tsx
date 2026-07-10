// KARAOKE — Performance mode (Marquee port, #156): the across-the-room
// fullscreen lyrics overlay on the Stage room (#/job/:token). Literal port of
// design/claude-export/proto/perf.jsx PerfScreen (:3-84) — the vendored copy
// at commit N is the byte-for-byte diff baseline. The phone layout follows
// sections/m-perf.jsx PhonePerf (:4-68) inner pattern; desktop/TV is
// LaptopPerfBoard (:71-109) = the Final PerfScreen. The IOSDevice frame is
// canvas chrome and is NOT shipped. Component state inside the item route —
// no new hash route: the overlay is position:fixed over the Stage page and is
// rendered inside KaraokePlayer, so the player/engine stay mounted and
// playback survives enter/exit untouched.
//
// Adaptations (real data only — the engine API is untouched):
//   * pb/mix simulation → the existing KaraokePlayerApi: play/pause →
//     playPause, blend slider → setVocalLevel (the ONE equal-power crossfade
//     parameter — same recorded deviation as the Stage port: no per-stem
//     gains), top-strip time + progress bar → the quantized currentTime
//     state, the current-line wipe → the raw subscribeTime tick feed
//     (bypasses the React render path entirely).
//   * SONG/usePlayback clock → real SharePayload title/artist and the timed
//     synced-lyrics lines (stage-core.timeLines over the LRC payload, so the
//     wipe + MBulbs gap countdown derive from real timestamps); no synced
//     data → centered plain text, no fake timing.
//   * lyricScale baked at 150 (FINAL.lyricScale) — no TweaksPanel pickers;
//     ◐ shares the stage room's persisted day/night theme (token classes).
//   * Token resolutions (audit item 4): perf.jsx:40 radial background blends
//     var(--bg-soft) → var(--bg) (day & night resolve via the room's token
//     set); perf.jsx:52 lyric text-shadow → var(--glow) (green bake,
//     night-only — NOT the export's amber rgba); perf.jsx:75 slider gradient
//     mid-stops → color-mix(in oklab, var(--inst) X%, var(--vox)); the phone
//     thumb shadow (m-perf.jsx:46) → var(--shadow-sm).
//   * prefers-reduced-motion: the 3 s idle fade is disabled (controls stay
//     visible) and the wipe doesn't sweep — the current line fills whole.
//   * Recorded deviations: pointerdown also wakes the controls (a touch tap
//     emits no pointermove); long real-world lines fit-to-width (#166): the
//     active line downscales its font via --line-scale, or wraps to two
//     balanced rows below FIT_FLOOR with the wipe fill split across the rows;
//     prev/next get the same overflow guard at their scale (the pure math
//     lives in lineFit.ts); play/skip disable until the engine is ready.

import { type CSSProperties, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { usePhoneLayout } from "../lib/layout";
import { railPct } from "../player/engineMath";
import type { KaraokePlayerApi } from "../player/useKaraokePlayer";
import type { StageTheme } from "../theme";
import { FIT_FLOOR, balancedSplit, fitLineScale, splitFill } from "./lineFit";
import { MBulbs, MicMark } from "./marks";
// BLEND_GRADIENT + the phone rail recipe moved to stage-core (#185) so the
// phone console reuses them; the desktop rail below still paints the gradient.
import { BLEND_GRADIENT, BlendRail, lyricState, type TimedLine } from "./stage-core";

// FINAL.lyricScale (final-app.jsx:9) — baked, no scale picker.
const LYRIC_SCALE = 150;

/** mm:ss for a (possibly NaN/Infinity) seconds value (the design's fmtTime). */
function fmtTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Available width for a lyric row — the #166 cap: min(92vw, container
// content width), so nothing ever reaches the viewport edges.
function availWidth(container: HTMLElement): number {
  return Math.min(window.innerWidth * 0.92, container.clientWidth);
}

// The current line rides the engine's raw tick feed (subscribeTime) and
// writes the DOM directly — per-tick wipe updates bypass React rendering
// (the same seam as the console LiveLyricWipe). Reduced motion: no sweep —
// the line fills whole while it's current.
//
// Fit-to-width (#166): real LRC lines overflow the 150%-scaled row the design
// assumes. On line change (never per tick) the natural single-row width is
// measured at scale 1 and fitLineScale picks a proportional --line-scale —
// font-size based, so layout and centering stay true (the previous
// transform:scale left the overflowing box off-center and still clipped).
// Below FIT_FLOOR the line wraps at the balancedSplit whitespace into two
// stacked .m-wipe rows and splitFill carries the progressive fill across
// them; a single giant word (no split point) falls back to the exact-fit
// downscale so nothing ever clips.
function PerfWipe({
  lines,
  subscribeTime,
  currentTime,
  size,
  reducedMotion,
}: {
  lines: TimedLine[];
  subscribeTime: (cb: (t: number) => void) => () => void;
  /** Mount-time playhead — paints the wipe before the first engine tick. */
  currentTime: number;
  size: number;
  reducedMotion: boolean;
}) {
  const rootRef = useRef<HTMLSpanElement | null>(null);
  const headRowRef = useRef<HTMLSpanElement | null>(null);
  const headDimRef = useRef<HTMLSpanElement | null>(null);
  const headFillRef = useRef<HTMLSpanElement | null>(null);
  const tailWrapRef = useRef<HTMLSpanElement | null>(null);
  const tailRowRef = useRef<HTMLSpanElement | null>(null);
  const tailDimRef = useRef<HTMLSpanElement | null>(null);
  const tailFillRef = useRef<HTMLSpanElement | null>(null);
  const mountTimeRef = useRef(currentTime);
  mountTimeRef.current = currentTime;
  useEffect(() => {
    let lastText: string | null = null;
    // Wrapped-mode [headLen, tailLen] for splitFill; null = single row.
    let split: [number, number] | null = null;

    const relayout = (text: string) => {
      const root = rootRef.current;
      const headRow = headRowRef.current;
      const headDim = headDimRef.current;
      const headFill = headFillRef.current;
      const tailWrap = tailWrapRef.current;
      const tailRow = tailRowRef.current;
      const tailDim = tailDimRef.current;
      const tailFill = tailFillRef.current;
      if (!root || !headRow || !headDim || !headFill || !tailWrap || !tailRow || !tailDim || !tailFill) return;
      // Reset to a natural single row at scale 1 and measure.
      split = null;
      root.style.setProperty("--line-scale", "1");
      headDim.textContent = text;
      headFill.textContent = text;
      tailWrap.style.display = "none";
      tailDim.textContent = "";
      tailFill.textContent = "";
      const avail = availWidth(root);
      if (!text || !(avail > 0)) return;
      let fit = fitLineScale(headRow.getBoundingClientRect().width, avail);
      const halves = fit.wrap ? balancedSplit(text) : null;
      if (halves) {
        headDim.textContent = halves[0];
        headFill.textContent = halves[0];
        tailDim.textContent = halves[1];
        tailFill.textContent = halves[1];
        tailWrap.style.display = "block";
        split = [halves[0].length, halves[1].length];
        fit = fitLineScale(Math.max(headRow.getBoundingClientRect().width, tailRow.getBoundingClientRect().width), avail);
      }
      root.style.setProperty("--line-scale", String(fit.scale));
      // Glyph metrics aren't perfectly linear under font-size scaling —
      // correct any residual overflow so no character ever clips.
      const widest = Math.max(headRow.getBoundingClientRect().width, halves ? tailRow.getBoundingClientRect().width : 0);
      if (widest > avail) root.style.setProperty("--line-scale", String(fit.scale * (avail / widest)));
    };

    const apply = (t: number) => {
      const headFill = headFillRef.current;
      const tailFill = tailFillRef.current;
      if (!headFill || !tailFill) return;
      const ls = lyricState(lines, t);
      const text = ls.cur ? ls.cur.text : "";
      if (lastText !== text) {
        lastText = text;
        relayout(text);
      }
      const sung = ls.cur ? (reducedMotion ? 100 : ls.sung) : 0;
      if (split) {
        const [head, tail] = splitFill(sung, split[0], split[1]);
        headFill.style.width = `${head}%`;
        tailFill.style.width = `${tail}%`;
      } else {
        headFill.style.width = `${sung}%`;
      }
    };
    apply(mountTimeRef.current);
    // Container/viewport resizes change the available width — re-fit the
    // current line. Width-gated so font-size-driven height changes from
    // relayout itself never re-trigger the observer.
    const root = rootRef.current;
    let lastW = root ? root.clientWidth : -1;
    const ro =
      root && typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            const w = root.clientWidth;
            if (w === lastW) return;
            lastW = w;
            if (lastText !== null) relayout(lastText);
          })
        : null;
    if (ro && root) ro.observe(root);
    const unsub = subscribeTime(apply);
    return () => {
      ro?.disconnect();
      unsub();
    };
  }, [lines, subscribeTime, reducedMotion, size]);
  // The recipe's font-family is set per .m-wipe row (a class rule on the row
  // would beat inheritance from the root); --line-scale is written only by
  // relayout, so React never fights the manual style updates.
  const rowStyle: CSSProperties = { fontFamily: "var(--font-display)" };
  return (
    <span ref={rootRef} style={{ display: "block", fontWeight: 700, fontSize: `calc(${size}px * var(--line-scale, 1))` }}>
      <span style={{ display: "block" }}>
        <span ref={headRowRef} className="m-wipe" dir="auto" style={rowStyle}>
          <span className="w-dim" style={{ color: "var(--lyric-dim)" }} ref={headDimRef}></span>
          <span className="w-fill" style={{ color: "var(--accent)" }} ref={headFillRef} aria-hidden="true"></span>
        </span>
      </span>
      <span ref={tailWrapRef} style={{ display: "none" }}>
        <span ref={tailRowRef} className="m-wipe" dir="auto" style={rowStyle}>
          <span className="w-dim" style={{ color: "var(--lyric-dim)" }} ref={tailDimRef}></span>
          <span className="w-fill" style={{ color: "var(--accent)" }} ref={tailFillRef} aria-hidden="true"></span>
        </span>
      </span>
    </span>
  );
}

// Neighbour (prev/next) lines — the same overflow guard at their scale
// (#166): render nowrap, measure once per text change (two-pass
// useLayoutEffect, re-rendered before paint so nothing flashes), and
// downscale the font to fit. Below FIT_FLOOR the line switches to a natural
// balanced wrap at the floor scale instead — there is no fill to preserve
// here, so CSS does the splitting.
function FitLine({ text }: { text: string }) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const [fit, setFit] = useState<{ forText: string | null; scale: number; wrap: boolean }>({
    forText: null,
    scale: 1,
    wrap: false,
  });
  const measured = fit.forText === text;
  useLayoutEffect(() => {
    if (measured) return;
    const el = ref.current;
    if (!el) return;
    const avail = el.parentElement ? availWidth(el.parentElement) : 0;
    const f = fitLineScale(el.getBoundingClientRect().width, avail);
    setFit({ forText: text, scale: f.wrap ? FIT_FLOOR : f.scale, wrap: f.wrap });
  });
  useEffect(() => {
    const onResize = () => setFit((s) => (s.forText === null ? s : { ...s, forText: null }));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return (
    <span
      ref={ref}
      dir="auto"
      style={{
        display: "inline-block",
        whiteSpace: measured && fit.wrap ? "normal" : "nowrap",
        textWrap: measured && fit.wrap ? "balance" : undefined,
        fontSize: measured ? `${fit.scale}em` : "1em",
      }}
    >
      {text}
    </span>
  );
}

export interface PerfProps {
  player: KaraokePlayerApi;
  /** Real SharePayload title (already defaulted by the Stage). */
  title: string;
  artist: string | null;
  /** Timed synced-lyrics lines; empty = no synced data (plain fallback). */
  lines: TimedLine[];
  /** Plain lyrics text for the no-synced-data fallback. */
  plain: string | null;
  /** The stage room's persisted ◐ theme. */
  theme: StageTheme;
  onToggleTheme: () => void;
  onExit: () => void;
  reducedMotion: boolean;
}

export function Perf({ player, title, artist, lines, plain, theme, onToggleTheme, onExit, reducedMotion }: PerfProps) {
  const phone = usePhoneLayout();
  const [idle, setIdle] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  // Controls fade after 3 s idle, pointer activity wakes (perf.jsx:8-12).
  // Reduced motion: no fade — `idle` never sets, controls stay visible.
  const poke = useCallback(() => {
    if (reducedMotion) return;
    setIdle(false);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setIdle(true), 3000);
  }, [reducedMotion]);

  useEffect(() => {
    poke();
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") onExit();
    };
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("keydown", esc);
      window.clearTimeout(timer.current);
    };
  }, [poke, onExit]);

  // Line ladder + gap countdown from the quantized playhead (~10 Hz) — line
  // granularity; only the wipe fill needs the raw tick feed (PerfWipe).
  const ls = lyricState(lines, player.currentTime);
  const gapBulbs = ls.inGap && ls.next ? Math.min(8, Math.ceil(ls.gap)) : 0;
  const sz = LYRIC_SCALE / 100;
  const vox = Math.round(player.vocalLevel * 100);
  const progress = (player.duration > 0 ? (player.currentTime / player.duration) * 100 : 0) + "%";

  // perf.jsx:24-35 verbatim drag mechanics; setMix → the single crossfade.
  const dragBlend = (e: React.PointerEvent) => {
    const rail = e.currentTarget;
    const move = (ev: { clientX: number }) => {
      const r = rail.getBoundingClientRect();
      player.setVocalLevel(railPct(ev.clientX, r.left, r.width) / 100);
    };
    move(e);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const day = theme === "day";
  const fade: CSSProperties = { opacity: idle ? 0 : 1, transition: reducedMotion ? undefined : "opacity .5s" };
  // Lyric metrics: phone per m-perf.jsx:22-28, desktop/TV per perf.jsx:51-57
  // (the design minHeights), everything × the baked 150% scale.
  const m = phone
    ? { prev: 17, cur: 29, next: 18, prevH: 22, curH: 35, nextH: 23, gap: 16, pad: "0 26px", track: "-0.01em", lh: 1.22, lineLh: 1.3 }
    : { prev: 20, cur: 41, next: 22, prevH: 26, curH: 48, nextH: 28, gap: 18, pad: "0 64px", track: "-0.015em", lh: 1.15, lineLh: undefined };

  return (
    <div
      className={day ? "m-booth" : "m-stage"}
      onPointerMove={poke}
      onPointerDown={poke}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 50,
        display: "flex",
        flexDirection: "column",
        // perf.jsx:40 — the radial literals resolve via the room tokens.
        background: "radial-gradient(120% 90% at 50% 112%, var(--bg-soft) 0%, var(--bg) 56%)",
      }}
    >
      {/* top strip (perf.jsx:41-48; phone strip per m-perf.jsx:13-18) */}
      <div
        className="m-mono"
        style={{
          display: "flex",
          alignItems: "center",
          gap: phone ? 9 : 12,
          padding: phone ? "calc(env(safe-area-inset-top, 0px) + 12px) 16px 4px" : "16px 24px",
          fontSize: phone ? 11 : 11.5,
          color: "var(--muted)",
          ...fade,
        }}
      >
        <MicMark size={phone ? 20 : 22} />
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {title}
          {artist ? ` — ${artist}` : ""}
        </span>
        <span style={{ flex: 1 }}></span>
        <span style={{ whiteSpace: "nowrap" }}>{fmtTime(player.currentTime)} / {fmtTime(player.duration)}</span>
        <button className="m-btn sm ghost" type="button" title="Day / night" onClick={onToggleTheme}>◐</button>
        <button className="m-btn sm ghost" type="button" onClick={onExit} aria-label="Exit performance mode">
          {phone ? "✕" : "esc ✕"}
        </button>
      </div>

      {/* center: prev / current (wipe) / next + gap countdown (perf.jsx:50-61) */}
      {lines.length > 0 ? (
        <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: m.gap, padding: m.pad, textAlign: "center", minHeight: 0 }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: m.prev * sz, fontWeight: 500, color: "var(--lyric-prev)", lineHeight: m.lineLh, minHeight: m.prevH * sz }}>
            {ls.prev ? <FitLine text={ls.prev.text} /> : ""}
          </div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: m.cur * sz, fontWeight: 700, letterSpacing: m.track, lineHeight: m.lh, minHeight: m.curH * sz, textShadow: "var(--glow)" }}>
            {ls.cur ? (
              <PerfWipe
                lines={lines}
                subscribeTime={player.subscribeTime}
                currentTime={player.currentTime}
                size={m.cur * sz}
                reducedMotion={reducedMotion}
              />
            ) : (
              <span style={{ color: "var(--lyric-dim)" }}>
                {ls.next ? "get ready…" : player.currentTime < 2 ? title : "instrumental"}
              </span>
            )}
          </div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: m.next * sz, fontWeight: 540, color: "var(--lyric-next)", lineHeight: m.lineLh, minHeight: m.nextH * sz }}>
            {ls.next ? <FitLine text={ls.next.text} /> : "— end —"}
          </div>
          <div style={{ display: "flex", justifyContent: "center", marginTop: 4, minHeight: 8 }}>
            {gapBulbs > 0 && <MBulbs n={8} lit={gapBulbs} size={7} gap={phone ? 9 : 10} />}
          </div>
        </div>
      ) : (
        // Plain-lyrics fallback — centered plain text, NO fake timing.
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflowY: "auto", padding: m.pad, textAlign: "center" }}>
          {plain ? (
            <div dir="auto" style={{ margin: "auto", padding: "24px 0", fontFamily: "var(--font-display)", fontSize: m.prev * sz, fontWeight: 500, lineHeight: 1.5, color: "var(--fg-soft)", whiteSpace: "pre-wrap" }}>
              {plain}
            </div>
          ) : (
            <div style={{ margin: "auto", padding: "24px 0" }}>
              <div style={{ fontFamily: "var(--font-display)", fontSize: m.cur * sz, fontWeight: 700, letterSpacing: m.track, lineHeight: m.lh, textShadow: "var(--glow)" }}>{title}</div>
              <div className="m-mono" style={{ marginTop: 14, fontSize: 11.5, color: "var(--muted)" }}>no lyrics for this track</div>
            </div>
          )}
        </div>
      )}

      {/* bottom controls — phone thumb zone (m-perf.jsx:35-56) / desktop row (perf.jsx:63-81) */}
      {phone ? (
        <div style={{ padding: "0 22px calc(env(safe-area-inset-bottom, 0px) + 28px)", display: "grid", gap: 18, ...fade }}>
          <BlendRail vox={vox} onVox={(pct) => player.setVocalLevel(pct / 100)} reducedMotion={reducedMotion} />
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 26 }}>
            <button className="m-btn" type="button" onClick={() => player.skip(-5)} disabled={!player.ready} style={{ width: 52, height: 52, borderRadius: "50%", justifyContent: "center", fontSize: 13 }}>−5s</button>
            <button
              className="m-btn primary"
              type="button"
              onClick={player.playPause}
              disabled={!player.ready}
              aria-label={player.playing ? "Pause" : "Play"}
              style={{ width: 72, height: 72, borderRadius: "50%", justifyContent: "center", fontSize: 24, boxShadow: "var(--glow)" }}
            >
              {player.playing ? "❚❚" : "▶"}
            </button>
            <button className="m-btn" type="button" onClick={() => player.skip(5)} disabled={!player.ready} style={{ width: 52, height: 52, borderRadius: "50%", justifyContent: "center", fontSize: 13 }}>+5s</button>
          </div>
          <div className="m-wipebar" style={{ "--wipe": progress } as CSSProperties}><i></i></div>
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 20, padding: "14px 24px 22px", ...fade }}>
          <button
            className="m-btn primary"
            type="button"
            onClick={player.playPause}
            disabled={!player.ready}
            aria-label={player.playing ? "Pause" : "Play"}
            style={{ width: 46, height: 46, borderRadius: "50%", justifyContent: "center", fontSize: 16, boxShadow: "var(--glow)" }}
          >
            {player.playing ? "❚❚" : "▶"}
          </button>
          <div style={{ width: 320 }}>
            <div className="m-mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--muted)", marginBottom: 6 }}>
              <span className="m-stem inst">karaoke</span>
              <span>vox {vox}%</span>
              <span className="m-stem vox">full voice</span>
            </div>
            <div onPointerDown={dragBlend} style={{ position: "relative", height: 14, display: "flex", alignItems: "center", cursor: "ew-resize", touchAction: "none" }}>
              <div style={{ position: "absolute", left: 0, right: 0, height: 5, borderRadius: 3, background: BLEND_GRADIENT }}></div>
              <span style={{ position: "absolute", left: vox + "%", top: "50%", transform: "translate(-50%,-50%)", width: 15, height: 15, borderRadius: "50%", background: "var(--fg)", border: "3px solid var(--bg)", transition: reducedMotion ? undefined : "left .1s" }}></span>
            </div>
          </div>
          <span style={{ flex: 1 }}></span>
          <div className="m-wipebar" style={{ "--wipe": progress, width: 200 } as CSSProperties}><i></i></div>
        </div>
      )}
    </div>
  );
}

export default Perf;
