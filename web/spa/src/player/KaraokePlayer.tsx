// KARAOKE — the Stage player block (Marquee port, #154): waveform card +
// console/setlist modules around the UNTOUCHED engine API (useKaraokePlayer).
// Literal port of design/claude-export/proto/stage.jsx TransportBar (:3-23),
// ConsoleModule (:26-46), SetlistModule (:49-89); fader visuals come from
// stage-core.tsx (core.jsx ProtoFader). The waveform card restyles the
// wavesurfer container per the ProtoWave contract (core.jsx:103-130): colors
// strictly from tokens (--inst / --vox / --accent), click-to-seek via the
// existing ws→engine edge — no second waveform, no media options.
//
// Recorded deviation: the design's mix model is an independent {vox, inst}
// pair; production is the single ear-verified equal-power crossfade. The two
// faders are a visual skin over that ONE parameter — VOX = vocalLevel, INST
// displays/drives 1 − vocalLevel (mirrored). DROP / hold-V ducking is pure UI
// over setVocalLevel (save level → 0 → restore). The hook API is not extended.
// The ⤢ Performance control ships with the Performance-mode issue.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { lyricState, ProtoFader, type TimedLine } from "../components/stage-core";
import { MBulbs, MWipe } from "../components/marks";
import type { StageTheme } from "../theme";
import { type KaraokePlayerApi, PLAYBACK_RATES, useKaraokePlayer } from "./useKaraokePlayer";

/** mm:ss for a (possibly NaN/Infinity) seconds value. */
function fmt(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Resolve the design tokens to concrete colors for the canvas. wavesurfer
// paints to a <canvas>, which can't read CSS custom properties, so we read
// the computed values — off the player's ROOM container (the .m-booth /
// .m-stage token scope), never bare :root, so the ◐ day/night flip
// live-updates the canvas (#154). ProtoWave contract: unplayed wave = the
// instrumental stem color, played progress = the vocal stem color, playhead
// cursor = the room accent.
function readWaveColors(el: Element): { wave: string; progress: string; cursor: string } {
  const cs = getComputedStyle(el);
  const get = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
  return {
    wave: get("--inst", "#7fa3c4"),
    progress: get("--vox", "#9fd07a"),
    cursor: get("--accent", "#5f7a4a"),
  };
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

// ── TransportBar (stage.jsx:3-23) ───────────────────────────────────────────
// Play/skip/time + the ⟲ A–B cycle; the rate select binds the existing
// PLAYBACK_RATES (unchanged). The design's loop cycle (none → A set → A–B →
// none) maps onto markA/markB/clearRegion: the engine wraps at B whenever a
// region is set, and a B within 1 s of A cancels (the design's guard).
function TransportBar({ player }: { player: KaraokePlayerApi }) {
  const { a, b } = player.region;
  const cycleLoop = () => {
    if (a == null) player.markA();
    else if (b == null) {
      if (player.currentTime > a + 1) player.markB();
      else player.clearRegion();
    } else player.clearRegion();
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <button className="m-btn primary" type="button" onClick={player.playPause} disabled={!player.ready}
        aria-label={player.playing ? "Pause" : "Play"} title="Play / pause (Space)"
        style={{ width: 40, height: 40, borderRadius: "50%", justifyContent: "center", fontSize: 14 }}>
        {player.playing ? "❚❚" : "▶"}
      </button>
      <button className="m-btn sm" type="button" onClick={() => player.skip(-5)} disabled={!player.ready} title="Back 5 seconds (←)">−5s</button>
      <button className="m-btn sm" type="button" onClick={() => player.skip(5)} disabled={!player.ready} title="Forward 5 seconds (→)">+5s</button>
      <span className="m-mono" style={{ fontSize: 12, color: "var(--fg-soft)", minWidth: 76 }}>{fmt(player.currentTime)} / {fmt(player.duration)}</span>
      <span style={{ flex: 1 }}></span>
      <button className="m-btn sm" type="button" onClick={cycleLoop} disabled={!player.ready}
        title="A–B repeat: press to mark A, again to mark B, again to clear"
        style={a != null ? { borderColor: "var(--accent)", color: "var(--accent)" } : undefined}>
        ⟲ {a == null ? "A–B" : b == null ? "A set… (B?)" : `${fmt(a)}–${fmt(b)}`}
      </button>
      <select value={player.rate} disabled={!player.ready} aria-label="Playback speed"
        onChange={(e) => player.setRate(Number(e.target.value))}
        style={{ appearance: "none", padding: "4px 9px", border: "1px solid var(--border)", borderRadius: "var(--radius)", background: "var(--bg-card)", color: "var(--fg)", fontSize: 12, fontFamily: "var(--font-mono)", cursor: "pointer" }}>
        {PLAYBACK_RATES.map((r) => (
          <option key={r} value={r}>{r}×</option>
        ))}
      </select>
    </div>
  );
}

// ── ConsoleModule (stage.jsx:26-46): faders + DROP ──────────────────────────
function ConsoleModule({
  voxPct,
  instPct,
  onVox,
  onInst,
  ducked,
  duckStart,
  duckEnd,
}: {
  voxPct: number;
  instPct: number;
  onVox: (pct: number) => void;
  onInst: (pct: number) => void;
  ducked: boolean;
  duckStart: () => void;
  duckEnd: () => void;
}) {
  // Hold "V" to duck, design behavior (window-level so it works mid-song
  // without hunting for focus) — but never while typing in a field.
  useEffect(() => {
    const isField = (t: EventTarget | null) =>
      t instanceof HTMLElement &&
      (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
    const dn = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "v" && !e.repeat && !isField(e.target)) duckStart();
    };
    const up = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "v") duckEnd();
    };
    window.addEventListener("keydown", dn);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", dn);
      window.removeEventListener("keyup", up);
    };
  }, [duckStart, duckEnd]);
  return (
    <div style={{ display: "flex", gap: 14, padding: "12px 16px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", alignItems: "center" }}>
      <ProtoFader label="VOX" color="var(--vox)" value={voxPct} ducked={ducked} onChange={onVox} />
      <ProtoFader label="INST" color="var(--inst)" value={instPct} onChange={onInst} />
      <div style={{ display: "grid", gap: 8 }}>
        <button className="m-btn sm" type="button"
          onPointerDown={duckStart} onPointerUp={duckEnd} onPointerLeave={duckEnd}
          style={{ borderColor: "var(--vox)", color: ducked ? "var(--accent-fg)" : "var(--vox)", background: ducked ? "var(--vox)" : "transparent", fontWeight: 700, justifyContent: "center" }}>DROP</button>
        <span className="m-mono" style={{ fontSize: 9, color: "var(--muted)", textAlign: "center", lineHeight: 1.4 }}>hold to drop vocals<br></br>while you sing · or "V"</span>
      </div>
    </div>
  );
}

