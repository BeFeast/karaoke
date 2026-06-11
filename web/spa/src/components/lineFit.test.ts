import { describe, expect, test } from "bun:test";
import { FIT_FLOOR, balancedSplit, fitLineScale, splitFill } from "./lineFit";

// The live overflow case from #166 (Pantera — Cowboys From Hell).
const PANTERA = "Comin' for you we're the cowboys from hell";

describe("fitLineScale", () => {
  test("a line that fits keeps the base scale", () => {
    expect(fitLineScale(500, 800)).toEqual({ scale: 1, wrap: false });
    expect(fitLineScale(800, 800)).toEqual({ scale: 1, wrap: false }); // exact fit
  });

  test("never upscales short lines", () => {
    expect(fitLineScale(10, 10_000).scale).toBe(1);
  });

  test("overflow scales down proportionally, no wrap above the floor", () => {
    expect(fitLineScale(1000, 800)).toEqual({ scale: 0.8, wrap: false });
  });

  test("exactly the floor still downscales (wrap is strictly below)", () => {
    expect(fitLineScale(1000, 600)).toEqual({ scale: FIT_FLOOR, wrap: false });
  });

  test("below the floor signals wrap but still returns the exact-fit scale", () => {
    const fit = fitLineScale(2000, 800);
    expect(fit.wrap).toBe(true);
    expect(fit.scale).toBe(0.4); // the no-clip fallback when the line can't split
  });

  test("base multiplies the returned scale (neighbour lines at their scale)", () => {
    const overflow = fitLineScale(1000, 800, 1.5);
    expect(overflow.scale).toBeCloseTo(1.2);
    expect(overflow.wrap).toBe(false);
    expect(fitLineScale(500, 800, 0.5)).toEqual({ scale: 0.5, wrap: false });
  });

  test("degenerate measurements keep the base scale and never wrap", () => {
    expect(fitLineScale(0, 800)).toEqual({ scale: 1, wrap: false });
    expect(fitLineScale(Number.NaN, 800)).toEqual({ scale: 1, wrap: false });
    expect(fitLineScale(800, 0)).toEqual({ scale: 1, wrap: false });
    expect(fitLineScale(800, Number.NaN, 2)).toEqual({ scale: 2, wrap: false });
  });
});

describe("balancedSplit", () => {
  test("splits at the whitespace nearest the midpoint", () => {
    expect(balancedSplit("ab cd")).toEqual(["ab", "cd"]);
    expect(balancedSplit("a bb ccccc")).toEqual(["a bb", "ccccc"]);
  });

  test("the Pantera line splits into two singable halves", () => {
    const split = balancedSplit(PANTERA);
    expect(split).toEqual(["Comin' for you we're", "the cowboys from hell"]);
  });

  test("no split point on the best whitespace loses exactly one separator", () => {
    const split = balancedSplit(PANTERA);
    expect(split).not.toBeNull();
    const [head, tail] = split as [string, string];
    expect(`${head} ${tail}`).toBe(PANTERA);
  });

  test("no interior whitespace → null (single giant word falls back to downscale)", () => {
    expect(balancedSplit("Supercalifragilisticexpialidocious")).toBeNull();
    expect(balancedSplit("")).toBeNull();
    expect(balancedSplit("   ")).toBeNull();
  });

  test("surrounding whitespace is trimmed before splitting", () => {
    expect(balancedSplit("  ab cd  ")).toEqual(["ab", "cd"]);
  });

  test("a run of spaces at the split point leaves no dangling whitespace", () => {
    const split = balancedSplit("aaaa    bbbb");
    expect(split).toEqual(["aaaa", "bbbb"]);
  });
});

describe("splitFill", () => {
  test("endpoints: 0% → both empty, 100% → both full", () => {
    expect(splitFill(0, 10, 10)).toEqual([0, 0]);
    expect(splitFill(100, 10, 10)).toEqual([100, 100]);
  });

  test("head fills completely before the tail starts", () => {
    const total = 10 + 1 + 9;
    const atBoundary = (10 / total) * 100; // chars sung == headLen
    const [head, tail] = splitFill(atBoundary, 10, 9);
    expect(head).toBe(100);
    expect(tail).toBe(0);
  });

  test("mid-head progress maps linearly onto the head row", () => {
    const total = 10 + 1 + 9;
    const [head, tail] = splitFill((5 / total) * 100, 10, 9);
    expect(head).toBeCloseTo(50);
    expect(tail).toBe(0);
  });

  test("tail progress maps linearly onto the tail row", () => {
    const total = 10 + 1 + 9;
    const sung = ((10 + 1 + 4.5) / total) * 100;
    const [head, tail] = splitFill(sung, 10, 9);
    expect(head).toBe(100);
    expect(tail).toBeCloseTo(50);
  });

  test("both rows are monotonic in sung%", () => {
    let prev: [number, number] = [0, 0];
    for (let s = 0; s <= 100; s += 2.5) {
      const cur = splitFill(s, 17, 23);
      expect(cur[0]).toBeGreaterThanOrEqual(prev[0]);
      expect(cur[1]).toBeGreaterThanOrEqual(prev[1]);
      prev = cur;
    }
  });

  test("out-of-range sung clamps to the endpoints", () => {
    expect(splitFill(-20, 10, 10)).toEqual([0, 0]);
    expect(splitFill(140, 10, 10)).toEqual([100, 100]);
  });
});
