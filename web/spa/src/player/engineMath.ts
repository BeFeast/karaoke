// Pure math for the single-clock dual-stem engine (#113). No DOM, no Web
// Audio — every function here is unit-tested under `bun test` without a
// browser. The engine (stemEngine.ts) is a thin imperative shell over these.

/**
 * One linear playback segment: between any two transport events (play, pause,
 * seek, rate change) the track position is a straight line of the AudioContext
 * clock. Transport events end the current segment and start a new one, so the
 * engine never integrates — position is always derivable from the latest
 * segment alone.
 */
export interface PlaySegment {
  /** Track position (s) when the sources last started (or were paused at). */
  startOffset: number;
  /** `ctx.currentTime` at that start. */
  startCtxTime: number;
  /** Playback rate the segment runs at. */
  rate: number;
  /** Whether the clock is advancing. */
  playing: boolean;
}

/** Track position at context time `now`, clamped to `[0, duration]`. */
export function positionAt(now: number, seg: PlaySegment, duration: number): number {
  const raw = seg.playing ? seg.startOffset + (now - seg.startCtxTime) * seg.rate : seg.startOffset;
  return Math.max(0, Math.min(duration, raw));
}

/**
 * Equal-power crossfade between the stems. `level` 0 = instrumental only,
 * 1 = vocals only; the endpoints are EXACT (a solo preset must fully mute the
 * other stem — the additive law this replaces held the instrumental at 0.75
 * gain on the "vocals" preset, which is the #113 "still plays instruments"
 * bug). In between, cos/sin keeps perceived loudness roughly constant.
 */
export function gainsForLevel(level: number): { inst: number; voc: number } {
  const l = Math.max(0, Math.min(1, level));
  if (l <= 0) return { inst: 1, voc: 0 };
  if (l >= 1) return { inst: 0, voc: 1 };
  return { inst: Math.cos((l * Math.PI) / 2), voc: Math.sin((l * Math.PI) / 2) };
}

/**
 * Where the playhead must jump on this tick, or null to keep going. Ported
 * verbatim from the old wavesurfer handlers: the A–B region wraps regardless
 * of the loop flag (B is an "always return to A" marker), while plain
 * track-end only wraps when loop is on.
 */
export function loopSeekTarget(
  pos: number,
  region: { a: number | null; b: number | null },
  loop: boolean,
  duration: number,
): number | null {
  if (region.b != null && pos >= region.b) return region.a ?? 0;
  if (pos >= duration && loop) return region.a ?? 0;
  return null;
}

/**
 * Horizontal rail drag → percent: where `clientX` falls along a track that
 * starts at `rectLeft` and spans `rectWidth`, clamped to [0, 100] and
 * rounded. Shared by the blend rails (Perf's drag + the BlendRail recipe).
 * A zero/negative width (rail not laid out yet) maps to 0, never NaN.
 */
export function railPct(clientX: number, rectLeft: number, rectWidth: number): number {
  if (rectWidth <= 0) return 0;
  return Math.round(Math.max(0, Math.min(100, ((clientX - rectLeft) / rectWidth) * 100)));
}

/**
 * Waveform peaks from one decoded channel: max(|sample|) per bucket,
 * normalized so the loudest bucket is 1 (all-zero input stays all-zero).
 * 4096 buckets is plenty for a full-width waveform canvas.
 */
export function computePeaks(channel: Float32Array, buckets = 4096): number[] {
  const out = new Array<number>(buckets).fill(0);
  if (channel.length === 0 || buckets <= 0) return out;
  const samplesPerBucket = channel.length / buckets;
  let globalMax = 0;
  for (let b = 0; b < buckets; b++) {
    const start = Math.floor(b * samplesPerBucket);
    const end = Math.min(channel.length, Math.max(start + 1, Math.floor((b + 1) * samplesPerBucket)));
    let max = 0;
    for (let i = start; i < end; i++) {
      const v = Math.abs(channel[i]);
      if (v > max) max = v;
    }
    out[b] = max;
    if (max > globalMax) globalMax = max;
  }
  if (globalMax > 0) {
    for (let b = 0; b < buckets; b++) out[b] /= globalMax;
  }
  return out;
}