// ── SetlistModule (stage.jsx:49-89): marquee sign + spotlight dimmer ────────
// The vertical rail drives the SAME single crossfade parameter as the faders.
function SetlistModule({ player, lines }: { player: KaraokePlayerApi; lines: TimedLine[] }) {
  const ls = lyricState(lines, player.currentTime);
  const gapBulbs = ls.inGap && ls.next ? Math.min(8, Math.ceil(ls.gap)) : 0;
  const vox = Math.round(player.vocalLevel * 100);
  const dimmerDrag = (e: React.PointerEvent) => {
    const rail = e.currentTarget;
    const move = (ev: { clientY: number }) => {
      const r = rail.getBoundingClientRect();
      const pct = 100 - ((ev.clientY - r.top) / r.height) * 100;
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
  return (
    <div style={{ display: "flex", gap: 16, alignItems: "stretch" }}>
      <div className="m-sign" style={{ flex: 1, padding: "16px 22px", display: "flex", flexDirection: "column", justifyContent: "center", gap: 9, textAlign: "center" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 14, fontWeight: 500, color: "var(--lyric-prev)", minHeight: 18 }}>{ls.prev ? ls.prev.text : "—"}</div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 700, lineHeight: 1.25, minHeight: 28 }}>
          {ls.cur
            ? <MWipe text={ls.cur.text} pct={ls.sung} size={22} family="var(--font-display)" weight={700} fill="var(--accent)" dim="var(--lyric-dim)" />
            : <span style={{ color: "var(--lyric-dim)" }}>{ls.next ? "get ready…" : "intro"}</span>}
        </div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 540, color: "var(--lyric-next)", minHeight: 19 }}>{ls.next ? ls.next.text : "— end —"}</div>
        <div style={{ display: "flex", justifyContent: "center", marginTop: 2, minHeight: 8 }}>
          {gapBulbs > 0 ? <MBulbs n={8} lit={gapBulbs} /> : <span></span>}
        </div>
      </div>
      <div style={{ display: "grid", justifyItems: "center", gridTemplateRows: "auto 1fr auto", padding: "4px 0", gap: 7 }}>
        <span className="m-mono" style={{ fontSize: 9, color: "var(--vox)" }}>VOX</span>
        <div onPointerDown={dimmerDrag} style={{ width: 16, display: "flex", justifyContent: "center", cursor: "ns-resize", touchAction: "none" }}>
          <div style={{ width: 4, borderRadius: 2, background: "linear-gradient(180deg, var(--vox), var(--inst))", position: "relative" }}>
            <span style={{ position: "absolute", top: (100 - vox) + "%", left: "50%", transform: "translate(-50%,-50%)", width: 18, height: 18, borderRadius: "50%", background: "var(--fg)", border: "3px solid var(--bg)", transition: "top .1s" }}></span>
          </div>
        </div>
        <span className="m-mono" style={{ fontSize: 9, color: "var(--muted)" }}>{vox}%</span>
      </div>
    </div>
  );
}

