import { describe, expect, test } from "bun:test";
import { detectLyricsDirection, detectTextDirection, parseLrc } from "./SyncedLyrics";

describe("parseLrc word tags (#221)", () => {
  test("enhanced line parses words + end, display text stays clean", () => {
    const [line] = parseLrc("[00:10.00]<00:10.00>hello <00:10.50>world <00:11.00>");
    expect(line.t).toBe(10);
    expect(line.text).toBe("hello world"); // tags stripped for display
    expect(line.end).toBe(11);
    expect(line.words).toHaveLength(2);
    expect(line.words?.[0]).toEqual({ t: 10, d: 0.5, text: "hello" });
    const [, w1] = line.words!;
    expect(w1.t).toBe(10.5);
    expect(w1.text).toBe("world");
    expect(w1.d).toBeCloseTo(0.5); // last word span from the trailing end tag
  });

  test("no trailing end tag: last word duration is null (consumer-derived)", () => {
    const [line] = parseLrc("[00:05.00]<00:05.00>one <00:05.40>two");
    expect(line.end).toBeUndefined();
    expect(line.words).toHaveLength(2);
    expect(line.words?.[0].d).toBeCloseTo(0.4);
    expect(line.words?.[1]).toEqual({ t: 5.4, d: null, text: "two" });
  });

  test("single word, no end tag", () => {
    const [line] = parseLrc("[00:02.00]<00:02.00>hi");
    expect(line.text).toBe("hi");
    expect(line.words).toEqual([{ t: 2, d: null, text: "hi" }]);
    expect(line.end).toBeUndefined();
  });

  test("plain line keeps words/end undefined (per-line fallback)", () => {
    const [line] = parseLrc("[00:03.00] just plain text");
    expect(line.text).toBe("just plain text");
    expect(line.words).toBeUndefined();
    expect(line.end).toBeUndefined();
  });

  test("mixed file: enhanced + plain lines coexist per-line", () => {
    const lines = parseLrc(
      ["[00:01.00]<00:01.00>tick <00:01.50>tock <00:02.00>", "[00:03.00] plain again"].join("\n"),
    );
    expect(lines).toHaveLength(2);
    expect(lines[0].words).toHaveLength(2);
    expect(lines[0].end).toBe(2);
    expect(lines[1].words).toBeUndefined();
    expect(lines[1].text).toBe("plain again");
  });

  test("repeated leading tags share the same parsed words", () => {
    const lines = parseLrc("[00:01.00][00:09.00]<00:01.00>la <00:01.50>la <00:02.00>");
    expect(lines.map((l) => l.t)).toEqual([1, 9]);
    for (const l of lines) {
      expect(l.text).toBe("la la");
      expect(l.words).toHaveLength(2);
      expect(l.end).toBe(2);
    }
  });

  test("malformed enhanced lines degrade to plain (no words)", () => {
    // Text before the first word tag.
    const [lead] = parseLrc("[00:01.00]lead <00:01.20>word");
    expect(lead.text).toBe("lead word");
    expect(lead.words).toBeUndefined();
    // Empty word between two consecutive tags.
    const [empty] = parseLrc("[00:04.00]<00:04.00>a <00:04.50> <00:05.00>b");
    expect(empty.words).toBeUndefined();
    expect(empty.text).toBe("a  b");
  });
});

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


describe("parseLrc first-word line-start adjustment (#236)", () => {
  test("pulls a word-timed line's start up to its earlier first word", () => {
    const body = "[00:19.13]<00:18.92>When <00:19.12>you <00:19.26>were <00:20.68>";
    const [line] = parseLrc(body);
    expect(line.t).toBeCloseTo(18.92, 5);
    expect(line.text).toBe("When you were");
  });

  test("keeps the tag when the first word starts at/after it", () => {
    const body = "[00:10.00]<00:10.20>hello <00:11.00>world <00:12.00>";
    const [line] = parseLrc(body);
    expect(line.t).toBe(10);
  });

  test("ignores absurd drift (> 2 s) and keeps the curated tag", () => {
    const body = "[00:30.00]<00:20.00>way <00:21.00>off <00:22.00>";
    const [line] = parseLrc(body);
    expect(line.t).toBe(30);
  });

  test("clamps the adjustment to the previous line's start (monotonic)", () => {
    const body = [
      "[00:10.00]plain first line",
      "[00:10.10]<00:09.50>early <00:11.00>word <00:12.00>",
    ].join("\n");
    const [a, b] = parseLrc(body);
    expect(a.t).toBe(10);
    expect(b.t).toBe(10); // clamped to a.t, not 9.5
  });

  test("leaves word-less lines untouched", () => {
    const [line] = parseLrc("[00:05.00]no words here");
    expect(line.t).toBe(5);
  });
});
