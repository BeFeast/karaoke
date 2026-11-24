// The Stage dock (Sonereli UI/UX v1 step 6): scrub, transport and Original
// vocals in the bottom ≈230 pt, in thumb reach. Scrub and time always run
// left to right. Dragging the scrub thickens the track and shows a bubble with
// the time and the lyric line at that point; on release it seeks, snapping to
// a line start closer than 0.6 s.

import { type CSSProperties, useRef, useState } from "react";
import { t } from "../i18n";
import { fmtClock } from "../lib/library";
import { useRailDrag } from "../lib/railDrag";
import { snapDetent } from "../lib/vocals";
import type { KaraokePlayerApi } from "../player/useKaraokePlayer";
import { Icon } from "./Icon";
import { activeLineIndex, type LyricLine } from "./SyncedLyrics";

/** A scrub release this close to a line start lands on the line start. */
export const SNAP_S = 0.6;

export function snapToLine(time: number, lines: LyricLine[]): number {
  let best = time;
  let distance = SNAP_S;
  for (const line of lines) {
    const d = Math.abs(line.t - time);
    if (d < distance) {
      distance = d;
      best = line.t;
    }
  }
  return best;
}

export function StageDock({ player, lines, vocalPct, onVocals, onToggleVocals, children }: {
  player: KaraokePlayerApi;
  lines: LyricLine[];
  /** Original vocals level, 0–100. */
  vocalPct: number;
  onVocals: (pct: number) => void;
  /** Mic toggle: 0 ↔ the previous level. */
  onToggleVocals: () => void;
  /** Status line above the controls (buffering, audio error). */
  children?: React.ReactNode;
}) {
  const disabled = !player.ready;
  return (
    <div className="sn-stage-dock">
      {children}
      <Scrub player={player} lines={lines} />
      <div className="sn-transport">
        <button type="button" className="sn-iconbtn sn-skip" onClick={() => player.skip(-5)} disabled={disabled}
          aria-label={t("stage.back5")}>
          <Icon name="back5" size={30} />
        </button>
        <button type="button" className={`sn-play${player.ready || player.error ? "" : " is-buffering"}`}
          onClick={player.playPause} disabled={disabled} data-sn-play=""
          aria-label={player.playing ? t("stage.pause") : t("stage.play")}>
          {player.ready || player.error ? <Icon name={player.playing ? "pause" : "play"} size={34} />
            : <span className="sn-spinner" aria-hidden="true" />}
        </button>
        <button type="button" className="sn-iconbtn sn-skip" onClick={() => player.skip(5)} disabled={disabled}
          aria-label={t("stage.fwd5")}>
          <Icon name="fwd5" size={30} />
        </button>
      </div>
      <Vocals pct={vocalPct} onChange={onVocals} onToggle={onToggleVocals} disabled={!player.hasVocals} />
    </div>
  );
}

