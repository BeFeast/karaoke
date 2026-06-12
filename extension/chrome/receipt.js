// Pure formatting helpers for the popup receipt card (issue #177).
//
// `chrome.*`-free so they unit-test under `bun test` (see receipt.test.js).
// The popup imports `failedReceiptLine` to render a failed job as one compact
// line instead of the raw multi-line PipelineError dump; the full dump goes
// behind a `title` tooltip, never inline.

// Cap for the one-line summary — long enough for "command failed (1): yt-dlp
// …" to stay meaningful, short enough to never wrap the receipt card.
const LINE_LIMIT = 140;

// First non-empty line of a (possibly multi-line) error dump, trimmed and
// length-capped with an ellipsis.
function compactErrorLine(text, limit = LINE_LIMIT) {
  const line =
    String(text || "")
      .split(/\r?\n/)
      .map((part) => part.trim())
      .find((part) => part) || "";
  if (line.length <= limit) {
    return line;
  }
  return `${line.slice(0, limit - 1)}…`;
}

// One-line failure summary for a job: the curated `stage_note` when the server
// set one (the short operator/UI hint, #172), else the first line of the raw
// `error` dump, else "". The status word itself is NOT part of the line — the
// receipt chip already says "failed", and repeating it was the #177 bug.
function failedReceiptLine(job) {
  const note = String(job?.stage_note || "").trim();
  if (note) {
    return compactErrorLine(note);
  }
  return compactErrorLine(job?.error);
}

// "Youtube ✓" — the matched yt-dlp extractor on the receipt meta line (#181).
// IE_NAMEs come lowercase ("youtube", "soundcloud"); the sign capitalizes the
// first letter. Empty when no extractor is known — a Submit-anyway receipt
// after a generic-only or unavailable preflight shows no extractor tick.
function extractorReceiptLabel(extractor) {
  const name = String(extractor || "").trim();
  if (!name) {
    return "";
  }
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} ✓`;
}

export { compactErrorLine, extractorReceiptLabel, failedReceiptLine };
