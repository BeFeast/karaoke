// Dual-stem karaoke playback hook.
//
// The audio engine is DualStemEngine (stemEngine.ts): both stems decoded into
// AudioBuffers and played by co-started AudioBufferSourceNodes on ONE
// AudioContext clock, mixed by per-stem GainNodes — sample-locked by
// construction, no follower element, no drift correction (#113).
//
// Wavesurfer is a visual-only waveform + seek surface here: created from
// pre-computed peaks + duration (a supported 7.x path), with NO url and NO
// media — it never plays anything. Exactly two edges connect it to the engine:
//
//   * ws → engine: the `interaction` event (absolute seconds) drives seek().
//     Wavesurfer also pokes its own src-less internal element on click; that
//     is inert, and the engine's setTime on the next tick is authoritative.
//   * engine → ws: setTime(pos) from the engine tick renders the cursor.
//
// The engine tick also feeds `subscribeTime` (raw position, for the lyrics
// highlight seam) and React state (quantized to 0.1 s so the transport row
// re-renders at ~10 Hz, not at the rAF rate).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";
import { computePeaks, gainsForLevel } from "./engineMath";
import { type ABRegion, DualStemEngine } from "./stemEngine";

/** Available playback speeds for the rate control. */
export const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5] as const;

export interface KaraokePlayerOptions {
  /** Same-origin URL of the instrumental stem (audio + waveform). */
  instrumentalUrl: string;
  /** Same-origin URL of the vocals stem, if present. */
  vocalsUrl: string | null;
  /** Waveform container element. */
  container: HTMLElement | null;
  /** Wave / progress / cursor colors (resolved from CSS tokens by the caller). */
  colors: { wave: string; progress: string; cursor: string };
  /** Skip the moving-cursor animation work for reduced-motion users. */
  reducedMotion: boolean;
}

export type { ABRegion };

export interface KaraokePlayerState {
  ready: boolean;
  playing: boolean;
  duration: number;
  /** Playhead quantized to ~0.1 s — display-grade; subscribeTime is the raw feed. */
  currentTime: number;
  rate: number;
  /** 0 = instrumental only … 1 = vocals only (equal-power crossfade). */
  vocalLevel: number;
  loop: boolean;
  region: ABRegion;
  hasVocals: boolean;
  error: string | null;
}

export interface KaraokePlayerApi extends KaraokePlayerState {
  playPause: () => void;
  /** Absolute seek, clamped to [0, duration]. Drives #59's click-to-seek too. */
  seek: (time: number) => void;
  /** Relative skip in seconds (e.g. -5 / +5). */
  skip: (delta: number) => void;
  setRate: (rate: number) => void;
  /** 0 = instrumental only, 1 = vocals only. Drives both gains. */
  setVocalLevel: (level: number) => void;
  /** A/B toggle preset: instrumental-only vs vocals-only (true solos). */
  setMix: (mix: "instrumental" | "vocals") => void;
  toggleLoop: () => void;
  /** Set the A or B repeat marker at the current playhead (toggles off if set). */
  markA: () => void;
  markB: () => void;
  clearRegion: () => void;
  /**
   * Raw engine-clock position feed (every tick, no quantization), outside the
   * React render path. Returns an unsubscribe.
   */
  subscribeTime: (cb: (t: number) => void) => () => void;
}

