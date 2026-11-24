import { describe, expect, test } from "bun:test";
import { snapToLine } from "./StageDock";
import { timeLines } from "./stage-core";
import { anchorOffset, anchorScrollTop, cueCountdown, fitFontPx, lineRoles, lyricCues } from "./stageLyricsModel";
import type { LyricLine } from "./SyncedLyrics";

const lines: LyricLine[] = [
  { t: 6, text: "one" },
  { t: 9, text: "two" },
  { t: 11, text: "spoken", speech: true },
  { t: 12, text: "three" },
  { t: 40, text: "four" },
];

describe("lineRoles", () => {
  test("before the first line the first sung line is next", () => {
    expect(lineRoles(lines, -1)).toEqual(["next", "ahead", "speech", "ahead", "ahead"]);
  });
  test("next skips speech lines", () => {
    expect(lineRoles(lines, 1)).toEqual(["past", "active", "speech", "next", "ahead"]);
  });
});

describe("lyricCues", () => {
  const cues = lyricCues(lines, timeLines(lines, 60));
  test("intro before the first line and an instrumental break of 8 s or more", () => {
    expect(cues.map((cue) => [cue.kind, cue.before])).toEqual([["intro", 0], ["instrumental", 4]]);
    expect(cues[0]).toMatchObject({ start: 0, end: 6 });
    expect(cues[1].end).toBe(40);
  });
  test("count-in lights the dots over the last 3 s", () => {
    expect(cueCountdown(cues[0], 1).lit).toBe(0);
    expect(cueCountdown(cues[0], 3.5).lit).toBe(1);
    expect(cueCountdown(cues[0], 4.5).lit).toBe(2);
    expect(cueCountdown(cues[0], 5.5).lit).toBe(3);
    expect(cueCountdown(cues[0], 6).active).toBe(false);
  });
});

describe("follow anchor", () => {
  test("puts the line top at 28% of the viewport, within scroll bounds", () => {
    expect(anchorScrollTop(500, 500, 2000)).toBe(360);
    expect(anchorScrollTop(50, 500, 2000)).toBe(0);
    expect(anchorScrollTop(3000, 500, 2000)).toBe(2000);
  });
});

describe("snapToLine", () => {
  test("snaps within 0.6 s of a line start", () => {
    expect(snapToLine(9.4, lines)).toBe(9);
    expect(snapToLine(11.7, lines)).toBe(12);
    expect(snapToLine(25, lines)).toBe(25);
  });
});

describe("long lines", () => {
  test("a line too tall for the anchor moves up to stay whole", () => {
    expect(anchorOffset(500, 100)).toBe(140);
    expect(anchorOffset(500, 400)).toBe(84);
    expect(anchorOffset(500, 600)).toBe(8);
  });
  test("fit shrinks the font but never below 20 px", () => {
    expect(fitFontPx(28, 300, 400)).toBe(28);
    expect(fitFontPx(28, 544, 390)).toBe(23);
    expect(fitFontPx(28, 2000, 390)).toBe(20);
  });
});
