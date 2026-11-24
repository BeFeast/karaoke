// Dual-stem karaoke playback hook.
//
// The audio engine is DualStemEngine (stemEngine.ts): both stems decoded into
// AudioBuffers and played by co-started AudioBufferSourceNodes on ONE
// AudioContext clock, mixed by per-stem GainNodes — sample-locked by
// construction, no follower element, no drift correction (#113).
//
// The Stage draws its own scrub bar (Sonereli UI/UX v1), so there is no
// waveform: the engine tick feeds `subscribeTime` (raw position, for the
// lyrics) and React state (quantized to 0.1 s so the transport re-renders at
// ~10 Hz, not at the rAF rate).

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { gainsForLevel } from "./engineMath";
import { type ABRegion, DualStemEngine } from "./stemEngine";

/** Available playback speeds for the rate control. */
export const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5] as const;

export interface KaraokePlayerOptions {
  /** Same-origin URL of the instrumental stem; null = no song loaded. */
  instrumentalUrl: string | null;
  fetchStem?: (url: string, signal?: AbortSignal) => Promise<Response>;
  /** Same-origin URL of the vocals stem, if present. */
  vocalsUrl: string | null;
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
  /** The track played to its end (cleared by play / seek). */
  ended: boolean;
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
  const { instrumentalUrl, vocalsUrl, fetchStem } = opts;

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
    ended: false,
    error: null,
  });

  const ctxRef = useRef<AudioContext | null>(null);
  const engineRef = useRef<DualStemEngine | null>(null);
  // Mutable mirrors so the async setup reads fresh values without re-running.
  const levelRef = useRef(0.5);
  const loopRef = useRef(false);
  const regionRef = useRef<ABRegion>({ a: null, b: null });
  const timeSubsRef = useRef(new Set<(t: number) => void>());

  // ── Build the engine once per URL set ───────────────────────────────────
  useEffect(() => {
    setState((s) => ({ ...s, ready: false, playing: false, duration: 0, currentTime: 0, ended: false,
      hasVocals: vocalsUrl != null, error: null }));
    if (!instrumentalUrl) return;
    let disposed = false;
    const abort = new AbortController();
    let engine: DualStemEngine | null = null;
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

    DualStemEngine.load({ instrumentalUrl, vocalsUrl, fetchStem, ctx, signal: abort.signal })
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

        // engine → React: raw time feed, quantized state.
        unsubs.push(
          loaded.onTick((pos, force) => {
            if (disposed) return;
            for (const cb of timeSubsRef.current) cb(pos);
            // ~10 Hz for the mm:ss display; transport events (pause/seek)
            // always land exactly so the display never shows a stale 0.1 s.
            setState((s) => (!force && Math.abs(s.currentTime - pos) < 0.1 ? s : { ...s, currentTime: pos }));
          }),
        );
        unsubs.push(
          loaded.onEnded(() => {
            if (disposed) return;
            setState((s) => ({ ...s, playing: false, ended: true }));
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
      ctxRef.current = null;
      void ctx.close().catch(() => undefined);
    };
  }, [instrumentalUrl, vocalsUrl, fetchStem]);

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
      setState((s) => ({ ...s, playing: true, ended: false }));
    }
  }, []);

  const seek = useCallback((time: number) => {
    engineRef.current?.seek(time);
    setState((s) => (s.ended ? { ...s, ended: false } : s));
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
