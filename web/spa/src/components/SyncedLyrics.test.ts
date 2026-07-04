import { describe, expect, test } from "bun:test";
import { detectLyricsDirection, parseLrc } from "./SyncedLyrics";

describe("detectLyricsDirection", () => {
  test("returns rtl for Hebrew lyrics after neutral characters", () => {
    const lines = parseLrc("[00:01.00] 123 שלום עולם");

    expect(detectLyricsDirection(lines)).toBe("rtl");
  });

  test("returns rtl for Arabic lyrics", () => {
    const lines = parseLrc("[00:01.00] مرحبا بالعالم");

    expect(detectLyricsDirection(lines)).toBe("rtl");
  });

  test("uses the first strong character across mixed-script lyrics", () => {
    expect(detectLyricsDirection(parseLrc("[00:01.00] Hello שלום"))).toBe("ltr");
    expect(detectLyricsDirection(parseLrc("[00:01.00] שלום Hello"))).toBe("rtl");
  });

  test("falls back to browser auto direction when no strong script is found", () => {
    const lines = parseLrc("[00:01.00] 1234 !!!");

    expect(detectLyricsDirection(lines)).toBe("auto");
  });
});
