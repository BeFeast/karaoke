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
//     state, shared with the current-line highlight.
//   * SONG/usePlayback clock → real SharePayload title/artist and the timed
//     synced-lyrics lines (stage-core.timeLines over the LRC payload, so the
//     wipe + MBulbs gap countdown derive from real timestamps); no synced
//     data → centered plain text, no fake timing.
//   * Readable phone/desktop sizes stay fixed while lyrics wrap.
//     (Day/Night removed in Sonereli UI/UX v1: one dark room.)
//   * Token resolutions (audit item 4): perf.jsx:40 radial background blends
//     var(--bg-soft) → var(--bg) (day & night resolve via the room's token
//     set); perf.jsx:52 lyric text-shadow → var(--glow) (green bake,
//     night-only — NOT the export's amber rgba); perf.jsx:75 slider gradient
//     mid-stops → color-mix(in oklab, var(--inst) X%, var(--vox)); the phone
//     thumb shadow (m-perf.jsx:46) → var(--shadow-sm).
//   * prefers-reduced-motion: the 3 s idle fade is disabled (controls stay
//     visible) and the wipe doesn't sweep — the current line fills whole.

import { type CSSProperties, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { FeedbackButton } from "../feedback/Feedback";
import { usePhoneLayout } from "../lib/layout";
import { railPct } from "../player/engineMath";
import type { KaraokePlayerApi } from "../player/useKaraokePlayer";
import { LyricText } from "./LyricText";
import { MBulbs, PlaybackIcon, SonereliMark } from "./marks";
import { PlainLyrics } from "./SyncedLyrics";
// BLEND_GRADIENT + the phone rail recipe moved to stage-core (#185) so the
// phone console reuses them; the desktop rail below still paints the gradient.
import { BLEND_GRADIENT, BlendRail, lyricState, type TimedLine } from "./stage-core";

/** mm:ss for a (possibly NaN/Infinity) seconds value (the design's fmtTime). */
function fmtTime(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
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
  onExit: () => void;
  reducedMotion: boolean;
}

export function Perf({ player, title, artist, lines, plain, onExit, reducedMotion }: PerfProps) {
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
      if (e.key === "Escape" && !document.querySelector(".feedback-sheet[open]")) onExit();
    };
    window.addEventListener("keydown", esc);
    return () => {
      window.removeEventListener("keydown", esc);
      window.clearTimeout(timer.current);
    };
  }, [poke, onExit]);

  // The line ladder and word highlight share the player playhead.
  const ls = lyricState(lines, player.currentTime);
  const gapBulbs = ls.inGap && ls.next ? Math.min(8, Math.ceil(ls.gap)) : 0;
  const activeRow = useRef<HTMLDivElement | null>(null);
  useLayoutEffect(() => {
    if (activeRow.current) activeRow.current.scrollTop = 0;
  }, [ls.idx]);
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

  const fade: CSSProperties = { opacity: idle ? 0 : 1, transition: reducedMotion ? undefined : "opacity .5s" };
  const m = phone
    ? { neighbor: 20, active: 24, gap: 12, pad: "0 24px", lh: 1.2 }
    : { neighbor: 30, active: 60, gap: 16, pad: "0 64px", lh: 1.15 };

  return (
    <div
      className="m-stage"
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
        <SonereliMark size={phone ? 20 : 22} />
        <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {title}
          {artist ? ` — ${artist}` : ""}
        </span>
        <span style={{ flex: 1 }}></span>
        <span style={{ whiteSpace: "nowrap" }}>{fmtTime(player.currentTime)} / {fmtTime(player.duration)}</span>
        <button className="m-btn sm ghost" type="button" onClick={onExit} aria-label="Exit performance mode">
          {phone ? "✕" : "esc ✕"}
        </button>
      </div>

      <div className="feedback-performance">
        <FeedbackButton />
      </div>

      {/* Quality remains visible when the transport controls fade. */}

      {/* center: prev / current (wipe) / next + gap countdown (perf.jsx:50-61) */}
      {lines.length > 0 ? (
        <div className="performance-lyrics" style={{ gap: m.gap, padding: m.pad }}>
          <div className="performance-neighbor performance-previous"
            style={{ fontSize: m.neighbor, color: "var(--lyric-prev)" }}>
            {ls.prev && <LyricText line={ls.prev} neighbor />}
          </div>
          <div ref={activeRow} className="performance-current"
            style={{ fontSize: m.active, lineHeight: m.lh, textShadow: "var(--glow)" }}>
            {ls.cur ? (
              <LyricText line={ls.cur} currentTime={player.currentTime} reducedMotion={reducedMotion} />
            ) : (
              <span>{ls.next ? "get ready…" : player.currentTime < 2 ? title : "instrumental"}</span>
            )}
          </div>
          <div className="performance-neighbor" style={{ fontSize: m.neighbor, color: "var(--lyric-next)" }}>
            {ls.next ? <LyricText line={ls.next} neighbor /> : "— end —"}
          </div>
          <div style={{ display: "flex", justifyContent: "center", marginTop: 4, minHeight: 8 }}>
            {gapBulbs > 0 && <MBulbs n={8} lit={gapBulbs} size={7} gap={phone ? 9 : 10} />}
          </div>
        </div>
      ) : (
        // Plain-lyrics fallback — centered plain text, NO fake timing.
        <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column", overflowY: "auto", padding: m.pad, textAlign: "center" }}>
          {plain ? (
            <PlainLyrics
              text={plain}
              style={{ margin: "auto", padding: "24px 0", fontFamily: "var(--font-display)", fontSize: m.neighbor, fontWeight: 500, lineHeight: 1.5, color: "var(--fg-soft)", whiteSpace: "pre-wrap" }}
            />
          ) : (
            <div style={{ margin: "auto", padding: "24px 0" }}>
              <div style={{ fontFamily: "var(--font-display)", fontSize: m.active, fontWeight: 700, lineHeight: m.lh, textShadow: "var(--glow)" }}>{title}</div>
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
              <PlaybackIcon playing={player.playing} />
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
            <PlaybackIcon playing={player.playing} />
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
