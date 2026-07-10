import { describe, expect, test } from "bun:test";
import { detectLyricsDirection, detectTextDirection, parseLrc } from "./SyncedLyrics";

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

describe("detectTextDirection", () => {
  test("resolves a single line from its first strong character", () => {
    expect(detectTextDirection("שלום עולם")).toBe("rtl");
    expect(detectTextDirection("مرحبا بالعالم")).toBe("rtl");
    expect(detectTextDirection("Hello world")).toBe("ltr");
    expect(detectTextDirection("123 שלום")).toBe("rtl");
    expect(detectTextDirection("(intro) Hello שלום")).toBe("ltr");
  });

  test("returns auto for empty or strong-less lines", () => {
    expect(detectTextDirection("")).toBe("auto");
    expect(detectTextDirection("1234 !!!")).toBe("auto");
  });

  test("resolves each line of a mixed-script block independently (#214)", () => {
    // Regression: an English intro line must not force the later Hebrew/Arabic
    // lines to LTR — the plain fallbacks render one dir-scoped block per line.
    const block = "Intro line\nשורה בעברית\nسطر عربي";
    expect(block.split(/\r?\n/).map(detectTextDirection)).toEqual(["ltr", "rtl", "rtl"]);
  });
});
