// Unit tests for the receipt failure formatter (issue #177): the popup shows
// one compact line instead of the raw multi-line PipelineError dump. Run with:
// bun test  (from extension/chrome)

import { describe, expect, test } from "bun:test";
import { compactErrorLine, extractorReceiptLabel, failedReceiptLine } from "./receipt.js";

// The real shape of `job.error` (worker pipeline.py _run): a one-line summary
// followed by the captured stderr tail.
const DUMP =
  "command failed (1): yt-dlp -x https://karaoke.oklabs.uk/app/#/settings\n" +
  "stderr:\n" +
  "ERROR: Unsupported URL: https://karaoke.oklabs.uk/app/#/settings\n" +
  "  File \"yt_dlp/extractor/common.py\", line 1234, in extract";

describe("compactErrorLine", () => {
  test("keeps only the first line of a multi-line dump", () => {
    expect(compactErrorLine(DUMP)).toBe(
      "command failed (1): yt-dlp -x https://karaoke.oklabs.uk/app/#/settings",
    );
  });

  test("skips leading blank/whitespace lines and trims", () => {
    expect(compactErrorLine("\n  \n  boom happened  \nrest")).toBe("boom happened");
  });

  test("handles CRLF dumps", () => {
    expect(compactErrorLine("first\r\nsecond")).toBe("first");
  });

  test("caps overlong lines with an ellipsis", () => {
    const line = compactErrorLine(`${"x".repeat(300)}\nrest`);
    expect(line.length).toBe(140);
    expect(line.endsWith("…")).toBe(true);
  });

  test("a line exactly at the cap is untouched", () => {
    const exact = "y".repeat(140);
    expect(compactErrorLine(exact)).toBe(exact);
  });

  test("empty/undefined input yields the empty string", () => {
    expect(compactErrorLine("")).toBe("");
    expect(compactErrorLine(undefined)).toBe("");
    expect(compactErrorLine("\n\n  \n")).toBe("");
  });
});

describe("failedReceiptLine", () => {
  test("prefers the curated stage_note over the raw dump", () => {
    const job = { status: "failed", stage_note: "yt-dlp: Unsupported URL", error: DUMP };
    expect(failedReceiptLine(job)).toBe("yt-dlp: Unsupported URL");
  });

  test("falls back to the first line of the error dump", () => {
    const job = { status: "failed", stage_note: null, error: DUMP };
    expect(failedReceiptLine(job)).toBe(
      "command failed (1): yt-dlp -x https://karaoke.oklabs.uk/app/#/settings",
    );
  });

  test("never echoes the status word when there is nothing to show", () => {
    expect(failedReceiptLine({ status: "failed" })).toBe("");
    expect(failedReceiptLine({ status: "failed", stage_note: "  ", error: "" })).toBe("");
  });

  test("compacts an overlong stage_note too", () => {
    const job = { stage_note: "z".repeat(300) };
    expect(failedReceiptLine(job).length).toBe(140);
    expect(failedReceiptLine(job).endsWith("…")).toBe(true);
  });
});

describe("extractorReceiptLabel", () => {
  test("capitalizes the lowercase IE_NAME and adds the tick (#181)", () => {
    expect(extractorReceiptLabel("youtube")).toBe("Youtube ✓");
    expect(extractorReceiptLabel("soundcloud")).toBe("Soundcloud ✓");
  });

  test("already-cased names keep their casing", () => {
    expect(extractorReceiptLabel("BiliBili")).toBe("BiliBili ✓");
  });

  test("no extractor → empty label (Submit-anyway receipts show no tick)", () => {
    expect(extractorReceiptLabel(null)).toBe("");
    expect(extractorReceiptLabel("")).toBe("");
    expect(extractorReceiptLabel("   ")).toBe("");
    expect(extractorReceiptLabel(undefined)).toBe("");
  });
});