// ── Inline current-lyric wipe (console view, stage.jsx:136-138) ─────────────
// Rides the engine's raw tick feed (subscribeTime) and writes the DOM
// directly, bypassing the React render path entirely — the same .m-wipe
// markup MWipe renders, updated imperatively.
function LiveLyricWipe({
  lines,
  subscribeTime,
}: {
  lines: TimedLine[];
  subscribeTime: (cb: (t: number) => void) => () => void;
}) {
  const dimRef = useRef<HTMLSpanElement | null>(null);
  const fillRef = useRef<HTMLSpanElement | null>(null);
  useEffect(
    () =>
      subscribeTime((t) => {
        const dim = dimRef.current;
        const fill = fillRef.current;
        if (!dim || !fill) return;
        const ls = lyricState(lines, t);
        const text = ls.cur ? ls.cur.text : "";
        if (dim.textContent !== text) {
          dim.textContent = text;
          fill.textContent = text;
        }
        fill.style.width = `${ls.cur ? ls.sung : 0}%`;
      }),
    [lines, subscribeTime],
  );
  return (
    <span className="m-wipe" style={{ fontFamily: "var(--font-display)", fontSize: 17, fontWeight: 650 }}>
      <span className="w-dim" style={{ color: "var(--lyric-dim)" }} ref={dimRef}></span>
      <span className="w-fill" style={{ color: "var(--accent)" }} ref={fillRef} aria-hidden="true"></span>
    </span>
  );
}

export interface KaraokePlayerProps {
  /** Same-origin instrumental URL (master + waveform). */
  instrumentalUrl: string;
  /** Same-origin vocals URL, or null when only the instrumental exists. */
  vocalsUrl: string | null;
  /**
   * Surfaces the live playhead for sibling features (e.g. the #59 synced-lyrics
   * panel) without re-rendering this component's parent on every frame.
   */
  onTime?: (currentTime: number, duration: number) => void;
  /** Lets a sibling (e.g. lyrics line click) drive the transport. */
  seekRef?: React.MutableRefObject<((time: number) => void) | null>;
  /** Stage view: console (FINAL default) or setlist. */
  view: "console" | "setlist";
  /** Timed lyric lines for the wipe / setlist modules (empty = no lyrics). */
  lines: TimedLine[];
  /** Room theme — the ◐ flip re-reads canvas colors off the room container. */
  theme: StageTheme;
}

