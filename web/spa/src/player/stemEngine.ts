// Single-clock dual-stem playback engine (#113).
//
// Both stems are fetched and decoded into AudioBuffers up front, then played
// through two AudioBufferSourceNodes scheduled to start() at the SAME
// AudioContext timestamp. One context clock drives both stems, so they are
// sample-locked by construction — there is no follower, no drift controller,
// and no per-frame correction to glitch. (The two-<audio>-element design this
// replaces could not hold sync on Safari: every corrective playbackRate /
// currentTime write re-primed WebKit's time-stretcher, 60–120×/s.)
//
// AudioBufferSourceNodes are one-shot: every transport change (play, seek,
// rate) stops the current pair and schedules a fresh pair at the new offset.
// Between transport events the track position is a pure function of the
// context clock (engineMath.positionAt), so pause/seek/cursor never read from
// a media element.
//
// Memory budget: decoded stereo Float32 at 48 kHz is ~384 KB/s per stem →
// ~45 MB/min for both stems (~225 MB for a 5-minute song). Acceptable for the
// target (LAN desktop + occasional mobile, songs ≤ ~6 min). There is no
// streaming fallback in scope; if fetch/decode throws (OOM, unsupported
// codec), the hook surfaces state.error and the existing error UI renders.

import { type PlaySegment, loopSeekTarget, positionAt } from "./engineMath";

/**
 * Lead time between scheduling and the shared start() timestamp. Gives the
 * audio thread room to begin both sources on the exact same tick instead of
 * "as soon as possible" (which could differ per node under load).
 */
const CO_START_LEAD_S = 0.03;
/** Gain-change smoothing (setTargetAtTime time constant) — zipper-free blend. */
const GAIN_SMOOTHING_S = 0.01;

export interface StemEngineLoadOptions {
  /** Same-origin URL of the instrumental stem (`/share/{token}/...`). */
  instrumentalUrl: string;
  /** Same-origin URL of the vocals stem, or null for instrumental-only jobs. */
  vocalsUrl: string | null;
  /** The context the caller owns (created in, resumed by, and closed by the hook). */
  ctx: AudioContext;
  /** Aborts the in-flight stem fetches (component unmount). */
  signal?: AbortSignal;
}

export interface ABRegion {
  a: number | null;
  b: number | null;
}

async function fetchDecode(url: string, ctx: AudioContext, signal?: AbortSignal): Promise<AudioBuffer> {
  // Stems are same-origin, so no crossOrigin/CORS dance — a plain fetch.
  const res = await fetch(url, { signal });
  if (!res.ok) throw new Error(`stem fetch failed: HTTP ${res.status} for ${url}`);
  const bytes = await res.arrayBuffer();
  return await ctx.decodeAudioData(bytes);
}

export class DualStemEngine {
  /** Track duration in seconds (the instrumental buffer is authoritative). */
  readonly duration: number;
  /** Exposed so the caller can compute waveform peaks from channel 0. */
  readonly instrumentalBuffer: AudioBuffer;

  /** A–B markers; read on every tick. The caller mutates via setRegion. */
  private region: ABRegion = { a: null, b: null };
  /** Whole-track loop flag; read at track end. */
  private loop = false;

  private readonly ctx: AudioContext;
  private readonly vocalBuffer: AudioBuffer | null;
  private readonly instGain: GainNode;
  private readonly vocGain: GainNode | null;
  private instSource: AudioBufferSourceNode | null = null;
  private vocSource: AudioBufferSourceNode | null = null;
  private segment: PlaySegment = { startOffset: 0, startCtxTime: 0, rate: 1, playing: false };
  private rate = 1;
  private rafId: number | null = null;
  private tickSubs = new Set<(pos: number, force: boolean) => void>();
  private endedSubs = new Set<() => void>();
  private disposed = false;

  static async load(opts: StemEngineLoadOptions): Promise<DualStemEngine> {
    const [instBuf, vocBuf] = await Promise.all([
      fetchDecode(opts.instrumentalUrl, opts.ctx, opts.signal),
      opts.vocalsUrl ? fetchDecode(opts.vocalsUrl, opts.ctx, opts.signal) : Promise.resolve(null),
    ]);
    return new DualStemEngine(opts.ctx, instBuf, vocBuf);
  }

  private constructor(ctx: AudioContext, instBuf: AudioBuffer, vocBuf: AudioBuffer | null) {
    this.ctx = ctx;
    this.instrumentalBuffer = instBuf;
    this.vocalBuffer = vocBuf;
    this.duration = instBuf.duration;
    // The mixer: one GainNode per stem, created once; sources come and go.
    this.instGain = ctx.createGain();
    this.instGain.connect(ctx.destination);
    this.instGain.gain.value = 1;
    if (vocBuf) {
      this.vocGain = ctx.createGain();
      this.vocGain.connect(ctx.destination);
      this.vocGain.gain.value = 0;
    } else {
      this.vocGain = null;
    }
  }

  get playing(): boolean {
    return this.segment.playing;
  }

  /** Track position right now, from the context clock. */
  getPosition(): number {
    // Clamp `now` to the segment start so the 30ms co-start lead never reads
    // as a momentary step backwards right after play/seek.
    const now = Math.max(this.ctx.currentTime, this.segment.startCtxTime);
    return positionAt(now, this.segment, this.duration);
  }