export function useKaraokePlayer(opts: KaraokePlayerOptions): KaraokePlayerApi {
  const { instrumentalUrl, vocalsUrl, container, colors, reducedMotion } = opts;

  const [state, setState] = useState<KaraokePlayerState>({
    ready: false,
    playing: false,
    duration: 0,
    currentTime: 0,
    rate: 1,
    vocalLevel: 0.5,
    loop: false,
    region: { a: null, b: null },
    hasVocals: vocalsUrl != null,
    error: null,
  });

  const wsRef = useRef<WaveSurfer | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const engineRef = useRef<DualStemEngine | null>(null);
  // Mutable mirrors so the async setup reads fresh values without re-running.
  const levelRef = useRef(0.5);
  const loopRef = useRef(false);
  const regionRef = useRef<ABRegion>({ a: null, b: null });
  const colorsRef = useRef(colors);
  const timeSubsRef = useRef(new Set<(t: number) => void>());

  // ── Build the engine + the visual waveform once per URL set ──────────────
  useEffect(() => {
    if (!container) return;
    let disposed = false;
    const abort = new AbortController();
    let engine: DualStemEngine | null = null;
    let ws: WaveSurfer | null = null;
    const unsubs: Array<() => void> = [];

    let ctx: AudioContext | null = null;
    try {
      const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      ctx = new Ctor();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.warn("karaoke: Web Audio unavailable", err);
      setState((s) => ({ ...s, error: "This browser doesn’t support Web Audio playback." }));
      return;
    }
    ctxRef.current = ctx;

    DualStemEngine.load({ instrumentalUrl, vocalsUrl, ctx, signal: abort.signal })
      .then((loaded) => {
        if (disposed) {
          loaded.dispose();
          return;
        }
        engine = loaded;
        engineRef.current = loaded;

        // Hand the engine the current control state (it may have changed
        // between mount and decode completing).
        const g = gainsForLevel(levelRef.current);
        loaded.setGains(g.inst, g.voc);
        loaded.setLoop(loopRef.current);
        loaded.setRegion(regionRef.current);

        // Visual-only wavesurfer: peaks + duration, no url, no media.
        const c = colorsRef.current;
        ws = WaveSurfer.create({
          container,
          peaks: [computePeaks(loaded.instrumentalBuffer.getChannelData(0), 4096)],
          duration: loaded.duration,
          height: 96,
          waveColor: c.wave,
          progressColor: c.progress,
          cursorColor: c.cursor,
          cursorWidth: 2,
          barWidth: 2,
          barGap: 1,
          barRadius: 2,
          normalize: true,
          interact: true,
          hideScrollbar: true,
        });
        wsRef.current = ws;

        // ws → engine: waveform click/drag seeks (absolute seconds).
        unsubs.push(ws.on("interaction", (newTime: number) => loaded.seek(newTime)));

        // engine → ws + React: cursor, raw time feed, quantized state.
        unsubs.push(
          loaded.onTick((pos, force) => {
            if (disposed) return;
            ws?.setTime(pos);
            for (const cb of timeSubsRef.current) cb(pos);
            // ~10 Hz for the mm:ss display; transport events (pause/seek)
            // always land exactly so the display never shows a stale 0.1 s.
            setState((s) => (!force && Math.abs(s.currentTime - pos) < 0.1 ? s : { ...s, currentTime: pos }));
          }),
        );
        unsubs.push(
          loaded.onEnded(() => {
            if (disposed) return;
            setState((s) => ({ ...s, playing: false }));
          }),
        );

        setState((s) => ({ ...s, ready: true, duration: loaded.duration, error: null }));
      })
      .catch((err: unknown) => {
        if (disposed || (err instanceof DOMException && err.name === "AbortError")) return;
        // eslint-disable-next-line no-console
        console.warn("karaoke: failed to load stems", err);
        setState((s) => ({ ...s, error: "Couldn’t load the karaoke audio." }));
      });

    return () => {
      disposed = true;
      abort.abort();
      for (const off of unsubs) off();
      engineRef.current = null;
      engine?.dispose();
      wsRef.current = null;
      try {
        ws?.destroy();
      } catch {
        /* already torn down */
      }
      ctxRef.current = null;
      void ctx.close().catch(() => undefined);
    };
    // Re-create only when the source URLs change. Color/motion changes are
    // applied via setOptions in a separate effect to avoid rebuilding.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [container, instrumentalUrl, vocalsUrl]);

  // ── Live-apply color changes (theme toggle) without rebuilding ───────────
  useEffect(() => {
    colorsRef.current = colors;
    wsRef.current?.setOptions({
      waveColor: colors.wave,
      progressColor: colors.progress,
      cursorColor: colors.cursor,
    });
  }, [colors.wave, colors.progress, colors.cursor]);

  // Reduced-motion: cursor still tracks, but disable the smooth scroll cost.
  useEffect(() => {
    wsRef.current?.setOptions({ autoScroll: !reducedMotion });
  }, [reducedMotion]);

  // ── Imperative controls ──────────────────────────────────────────────────
  const playPause = useCallback(() => {
    const engine = engineRef.current;
    if (!engine) return;
    // The one and only gesture-context resume: AudioBufferSourceNode.start()
    // itself is not autoplay-gated, only the context is. Synchronous, FIRST.
    void ctxRef.current?.resume();
    if (engine.playing) {
      engine.pause();
      setState((s) => ({ ...s, playing: false }));
    } else {
      engine.play();
      setState((s) => ({ ...s, playing: true }));
    }
  }, []);

  const seek = useCallback((time: number) => {
    engineRef.current?.seek(time);
  }, []);

  const skip = useCallback(
    (delta: number) => {
      const engine = engineRef.current;
      if (!engine) return;
      seek(engine.getPosition() + delta);
    },
    [seek],
  );

  const setRate = useCallback((rate: number) => {
    engineRef.current?.setRate(rate);
    setState((s) => ({ ...s, rate }));
  }, []);

  const applyLevel = useCallback((level: number) => {
    const l = Math.max(0, Math.min(1, level));
    levelRef.current = l;
    const g = gainsForLevel(l);
    engineRef.current?.setGains(g.inst, g.voc);
    setState((s) => ({ ...s, vocalLevel: l }));
  }, []);

  const setVocalLevel = useCallback((level: number) => applyLevel(level), [applyLevel]);

  const setMix = useCallback(
    (mix: "instrumental" | "vocals") => applyLevel(mix === "vocals" ? 1 : 0),
    [applyLevel],
  );

  const toggleLoop = useCallback(() => {
    setState((s) => {
      const loop = !s.loop;
      loopRef.current = loop;
      engineRef.current?.setLoop(loop);
      return { ...s, loop };
    });
  }, []);

  const markA = useCallback(() => {
    setState((s) => {
      const t = engineRef.current?.getPosition() ?? 0;
      const region: ABRegion = { a: s.region.a != null ? null : t, b: s.region.b };
      regionRef.current = region;
      engineRef.current?.setRegion(region);
      return { ...s, region };
    });
  }, []);

  const markB = useCallback(() => {
    setState((s) => {
      const t = engineRef.current?.getPosition() ?? 0;
      const region: ABRegion = { a: s.region.a, b: s.region.b != null ? null : t };
      regionRef.current = region;
      engineRef.current?.setRegion(region);
      return { ...s, region };
    });
  }, []);

  const clearRegion = useCallback(() => {
    const region: ABRegion = { a: null, b: null };
    regionRef.current = region;
    engineRef.current?.setRegion(region);
    setState((s) => ({ ...s, region }));
  }, []);

  const subscribeTime = useCallback((cb: (t: number) => void) => {
    timeSubsRef.current.add(cb);
    return () => {
      timeSubsRef.current.delete(cb);
    };
  }, []);

  return useMemo(
    () => ({
      ...state,
      playPause,
      seek,
      skip,
      setRate,
      setVocalLevel,
      setMix,
      toggleLoop,
      markA,
      markB,
      clearRegion,
      subscribeTime,
    }),
    [state, playPause, seek, skip, setRate, setVocalLevel, setMix, toggleLoop, markA, markB, clearRegion, subscribeTime],
  );
}