function Scrub({ player, lines }: { player: KaraokePlayerApi; lines: LyricLine[] }) {
  const railRef = useRef<HTMLDivElement | null>(null);
  const [drag, setDrag] = useState<number | null>(null);
  const duration = player.duration || 0;
  const time = drag ?? player.currentTime;
  const pct = duration > 0 ? Math.min(100, (time / duration) * 100) : 0;
  const { onPointerDown } = useRailDrag(railRef, {
    onMove: (f) => duration > 0 && setDrag(f * duration),
    onEnd: (f) => {
      if (duration > 0) player.seek(snapToLine(f * duration, lines));
      setDrag(null);
    },
  });
  const line = drag !== null ? lines[activeLineIndex(lines, drag)] : undefined;
  return (
    <div className={`sn-scrub${drag !== null ? " is-dragging" : ""}`} dir="ltr">
      {drag !== null && (
        <div className="sn-scrub-bubble" role="status">
          <span className="sn-cyan">{fmtClock(drag)}</span>
          {line && <bdi className="sn-t1" dir="auto">{line.text}</bdi>}
        </div>
      )}
      <span className="sn-time sn-cyan">{fmtClock(time)}</span>
      <div
        ref={railRef}
        className="sn-rail"
        role="slider"
        tabIndex={player.ready ? 0 : -1}
        aria-label={t("stage.position")}
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(time)}
        aria-valuetext={t("stage.positionValue", { elapsed: fmtClock(time), total: fmtClock(duration) })}
        aria-disabled={!player.ready || undefined}
        data-sn-scrub=""
        onPointerDown={player.ready ? onPointerDown : undefined}
        onKeyDown={(e) => {
          const delta = e.key === "ArrowLeft" || e.key === "ArrowDown" ? -5 : e.key === "ArrowRight" || e.key === "ArrowUp" ? 5 : 0;
          if (!delta || !player.ready) return;
          e.preventDefault();
          e.stopPropagation();
          player.skip(delta);
        }}
        style={{ "--p": `${pct}%` } as CSSProperties}
      >
        <span className="sn-rail-track"><i /></span>
        <span className="sn-rail-thumb" />
      </div>
      <span className="sn-time sn-time-left">−{fmtClock(Math.max(0, duration - time))}</span>
    </div>
  );
}

function Vocals({ pct, onChange, onToggle, disabled }: {
  pct: number;
  onChange: (pct: number) => void;
  onToggle: () => void;
  disabled: boolean;
}) {
  const railRef = useRef<HTMLDivElement | null>(null);
  // Detents at Off / Half / Full; Android gets a light tick (no haptics on iOS web).
  const last = useRef(pct);
  const { onPointerDown } = useRailDrag(railRef, {
    onMove: (f) => {
      const next = snapDetent(f * 100);
      if (next !== last.current && (next === 0 || next === 50 || next === 100)) navigator.vibrate?.(10);
      last.current = next;
      onChange(next);
    },
  });
  const value = disabled ? 0 : pct;
  return (
    <div className={`sn-vocals${disabled ? " is-disabled" : ""}`}>
      <button type="button" className="sn-vocals-mic" onClick={onToggle} disabled={disabled}
        aria-label={t("stage.vocalsMute")} aria-pressed={value === 0} data-sn-mic="">
        <Icon name={value > 0 ? "mic" : "micoff"} size={24} />
      </button>
      <div className="sn-vocals-body">
        <div className="sn-vocals-head">
          <span className="sn-label">{t("stage.vocals")}</span>
          <span className="sn-vocals-pct">{value}%</span>
        </div>
        <div
          ref={railRef}
          className="sn-rail sn-rail-pink"
          role="slider"
          tabIndex={disabled ? -1 : 0}
          aria-label={t("stage.vocalsA11y")}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={value}
          aria-valuetext={t("stage.vocalsValue", { n: value })}
          aria-disabled={disabled || undefined}
          data-sn-vocals=""
          dir="ltr"
          onPointerDown={disabled ? undefined : onPointerDown}
          onKeyDown={(e) => {
            const next: Record<string, number> = {
              ArrowLeft: pct - 5, ArrowDown: pct - 5, ArrowRight: pct + 5, ArrowUp: pct + 5, Home: 0, End: 100,
            };
            if (disabled || !(e.key in next)) return;
            e.preventDefault();
            e.stopPropagation();
            onChange(Math.max(0, Math.min(100, next[e.key])));
          }}
          style={{ "--p": `${value}%` } as CSSProperties}
        >
          <span className="sn-rail-track"><i /></span>
          <span className="sn-rail-thumb" />
        </div>
        <div className="sn-vocals-scale" aria-hidden="true">
          <span>{t("stage.off")}</span><span>{t("stage.half")}</span><span>{t("stage.full")}</span>
        </div>
        {disabled && <p className="sn-vocals-note">{t("stage.noVocalTrack")}</p>}
      </div>
    </div>
  );
}
