import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "../theme";
import { PLAYBACK_RATES, useKaraokePlayer } from "./useKaraokePlayer";

/** mm:ss for a (possibly NaN/Infinity) seconds value. */
function fmt(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Resolve the design-system tokens to concrete colors for the canvas. wavesurfer
// paints to a <canvas>, which can't read CSS custom properties, so we read the
// computed values off :root and re-read whenever the theme flips.
function readWaveColors(): { wave: string; progress: string; cursor: string } {
  const cs = getComputedStyle(document.documentElement);
  const get = (name: string, fallback: string) => cs.getPropertyValue(name).trim() || fallback;
  const border = get("--border-soft", "#c8ccd3");
  const accent = get("--accent", "#657153");
  const fgSoft = get("--fg-soft", "#3a4234");
  return { wave: border, progress: accent, cursor: fgSoft };
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
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
}

export function KaraokePlayer({ instrumentalUrl, vocalsUrl, onTime, seekRef }: KaraokePlayerProps) {
  const [theme] = useTheme();
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const [colors, setColors] = useState(readWaveColors);
  const reducedMotion = useMemo(prefersReducedMotion, []);

  // Re-read canvas colors after the theme attribute settles.
  useEffect(() => {
    setColors(readWaveColors());
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

  // Expose the transport to siblings (#59).
  useEffect(() => {
    onTime?.(player.currentTime, player.duration);
  }, [player.currentTime, player.duration, onTime]);
  useEffect(() => {
    if (seekRef) seekRef.current = player.seek;
    return () => {
      if (seekRef) seekRef.current = null;
    };
  }, [seekRef, player.seek]);

  // Keyboard transport. Scoped to the player root so it doesn't hijack typing
  // elsewhere; Space/←/→ are the karaoke staples.
  const rootRef = useRef<HTMLElement | null>(null);
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      // Let native controls (slider, buttons handle their own arrows/space).
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

  const atInstrumental = player.vocalLevel <= 0.001;
  const atVocals = player.vocalLevel >= 0.999;
  const { a, b } = player.region;

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
      <div className="ksplayer-wave-wrap">
        {!player.ready && <div className="ksplayer-loading" aria-hidden />}
        <div ref={containerRef} className="ksplayer-wave" />
        {player.error && <div className="error ksplayer-error">{player.error}</div>}
      </div>

      <div className="ksplayer-transport">
        <button
          type="button"
          className="btn primary ksplayer-play"
          onClick={player.playPause}
          disabled={!player.ready}
          aria-label={player.playing ? "Pause" : "Play"}
          title="Play / pause (Space)"
        >
          {player.playing ? "⏸" : "▶"}
        </button>
        <button
          type="button"
          className="btn sm"
          onClick={() => player.skip(-5)}
          disabled={!player.ready}
          title="Back 5 seconds (←)"
          aria-label="Back 5 seconds"
        >
          ⏪ 5s
        </button>
        <button
          type="button"
          className="btn sm"
          onClick={() => player.skip(5)}
          disabled={!player.ready}
          title="Forward 5 seconds (→)"
          aria-label="Forward 5 seconds"
        >
          5s ⏩
        </button>

        <span className="ksplayer-time mono" aria-live="off">
          {fmt(player.currentTime)} / {fmt(player.duration)}
        </span>

        <span className="ksplayer-spacer" />

        <label className="ksplayer-rate">
          <span className="ksplayer-ctl-label">speed</span>
          <select
            className="ksplayer-select"
            value={player.rate}
            onChange={(e) => player.setRate(Number(e.target.value))}
            disabled={!player.ready}
            aria-label="Playback speed"
          >
            {PLAYBACK_RATES.map((r) => (
              <option key={r} value={r}>
                {r}×
              </option>
            ))}
          </select>
        </label>
      </div>

      {player.hasVocals && (
        <div className="ksplayer-mix">
          <div className="ksplayer-ab" role="group" aria-label="Stem selection">
            <span className="ksplayer-ctl-label">stem</span>
            <button
              type="button"
              className={`btn sm${atInstrumental ? " primary" : ""}`}
              aria-pressed={atInstrumental}
              onClick={() => player.setMix("instrumental")}
            >
              Instrumental
            </button>
            <button
              type="button"
              className={`btn sm${atVocals ? " primary" : ""}`}
              aria-pressed={atVocals}
              onClick={() => player.setMix("vocals")}
            >
              Vocals
            </button>
          </div>

          <label className="ksplayer-blend">
            <span className="ksplayer-ctl-label">
              vocal blend <span className="mono">{Math.round(player.vocalLevel * 100)}%</span>
            </span>
            <input
              type="range"
              min={0}
              max={100}
              step={1}
              value={Math.round(player.vocalLevel * 100)}
              onChange={(e) => player.setVocalLevel(Number(e.target.value) / 100)}
              aria-label="Vocal blend (0% instrumental only to 100% full vocals)"
            />
          </label>
        </div>
      )}

      <div className="ksplayer-loop" role="group" aria-label="Loop and A–B repeat">
        <button
          type="button"
          className={`btn sm${player.loop ? " primary" : ""}`}
          aria-pressed={player.loop}
          onClick={player.toggleLoop}
          disabled={!player.ready}
          title="Loop the track (or the A–B section)"
        >
          ⟲ Loop
        </button>
        <span className="ksplayer-ctl-label">A–B repeat</span>
        <button
          type="button"
          className={`btn sm${a != null ? " primary" : ""}`}
          onClick={player.markA}
          disabled={!player.ready}
          title="Set / clear the A point at the playhead"
        >
          A {a != null ? `· ${fmt(a)}` : ""}
        </button>
        <button
          type="button"
          className={`btn sm${b != null ? " primary" : ""}`}
          onClick={player.markB}
          disabled={!player.ready}
          title="Set / clear the B point at the playhead"
        >
          B {b != null ? `· ${fmt(b)}` : ""}
        </button>
        {(a != null || b != null) && (
          <button type="button" className="link-btn" onClick={player.clearRegion}>
            clear
          </button>
        )}
      </div>
    </section>
  );
}

export default KaraokePlayer;