export function KaraokePlayer({ instrumentalUrl, vocalsUrl, onTime, seekRef, view, lines, theme }: KaraokePlayerProps) {
  const rootRef = useRef<HTMLElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [container, setContainer] = useState<HTMLElement | null>(null);
  // First paint reads :root (same booth-light values); the mount/theme effect
  // below immediately re-reads off the room container.
  const [colors, setColors] = useState(() => readWaveColors(document.documentElement));
  const reducedMotion = useMemo(prefersReducedMotion, []);

  // Re-read canvas colors off the room container whenever ◐ flips the room
  // class — runs after the DOM commit, so the computed tokens are the new set.
  useEffect(() => {
    if (rootRef.current) setColors(readWaveColors(rootRef.current));
  }, [theme]);

  // Mount the wavesurfer container once the ref is attached.
  useEffect(() => {
    setContainer(containerRef.current);
  }, []);

  const player = useKaraokePlayer({
    instrumentalUrl,
    vocalsUrl,
    container,
    colors,
    reducedMotion,
  });

  // Expose the transport to siblings (#59). The playhead rides the engine's
  // raw tick feed (subscribeTime) instead of React state, so per-tick updates
  // bypass this component's render entirely; refs keep the subscription stable
  // while still calling the latest onTime with the latest duration.
  const onTimeRef = useRef(onTime);
  onTimeRef.current = onTime;
  const durationRef = useRef(player.duration);
  durationRef.current = player.duration;
  useEffect(
    () => player.subscribeTime((t) => onTimeRef.current?.(t, durationRef.current)),
    [player.subscribeTime],
  );
  useEffect(() => {
    if (seekRef) seekRef.current = player.seek;
    return () => {
      if (seekRef) seekRef.current = null;
    };
  }, [seekRef, player.seek]);

  // ── DROP / hold-V ducking — pure UI over setVocalLevel ───────────────────
  // Press: remember the level, crossfade to instrumental-only; release:
  // restore. While ducked, fader drags retune the remembered level instead
  // (applied on release), so the faders keep showing the singer's mix.
  const { setVocalLevel } = player;
  const [ducked, setDucked] = useState(false);
  const [prior, setPrior] = useState(0.5);
  const duckedRef = useRef(false);
  const priorRef = useRef(0.5);
  const levelRef = useRef(player.vocalLevel);
  levelRef.current = player.vocalLevel;

  const duckStart = useCallback(() => {
    if (duckedRef.current) return;
    duckedRef.current = true;
    priorRef.current = levelRef.current;
    setPrior(levelRef.current);
    setVocalLevel(0);
    setDucked(true);
  }, [setVocalLevel]);
  const duckEnd = useCallback(() => {
    if (!duckedRef.current) return;
    duckedRef.current = false;
    setVocalLevel(priorRef.current);
    setDucked(false);
  }, [setVocalLevel]);

  // Both faders skin the one crossfade parameter (recorded deviation).
  const onVox = useCallback(
    (pct: number) => {
      const level = pct / 100;
      if (duckedRef.current) {
        priorRef.current = level;
        setPrior(level);
      } else {
        setVocalLevel(level);
      }
    },
    [setVocalLevel],
  );
  const onInst = useCallback((pct: number) => onVox(100 - pct), [onVox]);
  const voxPct = Math.round((ducked ? prior : player.vocalLevel) * 100);
  const instPct = 100 - voxPct;

  // Keyboard transport. Scoped to the player root so it doesn't hijack typing
  // elsewhere; Space/←/→ are the karaoke staples.
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      // Let native controls (slider, buttons) handle their own arrows/space.
      if (e.target instanceof HTMLInputElement) return;
      switch (e.key) {
        case " ":
        case "Spacebar":
          if (tag !== "BUTTON") {
            e.preventDefault();
            player.playPause();
          }
          break;
        case "ArrowLeft":
          e.preventDefault();
          player.skip(-5);
          break;
        case "ArrowRight":
          e.preventDefault();
          player.skip(5);
          break;
      }
    },
    [player],
  );

  return (
    // The player root is a keyboard transport surface (Space / ← / →); it is
    // focusable on purpose so the shortcuts work without hunting for a control.
    <section
      ref={rootRef}
      className="ksplayer"
      aria-label="Karaoke player"
      tabIndex={0}
      onKeyDown={onKeyDown}
    >
      {/* waveform card (stage.jsx:126-129; ProtoWave contract → wavesurfer) */}
      <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "14px 16px" }}>
        <div className="ksplayer-wave-wrap">
          {!player.ready && !player.error && <div className="ksplayer-loading" aria-hidden />}
          <div ref={containerRef} className="ksplayer-wave" aria-label="Waveform — click to seek" />
          {player.error && <div className="ksplayer-error">{player.error}</div>}
        </div>
      </div>

      {view === "console" ? (
        <div style={{ display: "flex", gap: 16, alignItems: "stretch", flexWrap: "wrap" }}>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 11, justifyContent: "center", minWidth: 0 }}>
            <TransportBar player={player} />
            <div className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>space play · ←/→ seek · V drops vocals · click wave to seek</div>
            <div style={{ fontFamily: "var(--font-display)", fontSize: 17, fontWeight: 650, minHeight: 22 }}>
              {lines.length > 0 && <LiveLyricWipe lines={lines} subscribeTime={player.subscribeTime} />}
            </div>
          </div>
          {player.hasVocals && (
            <ConsoleModule
              voxPct={voxPct}
              instPct={instPct}
              onVox={onVox}
              onInst={onInst}
              ducked={ducked}
              duckStart={duckStart}
              duckEnd={duckEnd}
            />
          )}
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <SetlistModule player={player} lines={lines} />
          <TransportBar player={player} />
        </div>
      )}
    </section>
  );
}

export default KaraokePlayer;
