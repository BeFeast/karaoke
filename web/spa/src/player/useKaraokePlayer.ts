// Dual-stem karaoke playback engine.
//
// Two stems (instrumental `karaoke.mp3` + `vocals.mp3`), one transport. The
// design:
//
//   * wavesurfer.js wraps the INSTRUMENTAL <audio> element (its `media` option)
//     and is the single source of truth for transport — play/pause, seek, and
//     playback-rate all act on that master element, and wavesurfer renders the
//     instrumental waveform + cursor from it.
//   * the VOCALS <audio> element is a follower: we mirror the master's
//     play/pause/seek/rate onto it and continuously correct drift so the two
//     stems stay sample-synced.
//   * both elements are routed through the WebAudio graph
//       MediaElementSource → GainNode → destination
//     so the gains form a 2-channel mixer. The blend slider and the A/B toggle
//     are just gain presets. (Once an element is wrapped in a
//     MediaElementSource its sound only reaches the speakers via the graph, so
//     the GainNodes — not element.volume — are the mixer.)
//
// Browser note: MediaElementSource is well supported, but the AudioContext
// must be resumed from a user gesture (we resume on the first play()). Perfect
// sample-accuracy across two independent <audio> elements is not guaranteed by
// the platform; we keep them tight by re-syncing on every transport event and
// nudging the follower whenever it drifts past a small threshold.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import WaveSurfer from "wavesurfer.js";

/** Largest follower/​master gap we tolerate before a hard re-seek (seconds). */
const DRIFT_TOLERANCE = 0.06;
/** Available playback speeds for the rate control. */
export const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5] as const;

export interface KaraokePlayerOptions {
  /** Same-origin URL of the instrumental stem (the master + waveform). */
  instrumentalUrl: string;
  /** Same-origin URL of the vocals stem (the follower), if present. */
  vocalsUrl: string | null;
  /** Waveform container element. */
  container: HTMLElement | null;
  /** Wave / progress / cursor colors (resolved from CSS tokens by the caller). */
  colors: { wave: string; progress: string; cursor: string };
  /** Skip the moving-cursor animation work for reduced-motion users. */
  reducedMotion: boolean;
}

export interface ABRegion {
  a: number | null;
  b: number | null;
}

export interface KaraokePlayerState {
  ready: boolean;
  playing: boolean;
  duration: number;
  currentTime: number;
  rate: number;
  /** 0 = instrumental only … 1 = full vocals over the instrumental. */
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
  /** 0 = instrumental only, 1 = full vocals. Drives both gains. */
  setVocalLevel: (level: number) => void;
  /** A/B toggle preset: instrumental-only vs vocals-only. */
  setMix: (mix: "instrumental" | "vocals") => void;
  toggleLoop: () => void;
  /** Set the A or B repeat marker at the current playhead (toggles off if set). */
  markA: () => void;
  markB: () => void;
  clearRegion: () => void;
}

