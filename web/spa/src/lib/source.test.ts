// Unit tests for the source_url display helpers (#173, #184).
// Runs under `bun test src` (excluded from tsc — see tsconfig "exclude").

import { describe, expect, test } from "bun:test";
import { compactUrlLabel, sourceDisplay } from "./source";

describe("sourceDisplay", () => {
  test("upload:// sentinel → kind upload, prefix-stripped filename", () => {
    expect(sourceDisplay("upload://song.mp3")).toEqual({ kind: "upload", label: "song.mp3" });
    expect(sourceDisplay("upload://Never Gonna Give You Up.m4a")).toEqual({
      kind: "upload",
      label: "Never Gonna Give You Up.m4a",
    });
  });

  test("percent-encoded names stay verbatim — the server stores them raw", () => {
    expect(sourceDisplay("upload://Never%20Gonna.mp3")).toEqual({
      kind: "upload",
      label: "Never%20Gonna.mp3",
    });
  });

  test("http(s) URLs → kind url, label verbatim", () => {
    expect(sourceDisplay("https://youtu.be/x")).toEqual({ kind: "url", label: "https://youtu.be/x" });
    expect(sourceDisplay("http://example.com/watch?v=1")).toEqual({
      kind: "url",
      label: "http://example.com/watch?v=1",
    });
  });

  test("empty / garbage → safe label, no throw", () => {
    expect(sourceDisplay("")).toEqual({ kind: "url", label: "" });
    expect(sourceDisplay("not a url at all")).toEqual({ kind: "url", label: "not a url at all" });
    expect(sourceDisplay("upload://")).toEqual({ kind: "upload", label: "uploaded audio" });
    expect(sourceDisplay("upload://   ")).toEqual({ kind: "upload", label: "uploaded audio" });
    // non-string garbage (API drift) must not throw either
    expect(sourceDisplay(undefined as unknown as string)).toEqual({ kind: "url", label: "" });
    expect(sourceDisplay(null as unknown as string)).toEqual({ kind: "url", label: "" });
  });

  test("prefix match is case-sensitive — the server always writes lowercase", () => {
    expect(sourceDisplay("UPLOAD://x.mp3").kind).toBe("url");
  });
});

describe("compactUrlLabel", () => {
  test("plain YouTube watch URL — scheme stripped, short enough to stay whole", () => {
    expect(compactUrlLabel("https://www.youtube.com/watch?v=dQw4w9WgXcQ")).toBe(
      "youtube.com/watch?v=dQw4w9WgXcQ",
    );
  });

  test(">56-char URL truncates to 56 chars plus a trailing ellipsis", () => {
    const raw =
      "https://music.example.com/some/very/long/path/segments?with=query&params=that&overflow=the+cap";
    const label = compactUrlLabel(raw);
    expect(label.endsWith("…")).toBe(true);
    expect(label).toHaveLength(57); // 56 visible chars + "…"
    expect(label).toBe(`${raw.replace("https://", "").slice(0, 56)}…`);
  });

  test("http:// and leading www. are both stripped", () => {
    expect(compactUrlLabel("http://www.example.com/watch?v=1")).toBe("example.com/watch?v=1");
  });

  test("non-URL string passes through untouched", () => {
    expect(compactUrlLabel("not a url at all")).toBe("not a url at all");
    expect(compactUrlLabel("")).toBe("");
  });

  test("custom max respected", () => {
    expect(compactUrlLabel("https://youtu.be/dQw4w9WgXcQ", 8)).toBe("youtu.be…");
    expect(compactUrlLabel("https://youtu.be/x", 200)).toBe("youtu.be/x");
  });
});
