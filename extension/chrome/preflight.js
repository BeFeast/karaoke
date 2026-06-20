// Preflight-driven submit decision for the toolbar click (issue #181).
//
// Pure and free of any `chrome.*` reference so it unit-tests under `bun test`
// (see preflight.test.js). The service worker calls `fetchPreflight` against
// the booth's GET /preflight (offline yt-dlp extractor matching, #180) and
// feeds the verdict to `classifySubmit`, the single decision function for the
// toolbar submit path.

// Hard cap on the preflight round-trip: past this the extension stops waiting
// and falls back to the Submit-anyway confirm state. The preflight is a
// courtesy check, never an infrastructure hard-block (#181).
const PREFLIGHT_TIMEOUT_MS = 2000;

// Popup copy for the non-submit verdicts. The generic-extractor line is the
// operator-locked wording from #181, verbatim.
const CONFIRM_GENERIC_MESSAGE =
  "yt-dlp знает эту страницу только через generic extractor — не похоже на видео-ссылку";
// A dedicated extractor matched, but it returns a feed / playlist / channel /
// search container, not a single track (#192). Same confirm + Submit-anyway
// escape hatch as the generic case, never a silent auto-submit of a non-track.
const CONFIRM_CONTAINER_MESSAGE =
  "yt-dlp видит здесь ленту, плейлист или канал, а не отдельное видео";
const CONFIRM_UNAVAILABLE_MESSAGE =
  "couldn't check this link with the booth — it may not be a video page";
const REFUSE_UNSUPPORTED_MESSAGE =
  "yt-dlp has no extractor for this page — nothing to submit";

// The toolbar-click decision (#181, #192), pure. `preflightResult` is the
// parsed GET /preflight body ({supported, extractor, generic_only,
// single_media}) or null when the check failed/timed out. In priority order:
// * local hard-refusals — non-http(s) scheme or the booth's own host →
//   "refuse" (mirrors guard.js submitRefusal, which supplies the messages;
//   these never depend on the server's answer);
// * no preflight verdict (error/timeout) → "confirm" — never hard-block a
//   submit on infrastructure;
// * `supported && single_media` (a confident single-track extractor) →
//   "submit" — the only auto-submit path;
// * `supported && !single_media` (a dedicated extractor, but a feed / playlist
//   / channel / search container) → "confirm" (#192) — fail-safe to one extra
//   click rather than silently mint a non-track job;
// * `generic_only` (yt-dlp would only guess via Generic) → "confirm";
// * neither → "refuse" — not even Generic wants it.
function classifySubmit(url, baseHost, preflightResult) {
  let parsed;
  try {
    parsed = new URL(String(url || ""));
  } catch {
    return "refuse";
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return "refuse";
  }
  if (baseHost && parsed.host === baseHost) {
    return "refuse";
  }
  if (!preflightResult) {
    return "confirm";
  }
  if (preflightResult.supported) {
    return preflightResult.single_media ? "submit" : "confirm";
  }
  return preflightResult.generic_only ? "confirm" : "refuse";
}

// GET /preflight?url=… with a hard AbortController timeout. Resolves to the
// parsed verdict ({supported, extractor, generic_only, single_media}) or null
// on ANY failure — timeout, network error, non-2xx, unparseable body — so the
// caller falls back to the confirm state (#181). `single_media` defaults to
// false when the field is absent, so an older booth degrades to confirm,
// never to a silent auto-submit (#192). `fetchImpl` is injectable for tests
// and defaults to the global fetch.
async function fetchPreflight(
  baseUrl,
  url,
  { headers = {}, timeoutMs = PREFLIGHT_TIMEOUT_MS, fetchImpl } = {},
) {
  const doFetch = fetchImpl || ((...args) => fetch(...args));
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await doFetch(
      `${baseUrl}/preflight?url=${encodeURIComponent(String(url || ""))}`,
      { headers, signal: controller.signal },
    );
    if (!response.ok) {
      return null;
    }
    const body = await response.json();
    return {
      supported: Boolean(body?.supported),
      extractor: body?.extractor ?? null,
      generic_only: Boolean(body?.generic_only),
      single_media: Boolean(body?.single_media),
    };
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export {
  PREFLIGHT_TIMEOUT_MS,
  CONFIRM_GENERIC_MESSAGE,
  CONFIRM_CONTAINER_MESSAGE,
  CONFIRM_UNAVAILABLE_MESSAGE,
  REFUSE_UNSUPPORTED_MESSAGE,
  classifySubmit,
  fetchPreflight,
};
