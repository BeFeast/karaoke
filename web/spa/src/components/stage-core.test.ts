import { describe, expect, test } from "bun:test";
import { type LyricLine, parseLrc } from "./SyncedLyrics";
import { lyricState, type TimedLine, timeLines, wordSungFraction } from "./stage-core";

// ── timeLines: duration derivation + word resolution ────────────────────────
describe("timeLines", () => {
  const plain = (t: number, text: string): LyricLine => ({ t, text });

  test("word-less lines keep today's derivation (interval, capped)", () => {
    const timed = timeLines([plain(0, "a"), plain(3, "b")], null);
    expect(timed[0]).toEqual({ t: 0, d: 3, text: "a", words: undefined, end: undefined });
    // Last line with no duration falls back to BREAK_LINE_S (5).
    expect(timed[1].d).toBe(5);
  });

  test("an over-long interval reads as a break (BREAK_LINE_S)", () => {
    const timed = timeLines([plain(0, "a"), plain(20, "b")], null);
    expect(timed[0].d).toBe(5);
  });

  test("last line uses the track duration when known, capped", () => {
    expect(timeLines([plain(0, "a")], 4)[0].d).toBe(4);
    expect(timeLines([plain(0, "a")], 40)[0].d).toBe(5); // capped as a break
  });

  test("a line with `end` sings until end, never past the next line", () => {
    const withEnd: LyricLine = { t: 0, text: "a", end: 2, words: [{ t: 0, d: 2, text: "a" }] };
    // interval (10) > end-t (2) -> d = 2
    expect(timeLines([withEnd, plain(10, "b")], null)[0].d).toBe(2);
    // end beyond the next line is capped by the interval
    const longEnd: LyricLine = { t: 0, text: "a", end: 8, words: [{ t: 0, d: 8, text: "a" }] };
    expect(timeLines([longEnd, plain(3, "b")], null)[0].d).toBe(3);
  });

  test("word spans are capped at the next line's start", () => {
    // The end tag (8) lands after the next line starts (3): the last word's
    // span is cut at 3 so the wipe completes before the line hands over.
    const line: LyricLine = {
      t: 0,
      text: "a b",
      end: 8,
      words: [
        { t: 0, d: 1, text: "a" },
        { t: 1, d: 7, text: "b" }, // tag-derived span reaching past next.t
      ],
    };
    const timed = timeLines([line, plain(3, "c")], null);
    expect(timed[0].words).toEqual([
      { t: 0, d: 1, text: "a" },
      { t: 1, d: 2, text: "b" }, // capped: 3 (next line) - 1
    ]);
  });

  test("resolves the last word's null duration from the next line start", () => {
    const line: LyricLine = {
      t: 0,
      text: "a b",
      words: [
        { t: 0, d: 0.5, text: "a" },
        { t: 0.5, d: null, text: "b" }, // no end tag -> derive from next line
      ],
    };
    const timed = timeLines([line, plain(2, "c")], null);
    expect(timed[0].words).toEqual([
      { t: 0, d: 0.5, text: "a" },
      { t: 0.5, d: 1.5, text: "b" }, // 2 (next line) - 0.5
    ]);
  });
});

// ── wordSungFraction: the piecewise character-weighted wipe ──────────────────
describe("wordSungFraction", () => {
  // "aa" [1,2), gap, "bbb" [3,4). total chars = 2 + 1(sep) + 3 = 6.
  const words = [
    { t: 1, d: 1, text: "aa" },
    { t: 3, d: 1, text: "bbb" },
  ];

  test("before the first word: fill is 0", () => {
    expect(wordSungFraction(words, 0)).toBe(0);
    expect(wordSungFraction(words, 1)).toBe(0); // exactly at word 0 start
  });

  test("the wipe reaches each word exactly at its start `t`", () => {
    // At word 1's start the fill sits at its left char boundary: 3/6 chars
    // (word 0 "aa" + its separator) already filled.
    expect(wordSungFraction(words, 3)).toBeCloseTo(0.5);
  });

  test("intra-line gap: fill holds at the last word's boundary", () => {
    const atBoundary = wordSungFraction(words, 2); // word 0 just finished
    expect(atBoundary).toBeCloseTo(0.5);
    expect(wordSungFraction(words, 2.4)).toBeCloseTo(0.5); // held
    expect(wordSungFraction(words, 2.9)).toBeCloseTo(0.5); // still held
  });

  test("mid-word fill is linear over that word at its own pace", () => {
    expect(wordSungFraction(words, 1.5)).toBeCloseTo((2 * 0.5) / 6); // word 0 half
    expect(wordSungFraction(words, 3.5)).toBeCloseTo((3 + 3 * 0.5) / 6); // word 1 half
  });

  test("after the last word: fully sung", () => {
    expect(wordSungFraction(words, 4)).toBe(1);
    expect(wordSungFraction(words, 99)).toBe(1);
  });

  test("monotonic non-decreasing across the line", () => {
    let prev = 0;
    for (let pos = 0; pos <= 5; pos += 0.05) {
      const cur = wordSungFraction(words, pos);
      expect(cur).toBeGreaterThanOrEqual(prev - 1e-9);
      prev = cur;
    }
  });

  test("a zero-span word fills instantly, never divides by zero", () => {
    const z = [
      { t: 0, d: 0, text: "x" },
      { t: 1, d: 1, text: "y" },
    ];
    expect(wordSungFraction(z, 0)).toBeCloseTo(2 / 3); // "x" + sep of total 1+1+1
    expect(Number.isFinite(wordSungFraction(z, 0))).toBe(true);
  });
});

