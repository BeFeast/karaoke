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
//     emits no pointermove); the wipe scales-to-fit long real-world lines
//     (.m-wipe is nowrap by recipe — the fill overlay can't re-wrap);
//     play/skip disable until the engine is ready.

import { type CSSProperties, useCallback, useEffect, useRef, useState } from "react";
import type { KaraokePlayerApi } from "../player/useKaraokePlayer";
import type { StageTheme } from "../theme";
import { MBulbs, MicMark } from "./marks";
import { lyricState, type TimedLine } from "./stage-core";

// FINAL.lyricScale (final-app.jsx:9) — baked, no scale picker.
const LYRIC_SCALE = 150;

// perf.jsx:75 / m-perf.jsx:42 — the karaoke ↔ full-voice rail. The export's
// raw mid-stop literals resolve as oklab mixes of the duet pair (audit item 4).
const BLEND_GRADIENT =
  "linear-gradient(90deg, var(--inst), color-mix(in oklab, var(--inst) 72%, var(--vox)) 45%, color-mix(in oklab, var(--inst) 28%, var(--vox)) 55%, var(--vox))";

/** mm:ss for a (possibly NaN/Infinity) seconds value (the design's fmtTime). */
function fmtTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Phone (thumb-zone) vs desktop/TV layout — m-perf.jsx PhonePerf vs
// LaptopPerfBoard. Live matchMedia so rotation/resize re-lays-out.
const PHONE_QUERY = "(max-width: 640px)";

function usePhoneLayout(): boolean {
  const [phone, setPhone] = useState(
    () => typeof window !== "undefined" && !!window.matchMedia?.(PHONE_QUERY).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia?.(PHONE_QUERY);
    if (!mq) return;
    const onChange = () => setPhone(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return phone;
}

// The current line rides the engine's raw tick feed (subscribeTime) and
// writes the DOM directly — per-tick wipe updates bypass React rendering
// (the same seam as the console LiveLyricWipe). Reduced motion: no sweep —
// the line fills whole while it's current.
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
  const dimRef = useRef<HTMLSpanElement | null>(null);
  const fillRef = useRef<HTMLSpanElement | null>(null);
  const mountTimeRef = useRef(currentTime);
  mountTimeRef.current = currentTime;
  useEffect(() => {
    const apply = (t: number) => {
      const root = rootRef.current;
      const dim = dimRef.current;
      const fill = fillRef.current;
      if (!root || !dim || !fill) return;
      const ls = lyricState(lines, t);
      const text = ls.cur ? ls.cur.text : "";
      if (dim.textContent !== text) {
        dim.textContent = text;
        fill.textContent = text;
        // .m-wipe is nowrap by recipe (the fill overlay can't re-wrap), so a
        // long real-world line scales down to the row instead of bleeding
        // off-screen — across-the-room readability over geometric fidelity.
        const avail = root.parentElement?.clientWidth ?? 0;
        root.style.transform =
          avail > 0 && root.scrollWidth > avail ? `scale(${avail / root.scrollWidth})` : "none";
      }
      fill.style.width = `${ls.cur ? (reducedMotion ? 100 : ls.sung) : 0}%`;
    };
    apply(mountTimeRef.current);
    return subscribeTime(apply);
  }, [lines, subscribeTime, reducedMotion]);
  return (
    <span ref={rootRef} className="m-wipe" style={{ fontSize: size, fontFamily: "var(--font-display)", fontWeight: 700 }}>
      <span className="w-dim" style={{ color: "var(--lyric-dim)" }} ref={dimRef}></span>
      <span className="w-fill" style={{ color: "var(--accent)" }} ref={fillRef} aria-hidden="true"></span>
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
      const pct = ((ev.clientX - r.left) / r.width) * 100;
      player.setVocalLevel(Math.round(Math.max(0, Math.min(100, pct))) / 100);
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
            {ls.prev ? ls.prev.text : ""}
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
            {ls.next ? ls.next.text : "— end —"}
          </div>
          <div style={{ display: "flex", justifyContent: "center", marginTop: 4, minHeight: 8 }}>
            {gapBulbs > 0 && <MBulbs n={8} lit={gapBulbs} size={7} gap={phone ? 9 : 10} />}
          </div>
        </div>
      ) : (
        // Plain-lyrics fallback — centered plain text, NO fake timing.
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflowY: "auto", padding: m.pad, textAlign: "center" }}>
          {plain ? (
            <div style={{ margin: "auto", padding: "24px 0", fontFamily: "var(--font-display)", fontSize: m.prev * sz, fontWeight: 500, lineHeight: 1.5, color: "var(--fg-soft)", whiteSpace: "pre-wrap" }}>
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
          <div>
            <div className="m-mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--muted)", marginBottom: 8 }}>
              <span className="m-stem inst">karaoke</span>
              <span style={{ color: "var(--fg-soft)" }}>vox {vox}%</span>
              <span className="m-stem vox">full voice</span>
            </div>
            <div onPointerDown={dragBlend} style={{ position: "relative", height: 28, display: "flex", alignItems: "center", cursor: "ew-resize", touchAction: "none" }}>
              <div style={{ position: "absolute", left: 0, right: 0, height: 6, borderRadius: 3, background: BLEND_GRADIENT }}></div>
              <span style={{ position: "absolute", left: vox + "%", top: "50%", transform: "translate(-50%,-50%)", width: 26, height: 26, borderRadius: "50%", background: "var(--fg)", border: "4px solid var(--bg)", boxShadow: "var(--shadow-sm)", transition: reducedMotion ? undefined : "left .1s" }}></span>
            </div>
          </div>
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
