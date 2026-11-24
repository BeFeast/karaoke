import { describe, expect, test } from "bun:test";
import { computePeaks, gainsForLevel, loopSeekTarget, positionAt, railPct, type PlaySegment } from "./engineMath";

describe("gainsForLevel", () => {
  test("the backing track always plays at full gain", () => {
    for (const l of [0, 0.25, 0.5, 1]) expect(gainsForLevel(l).inst).toBe(1);
  });
  test("0 mutes the vocals exactly, 100% is the original level", () => {
    expect(gainsForLevel(0).voc).toBe(0);
    expect(gainsForLevel(1).voc).toBeCloseTo(1, 10);
  });
  test("perceptual curve: 25% is -22.5 dB, 50% is -15 dB", () => {
    expect(20 * Math.log10(gainsForLevel(0.25).voc)).toBeCloseTo(-22.5, 6);
    expect(20 * Math.log10(gainsForLevel(0.5).voc)).toBeCloseTo(-15, 6);
  });
  test("monotonic and clamped", () => {
    let prev = -1;
    for (let l = 0; l <= 1.0001; l += 0.05) {
      const g = gainsForLevel(l).voc;
      expect(g).toBeGreaterThan(prev);
      prev = g;
    }
    expect(gainsForLevel(-0.5)).toEqual({ inst: 1, voc: 0 });
    expect(gainsForLevel(1.5).voc).toBeCloseTo(1, 10);
  });
});

describe("positionAt", () => {
  const playing: PlaySegment = { startOffset: 10, startCtxTime: 100, rate: 1.25, playing: true };

  test("advances by (now - start) * rate while playing", () => {
    expect(positionAt(104, playing, 600)).toBe(15);
  });

  test("paused segments hold startOffset", () => {
    const paused: PlaySegment = { ...playing, playing: false };
    expect(positionAt(104, paused, 600)).toBe(10);
  });

  test("clamps to [0, duration]", () => {
    expect(positionAt(10_000, playing, 600)).toBe(600);
    expect(positionAt(0, playing, 600)).toBe(0); // now before startCtxTime → never negative
  });

  test("consecutive segments compose: rate change mid-play stays continuous", () => {
    const seg1: PlaySegment = { startOffset: 0, startCtxTime: 100, rate: 1, playing: true };
    const atSwitch = positionAt(110, seg1, 600);
    expect(atSwitch).toBe(10);
    // Rate change at ctx time 110 starts a new segment from the same position.
    const seg2: PlaySegment = { startOffset: atSwitch, startCtxTime: 110, rate: 2, playing: true };
    expect(positionAt(115, seg2, 600)).toBe(20);
  });
});

describe("loopSeekTarget", () => {
  test("A–B region wraps to A even with loop OFF", () => {
    expect(loopSeekTarget(20, { a: 5, b: 20 }, false, 600)).toBe(5);
    expect(loopSeekTarget(25, { a: 5, b: 20 }, false, 600)).toBe(5);
  });

  test("B without A wraps to 0", () => {
    expect(loopSeekTarget(20, { a: null, b: 20 }, false, 600)).toBe(0);
  });

  test("track end with loop ON wraps to A ?? 0", () => {
    expect(loopSeekTarget(600, { a: null, b: null }, true, 600)).toBe(0);
    expect(loopSeekTarget(600, { a: 3, b: null }, true, 600)).toBe(3);
  });

  test("track end with loop OFF does not wrap", () => {
    expect(loopSeekTarget(600, { a: null, b: null }, false, 600)).toBeNull();
  });

  test("inside the region (or mid-track) keeps going", () => {
    expect(loopSeekTarget(10, { a: 5, b: 20 }, false, 600)).toBeNull();
    expect(loopSeekTarget(10, { a: null, b: null }, true, 600)).toBeNull();
  });
});

describe("railPct", () => {
  test("left edge → 0", () => {
    expect(railPct(40, 40, 200)).toBe(0);
  });

  test("right edge → 100", () => {
    expect(railPct(240, 40, 200)).toBe(100);
  });

  test("midpoint → 50", () => {
    expect(railPct(140, 40, 200)).toBe(50);
  });

  test("out-of-range clamps on both sides", () => {
    expect(railPct(-500, 40, 200)).toBe(0);
    expect(railPct(5000, 40, 200)).toBe(100);
  });

  test("zero-width rail guards to 0 (no NaN)", () => {
    expect(railPct(120, 40, 0)).toBe(0);
    expect(railPct(120, 40, -10)).toBe(0);
  });
});

describe("computePeaks", () => {
  test("exact bucket count, all values in [0,1]", () => {
    const ramp = new Float32Array(48_000);
    for (let i = 0; i < ramp.length; i++) ramp[i] = i / ramp.length;
    const peaks = computePeaks(ramp, 64);
    expect(peaks.length).toBe(64);
    for (const p of peaks) {
      expect(p).toBeGreaterThanOrEqual(0);
      expect(p).toBeLessThanOrEqual(1);
    }
    expect(peaks[63]).toBe(1); // ramp peaks in the last bucket
  });

  test("max-amplitude bucket lands where the synthetic peak is", () => {
    const data = new Float32Array(8192);
    data[6144] = 0.5; // lone spike at 3/4 of the signal
    const peaks = computePeaks(data, 64);
    expect(peaks[48]).toBe(1); // normalized so the global max = 1
    expect(peaks.filter((p) => p > 0).length).toBe(1);
  });

  test("all-zero input stays all-zero (no NaN from normalization)", () => {
    const peaks = computePeaks(new Float32Array(4096), 32);
    expect(peaks.length).toBe(32);
    expect(peaks.every((p) => p === 0)).toBe(true);
  });

  test("defaults to 4096 buckets", () => {
    expect(computePeaks(new Float32Array(8192)).length).toBe(4096);
  });
});
