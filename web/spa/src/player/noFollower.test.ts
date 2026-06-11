// Regression guard for #113: the two-<audio>-element follower architecture
// (per-frame drift correction of a second media element) caused audible
// glitching and must not creep back through a "compiles fine" path. The
// engine is single-clock Web Audio — buffers + co-started sources — so the
// player sources must never construct media elements or follower plumbing.

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (name: string) => readFileSync(new URL(name, import.meta.url), "utf8");

describe("no follower architecture in the player (#113)", () => {
  test("useKaraokePlayer.ts has no media elements or drift-sync apparatus", () => {
    const src = read("./useKaraokePlayer.ts");
    for (const token of ["syncVocal", "createMediaElementSource", "HARD_DRIFT_S", "new Audio(", "timeupdate"]) {
      expect(src.includes(token)).toBe(false);
    }
  });

  test("stemEngine.ts plays buffers, not media elements", () => {
    const src = read("./stemEngine.ts");
    for (const token of ["new Audio(", "createMediaElementSource"]) {
      expect(src.includes(token)).toBe(false);
    }
  });
});