  /**
   * Blend the stems. Smoothed with setTargetAtTime so slider drags are
   * zipper-free. Instrumental-only jobs pin the instrumental at full gain.
   */
  setGains(inst: number, voc: number): void {
    const t = this.ctx.currentTime;
    this.instGain.gain.setTargetAtTime(this.vocGain ? inst : 1, t, GAIN_SMOOTHING_S);
    this.vocGain?.gain.setTargetAtTime(voc, t, GAIN_SMOOTHING_S);
  }

  setRegion(region: ABRegion): void {
    this.region = region;
  }

  setLoop(loop: boolean): void {
    this.loop = loop;
  }

  play(): void {
    if (this.disposed || this.segment.playing) return;
    // Playing again from the very end restarts, mirroring media-element play().
    const offset = this.segment.startOffset >= this.duration ? 0 : this.segment.startOffset;
    this.startSources(offset, this.rate);
  }

  pause(): void {
    if (!this.segment.playing) return;
    const pos = this.getPosition();
    this.stopSources();
    this.segment = { startOffset: pos, startCtxTime: this.ctx.currentTime, rate: this.rate, playing: false };
    this.stopTicking();
    this.emitTick(true); // land the UI on the exact pause position
  }

  /** Absolute seek, clamped to [0, duration]. Keeps playing if playing. */
  seek(time: number): void {
    if (this.disposed) return;
    const target = Math.max(0, Math.min(this.duration, time));
    if (this.segment.playing) {
      this.startSources(target, this.rate);
    } else {
      this.segment = { ...this.segment, startOffset: target };
    }
    this.emitTick(true); // cursor + lyrics follow even while paused
  }

  /**
   * Playback rate for BOTH stems. While playing, restarts the source pair at
   * the current position so the position stays piecewise-linear and the stems
   * stay trivially co-started. (AudioBufferSourceNode has no preservesPitch:
   * pitch shifts with rate — documented UX change, fine for a practice tool.)
   */
  setRate(rate: number): void {
    if (this.disposed) return;
    this.rate = rate;
    if (this.segment.playing) {
      this.startSources(this.getPosition(), rate);
    } else {
      this.segment = { ...this.segment, rate };
    }
  }

  /** Per-tick position feed. `force` marks transport-event ticks (pause/seek). */
  onTick(cb: (pos: number, force: boolean) => void): () => void {
    this.tickSubs.add(cb);
    return () => {
      this.tickSubs.delete(cb);
    };
  }

  /** Fires when the track reaches the end with loop off (engine has paused). */
  onEnded(cb: () => void): () => void {
    this.endedSubs.add(cb);
    return () => {
      this.endedSubs.delete(cb);
    };
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.stopTicking();
    this.stopSources();
    this.tickSubs.clear();
    this.endedSubs.clear();
    this.instGain.disconnect();
    this.vocGain?.disconnect();
  }

  // ── Internals ──────────────────────────────────────────────────────────────

  /** Stop+discard the current pair and co-start a fresh pair at `offset`. */
  private startSources(offset: number, rate: number): void {
    this.stopSources();
    const t0 = this.ctx.currentTime + CO_START_LEAD_S;
    const inst = this.ctx.createBufferSource();
    inst.buffer = this.instrumentalBuffer;
    inst.playbackRate.value = rate;
    inst.connect(this.instGain);
    inst.start(t0, Math.min(offset, this.instrumentalBuffer.duration));
    this.instSource = inst;
    if (this.vocalBuffer && this.vocGain) {
      const voc = this.ctx.createBufferSource();
      voc.buffer = this.vocalBuffer;
      voc.playbackRate.value = rate;
      voc.connect(this.vocGain);
      // The vocals stem can be marginally shorter than the instrumental;
      // clamping the offset just means silence instead of a start() throw.
      voc.start(t0, Math.min(offset, this.vocalBuffer.duration));
      this.vocSource = voc;
    }
    this.segment = { startOffset: offset, startCtxTime: t0, rate, playing: true };
    this.startTicking();
  }

  private stopSources(): void {
    for (const src of [this.instSource, this.vocSource]) {
      if (!src) continue;
      try {
        src.stop();
      } catch {
        /* already stopped / never started — nothing to do */
      }
      src.disconnect();
    }
    this.instSource = null;
    this.vocSource = null;
  }

  // The single rAF tick: runs ONLY while playing (transport events emit their
  // own one-shot ticks). Decides loop/A–B wraps and track end from the
  // position math — source onended is NOT authoritative (it also fires on
  // every transport restart).
  private tick = (): void => {
    if (this.disposed || !this.segment.playing) {
      this.rafId = null;
      return;
    }
    const pos = this.getPosition();
    const target = loopSeekTarget(pos, this.region, this.loop, this.duration);
    if (target != null) {
      this.startSources(target, this.rate);
      this.emitTick(true);
    } else if (pos >= this.duration) {
      // Natural end, loop off: park the transport at the end and notify.
      this.stopSources();
      this.segment = { startOffset: this.duration, startCtxTime: this.ctx.currentTime, rate: this.rate, playing: false };
      this.emitTick(true);
      for (const cb of this.endedSubs) cb();
      this.rafId = null;
      return;
    } else {
      this.emitTick(false);
    }
    this.rafId = requestAnimationFrame(this.tick);
  };

  private startTicking(): void {
    if (this.rafId == null) this.rafId = requestAnimationFrame(this.tick);
  }

  private stopTicking(): void {
    if (this.rafId != null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }

  private emitTick(force: boolean): void {
    const pos = this.getPosition();
    for (const cb of this.tickSubs) cb(pos, force);
  }
}