// Equal-power-ish blend: at level 0 only the instrumental is heard; at level 1
// the vocals are full and the instrumental ducks slightly so the voice sits on
// top without clipping. Linear is fine for a practice tool.
function gainsForLevel(level: number): { inst: number; voc: number } {
  const l = Math.max(0, Math.min(1, level));
  return { inst: 1 - 0.25 * l, voc: l };
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
  const instGainRef = useRef<GainNode | null>(null);
  const vocGainRef = useRef<GainNode | null>(null);
  const vocalElRef = useRef<HTMLAudioElement | null>(null);
  // Mutable mirrors so event callbacks read fresh values without re-subscribing.
  const levelRef = useRef(0.5);
  const loopRef = useRef(false);
  const regionRef = useRef<ABRegion>({ a: null, b: null });

  // ── Set up wavesurfer + the WebAudio mixer once per URL set ──────────────
  useEffect(() => {
    if (!container) return;
    let disposed = false;

    // Stems are always same-origin (`/share/{token}/{name}`), so no crossOrigin
    // is needed — setting it would force a CORS preflight that the LAN host
    // doesn't answer. WebAudio's MediaElementSource is happy with same-origin
    // media as-is.
    const instEl = new Audio();
    instEl.preload = "auto";
    instEl.src = instrumentalUrl;

    const ws = WaveSurfer.create({
      container,
      media: instEl,
      backend: "MediaElement",
      height: 96,
      waveColor: colors.wave,
      progressColor: colors.progress,
      cursorColor: colors.cursor,
      cursorWidth: 2,
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      normalize: true,
      interact: true,
      autoplay: false,
      hideScrollbar: true,
    });
    wsRef.current = ws;

    // WebAudio mixer. createMediaElementSource can only be called once per
    // element, so we build the graph here and tear it down on cleanup.
    let ctx: AudioContext | null = null;
    let instGain: GainNode | null = null;
    let vocGain: GainNode | null = null;
    let vocalEl: HTMLAudioElement | null = null;
    try {
      const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      ctx = new Ctor();
      instGain = ctx.createGain();
      ctx.createMediaElementSource(instEl).connect(instGain).connect(ctx.destination);
      if (vocalsUrl) {
        vocalEl = new Audio();
        vocalEl.preload = "auto";
        vocalEl.src = vocalsUrl;
        vocGain = ctx.createGain();
        ctx.createMediaElementSource(vocalEl).connect(vocGain).connect(ctx.destination);
      }
      const g = gainsForLevel(levelRef.current);
      instGain.gain.value = vocalsUrl ? g.inst : 1;
      if (vocGain) vocGain.gain.value = g.voc;
    } catch (err) {
      // If WebAudio is unavailable we still let wavesurfer play the
      // instrumental on its own; the blend/AB controls just won't apply.
      // eslint-disable-next-line no-console
      console.warn("karaoke: WebAudio mixer unavailable", err);
    }
    ctxRef.current = ctx;
    instGainRef.current = instGain;
    vocGainRef.current = vocGain;
    vocalElRef.current = vocalEl;

    // ── Keep the vocal follower synced to the instrumental master ──────────
    const syncVocal = (hard: boolean) => {
      if (!vocalEl) return;
      const t = instEl.currentTime;
      if (hard || Math.abs(vocalEl.currentTime - t) > DRIFT_TOLERANCE) {
        vocalEl.currentTime = t;
      }
      vocalEl.playbackRate = instEl.playbackRate;
    };

    const onInstError = () => {
      if (disposed) return;
      setState((s) => ({ ...s, error: "Couldn’t load the instrumental track." }));
    };
    instEl.addEventListener("error", onInstError);

    const subs: Array<() => void> = [];
    subs.push(
      ws.on("ready", (duration) => {
        if (disposed) return;
        setState((s) => ({ ...s, ready: true, duration, error: null }));
      }),
    );
    subs.push(
      ws.on("error", () => {
        if (disposed) return;
        setState((s) => ({ ...s, error: "Couldn’t decode the audio for the waveform." }));
      }),
    );
    subs.push(
      ws.on("play", () => {
        if (disposed) return;
        void ctx?.resume();
        syncVocal(true);
        void vocalEl?.play().catch(() => undefined);
        setState((s) => ({ ...s, playing: true }));
      }),
    );
    subs.push(
      ws.on("pause", () => {
        if (disposed) return;
        vocalEl?.pause();
        syncVocal(true);
        setState((s) => ({ ...s, playing: false }));
      }),
    );
    subs.push(
      ws.on("finish", () => {
        if (disposed) return;
        vocalEl?.pause();
        setState((s) => ({ ...s, playing: false }));
      }),
    );
    subs.push(
      ws.on("seeking", () => {
        if (disposed) return;
        syncVocal(true);
      }),
    );
    subs.push(
      ws.on("timeupdate", (currentTime) => {
        if (disposed) return;
        // A–B enforcement on the master clock (plain loop is handled on finish).
        const { a, b } = regionRef.current;
        if (b != null && currentTime >= b) {
          ws.setTime(a ?? 0);
          syncVocal(true);
        }
        // Drift-correct the follower without yanking it every frame.
        syncVocal(false);
        setState((s) => (s.currentTime === currentTime ? s : { ...s, currentTime }));
      }),
    );
    subs.push(
      ws.on("finish", () => {
        if (disposed) return;
        if (loopRef.current) {
          const { a } = regionRef.current;
          ws.setTime(a ?? 0);
          void ws.play();
        }
      }),
    );

    return () => {
      disposed = true;
      instEl.removeEventListener("error", onInstError);
      for (const off of subs) off();
      wsRef.current = null;
      try {
        ws.destroy();
      } catch {
        /* already torn down */
      }
      vocalEl?.pause();
      void ctx?.close().catch(() => undefined);
      ctxRef.current = null;
      instGainRef.current = null;
      vocGainRef.current = null;
      vocalElRef.current = null;
    };
    // Re-create only when the source URLs change. Color/motion changes are
    // applied via setOptions in a separate effect to avoid rebuilding the graph.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [container, instrumentalUrl, vocalsUrl]);

  // ── Live-apply color changes (theme toggle) without rebuilding ───────────
  useEffect(() => {
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
    void ctxRef.current?.resume();
    void wsRef.current?.playPause();
  }, []);

  const seek = useCallback((time: number) => {
    const ws = wsRef.current;
    if (!ws) return;
    const dur = ws.getDuration() || 0;
    ws.setTime(Math.max(0, Math.min(dur, time)));
  }, []);

  const skip = useCallback(
    (delta: number) => {
      const ws = wsRef.current;
      if (!ws) return;
      seek(ws.getCurrentTime() + delta);
    },
    [seek],
  );

  const setRate = useCallback((rate: number) => {
    wsRef.current?.setPlaybackRate(rate, true);
    const vocalEl = vocalElRef.current;
    if (vocalEl) vocalEl.playbackRate = rate;
    setState((s) => ({ ...s, rate }));
  }, []);

  const applyLevel = useCallback((level: number) => {
    const l = Math.max(0, Math.min(1, level));
    levelRef.current = l;
    const g = gainsForLevel(l);
    const inst = instGainRef.current;
    const voc = vocGainRef.current;
    const t = ctxRef.current?.currentTime ?? 0;
    if (inst) inst.gain.setTargetAtTime(voc ? g.inst : 1, t, 0.01);
    if (voc) voc.gain.setTargetAtTime(g.voc, t, 0.01);
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
      return { ...s, loop };
    });
  }, []);

  const markA = useCallback(() => {
    setState((s) => {
      const t = wsRef.current?.getCurrentTime() ?? 0;
      const a = s.region.a != null ? null : t;
      const region: ABRegion = { a, b: a == null ? s.region.b : s.region.b };
      regionRef.current = region;
      return { ...s, region };
    });
  }, []);

  const markB = useCallback(() => {
    setState((s) => {
      const t = wsRef.current?.getCurrentTime() ?? 0;
      const b = s.region.b != null ? null : t;
      const region: ABRegion = { a: s.region.a, b };
      regionRef.current = region;
      return { ...s, region };
    });
  }, []);

  const clearRegion = useCallback(() => {
    const region: ABRegion = { a: null, b: null };
    regionRef.current = region;
    setState((s) => ({ ...s, region }));
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
    }),
    [state, playPause, seek, skip, setRate, setVocalLevel, setMix, toggleLoop, markA, markB, clearRegion],
  );
}