// ── lyricState: word-timed vs word-less lines ───────────────────────────────
describe("lyricState sung%", () => {
  test("word-less lines keep the linear line wipe (unchanged)", () => {
    const lines: TimedLine[] = [{ t: 0, d: 4, text: "plain" }];
    expect(lyricState(lines, 0).sung).toBe(0);
    expect(lyricState(lines, 2).sung).toBe(50);
    expect(lyricState(lines, 4).sung).toBe(100);
    expect(lyricState(lines, 10).sung).toBe(100); // clamped
  });

  test("word-timed lines use the piecewise fraction (×100)", () => {
    const timed = timeLines(
      [
        {
          t: 1,
          text: "aa bbb",
          end: 4,
          words: [
            { t: 1, d: 1, text: "aa" },
            { t: 3, d: 1, text: "bbb" },
          ],
        },
      ],
      null,
    );
    expect(lyricState(timed, 3).sung).toBeCloseTo(50); // reaches word 2 at its t
    expect(lyricState(timed, 2.5).sung).toBeCloseTo(50); // gap hold
  });

  test("inGap flips at the sung end of a word-timed line", () => {
    const timed = timeLines(
      [
        { t: 0, text: "a b", end: 2, words: [{ t: 0, d: 1, text: "a" }, { t: 1, d: 1, text: "b" }] },
        { t: 5, text: "c" },
      ],
      null,
    );
    expect(lyricState(timed, 1.5).inGap).toBe(false); // still singing "b"
    expect(lyricState(timed, 3).inGap).toBe(true); // past end (2), in the gap
  });
});

// ── RTL / Cyrillic: math is logical-order, visual start is CSS (#215) ────────
describe("word wipe respects script without special-casing (#166) (#215)", () => {
  const reaches = (line: LyricLine) => {
    const [timed] = timeLines([line], null);
    // Fill lands on each word's left boundary exactly at its start time.
    let cumChars = 0;
    const total = timed.words!.reduce((n, w, i) => n + w.text.length + (i > 0 ? 1 : 0), 0);
    for (let i = 0; i < timed.words!.length; i++) {
      const frac = lyricState([timed], timed.words![i].t).sung / 100;
      expect(frac).toBeCloseTo(cumChars / total);
      cumChars += timed.words![i].text.length + 1;
    }
  };

  test("Hebrew (RTL) line reaches each word at its start", () => {
    // parse a real enhanced-LRC Hebrew line end-to-end.
    const [line] = parseLrc("[00:00.00]<00:00.00>שלום <00:01.00>עולם <00:02.00>");
    expect(line.words).toHaveLength(2);
    reaches(line);
  });

  test("Arabic (RTL) line reaches each word at its start", () => {
    const [line] = parseLrc("[00:00.00]<00:00.00>مرحبا <00:01.00>بالعالم <00:02.00>");
    reaches(line);
  });

  test("Russian (Cyrillic, LTR) line reaches each word at its start", () => {
    const [line] = parseLrc("[00:00.00]<00:00.00>привет <00:01.00>мир <00:02.00>");
    reaches(line);
  });

  test("word wipe is monotonic for an RTL line", () => {
    const [line] = parseLrc("[00:00.00]<00:00.00>שלום <00:01.00>עולם <00:02.00>");
    const [timed] = timeLines([line], null);
    let prev = -1;
    for (let pos = 0; pos <= 2.5; pos += 0.05) {
      const sung = lyricState([timed], pos).sung;
      expect(sung).toBeGreaterThanOrEqual(prev - 1e-6);
      prev = sung;
    }
  });
});
