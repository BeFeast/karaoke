// The Stage lyrics (Sonereli UI/UX v1 steps 6–7): lyrics fill the screen.
// The active line is pink, bold and larger, anchored at 28% of the lyrics
// height; the next line is white, the lines ahead muted, the sung ones dimmer.
// The current word gets a cyan underline when the LRC has word timing.
// A count-in cue sits above the first line and above every instrumental
// break of 8 s or more. Scrolling by hand turns follow off and shows
// "Back to current line"; tapping a line seeks to its start.
//
// The list keeps the class names the verifier reads (.synced-lyrics-list,
// .synced-lyrics-line, .synced-lyrics-seek, aria-current): every child of the
// list is one lyric line, cues live inside the line they introduce.

import { type CSSProperties, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { t } from "../i18n";
import { fmtClock } from "../lib/library";
import { Icon } from "./Icon";
import { LyricText } from "./LyricText";
import { timeLines } from "./stage-core";
import {
  anchorScrollTop,
  cueCountdown,
  fitFontPx,
  fitRoom,
  PERF_MIN_ACTIVE_PX,
  type LyricCue,
  lineRoles,
  lyricCues,
  type LyricsSize,
} from "./stageLyricsModel";
import { activeLineIndex, detectLyricsDirection, detectTextDirection, type LyricLine, PlainLyrics } from "./SyncedLyrics";

const FOLLOW_MS = 280;
const easeOut = (p: number) => 1 - (1 - p) ** 3;

export function StageLyrics({ lines, duration, plain, currentTime, onSeek, size, reducedMotion, noLyrics, countIn = true }: {
  /** Parsed LRC lines (speech lines included); empty = unsynced. */
  lines: LyricLine[];
  duration: number | null;
  /** Plain text for unsynced songs. */
  plain: string | null;
  currentTime: number;
  onSeek: (time: number) => void;
  size: LyricsSize;
  reducedMotion: boolean;
  /** Rendered when there are no lyrics at all. */
  noLyrics: React.ReactNode;
  /** Settings: the intro count-in cue before the first line. */
  countIn?: boolean;
}) {
  if (lines.length === 0) {
    if (plain?.trim()) {
      return (
        <div className="sn-lyrics sn-lyrics-unsynced" data-size={size}>
          <div className="sn-lyrics-scroll">
            <p className="sn-lyrics-notice">{t("stage.unsynced")}</p>
            <PlainLyrics text={plain} className="lyrics-text sn-plain" />
          </div>
        </div>
      );
    }
    return <div className="sn-lyrics sn-lyrics-none">{noLyrics}</div>;
  }
  return <SyncedStageLyrics lines={lines} duration={duration} currentTime={currentTime} onSeek={onSeek}
    size={size} reducedMotion={reducedMotion} countIn={countIn} />;
}

function SyncedStageLyrics({ lines, duration, currentTime, onSeek, size, reducedMotion, countIn }: {
  lines: LyricLine[];
  duration: number | null;
  currentTime: number;
  onSeek: (time: number) => void;
  size: LyricsSize;
  reducedMotion: boolean;
  countIn: boolean;
}) {
  const timed = useMemo(() => timeLines(lines, duration), [lines, duration]);
  const cues = useMemo(() => lyricCues(lines, timed).filter((cue) => countIn || cue.kind !== "intro"),
    [lines, timed, countIn]);
  const cueBefore = useMemo(() => new Map(cues.map((cue) => [cue.before, cue])), [cues]);
  const direction = useMemo(() => detectLyricsDirection(lines), [lines]);
  const active = activeLineIndex(lines, currentTime);
  const roles = lineRoles(lines, active);

  const listRef = useRef<HTMLOListElement | null>(null);
  const rowRefs = useRef<Array<HTMLLIElement | null>>([]);
  const [follow, setFollow] = useState(true);
  const followRef = useRef(true);
  followRef.current = follow;
  const animation = useRef<number | null>(null);
  // Follow target: the active line, or the first line (with its intro cue)
  // before singing starts.
  const target = Math.max(0, active);

  const stopAnimation = () => {
    if (animation.current !== null) cancelAnimationFrame(animation.current);
    animation.current = null;
  };

  const anchorTop = useCallback((index: number) => {
    const list = listRef.current;
    const row = rowRefs.current[index];
    if (!list || !row) return null;
    const button = row.querySelector<HTMLElement>(".synced-lyrics-seek") ?? row;
    // The intro cue belongs on screen with the first line.
    const top = index === 0 || active < 0 ? row.offsetTop : button.offsetTop;
    const height = button.offsetTop + button.offsetHeight - top;
    return anchorScrollTop(top, list.clientHeight, list.scrollHeight - list.clientHeight, height);
  }, [active]);

  const scrollToTarget = useCallback((animate: boolean) => {
    const list = listRef.current;
    const row = rowRefs.current[target];
    if (!list || !row) return;
    stopAnimation();
    const goal = anchorTop(target);
    if (goal === null) return;
    // Animate only short moves of a line that is already on screen; a seek
    // far away jumps (it must be readable at once).
    const visible = row.offsetTop >= list.scrollTop && row.offsetTop + row.offsetHeight <= list.scrollTop + list.clientHeight;
    if (reducedMotion) {
      list.scrollTop = goal;
      return;
    }
    const glide = animate && visible;
    const from = list.scrollTop;
    const started = performance.now();
    // Re-read the goal every frame for the whole line transition: line sizes
    // animate (200 ms), so a goal computed once drifts. A jump re-pins too.
    const step = (now: number) => {
      const p = Math.min(1, (now - started) / FOLLOW_MS);
      const to = anchorTop(target) ?? goal;
      list.scrollTop = glide ? from + (to - from) * easeOut(p) : to;
      animation.current = p < 1 ? requestAnimationFrame(step) : null;
    };
    if (!glide) list.scrollTop = goal;
    animation.current = requestAnimationFrame(step);
  }, [anchorTop, reducedMotion, target]);

  // Fit: a very long active line shrinks (never below 20 px) so it stays
  // whole between the anchor and the dock. The size is reset when the line
  // stops being active.
  const fitted = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (fitted.current) {
      fitted.current.style.fontSize = "";
      fitted.current.style.lineHeight = "";
      fitted.current = null;
    }
    const list = listRef.current;
    const button = active >= 0 ? rowRefs.current[active]?.querySelector<HTMLElement>(".synced-lyrics-seek") : null;
    if (!list || !button || !button.closest(".is-active")) return;
    // Measure at the final (not mid-transition) size.
    const style = getComputedStyle(list.parentElement ?? list);
    const finalPx = parseFloat(style.getPropertyValue("--ly-a")) || parseFloat(getComputedStyle(button).fontSize);
    const finalLh = parseFloat(style.getPropertyValue("--ly-a-lh")) || finalPx * 1.2;
    button.style.transition = "none";
    button.style.fontSize = `${finalPx}px`;
    button.style.lineHeight = `${finalLh}px`;
    const fit = fitFontPx(finalPx, button.offsetHeight, fitRoom(list.clientHeight),
      size === "perf" ? PERF_MIN_ACTIVE_PX : undefined);
    button.style.fontSize = "";
    button.style.lineHeight = "";
    button.style.transition = "";
    if (fit < finalPx) {
      button.style.fontSize = `${fit}px`;
      button.style.lineHeight = `${Math.round(fit * (finalLh / finalPx))}px`;
      fitted.current = button;
    }
  }, [active, size, lines]);

  useLayoutEffect(() => {
    if (followRef.current) scrollToTarget(true);
  }, [target, size, scrollToTarget]);

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const observer = new ResizeObserver(() => {
      if (followRef.current && animation.current === null) scrollToTarget(false);
    });
    observer.observe(list);
    return () => {
      observer.disconnect();
      stopAnimation();
    };
  }, [scrollToTarget]);

  // Manual scroll (wheel or a real finger drag) turns follow off.
  const touchY = useRef<number | null>(null);
  const leaveFollow = () => {
    if (!followRef.current) return;
    stopAnimation();
    setFollow(false);
  };
  const backToLine = () => {
    setFollow(true);
    followRef.current = true;
    scrollToTarget(true);
  };
  const seekLine = (time: number) => {
    setFollow(true);
    followRef.current = true;
    onSeek(time);
  };

  const style = { "--sn-dir": direction } as CSSProperties;
  return (
    <div className="sn-lyrics" data-size={size} data-follow={follow ? "on" : "off"} style={style}>
      <ol
        ref={listRef}
        className="synced-lyrics-list sn-lyrics-scroll"
        aria-label={t("stage.lyrics")}
        dir={direction}
        onWheel={leaveFollow}
        onTouchStart={(e) => {
          touchY.current = e.touches[0]?.clientY ?? null;
        }}
        onTouchMove={(e) => {
          const y = e.touches[0]?.clientY;
          if (touchY.current !== null && y !== undefined && Math.abs(y - touchY.current) > 8) leaveFollow();
        }}
        onTouchEnd={() => {
          touchY.current = null;
        }}
      >
        {lines.map((line, i) => {
          const role = roles[i];
          const cue = cueBefore.get(i);
          return (
            <li
              key={`${line.t}-${i}`}
              ref={(el) => {
                rowRefs.current[i] = el;
              }}
              className={`synced-lyrics-line is-${role}${role === "active" ? " is-active" : role === "past" ? " is-sung" : role === "speech" ? " is-speech" : ""}`}
              aria-current={role === "active" ? "true" : undefined}
            >
              {cue && <Cue cue={cue} now={currentTime} />}
              <button
                type="button"
                className="synced-lyrics-seek"
                onClick={() => seekLine(line.t)}
                title={t("stage.seekLine", { time: fmtClock(line.t) })}
                dir={detectTextDirection(line.text)}
              >
                {line.speech ? <bdi className="synced-lyrics-speech-tag">{t("stage.speech")} · </bdi> : null}
                <LyricText line={timed[i]}
                  currentTime={role === "active" && line.words ? currentTime : undefined} />
              </button>
            </li>
          );
        })}
      </ol>
      {!follow && active >= 0 && (
        <button type="button" className="sn-follow-chip" onClick={backToLine}>
          <Icon name="down" size={18} />
          {t("stage.follow")}
        </button>
      )}
    </div>
  );
}

function Cue({ cue, now }: { cue: LyricCue; now: number }) {
  const { active, remaining, lit } = cueCountdown(cue, now);
  const label = cue.kind === "intro"
    ? active ? t("stage.introIn", { n: Math.max(1, Math.ceil(remaining)) }) : t("stage.intro")
    : t("stage.instrumental", { time: fmtClock(cue.end - cue.start) });
  return (
    <div className={`sn-cue${active ? " is-now" : ""}`} aria-hidden="true">
      <span className="sn-cue-dots">
        {[0, 1, 2].map((dot) => <i key={dot} className={dot < lit ? "is-lit" : undefined} />)}
      </span>
      {cue.kind === "instrumental" && <Icon name="note" size={20} />}
      <bdi>{label}</bdi>
    </div>
  );
}
