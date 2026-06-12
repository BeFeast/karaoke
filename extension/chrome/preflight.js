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
const CONFIRM_UNAVAILABLE_MESSAGE =
  "couldn't check this link with the booth — it may not be a video page";
const REFUSE_UNSUPPORTED_MESSAGE =
  "yt-dlp has no extractor for this page — nothing to submit";

// The toolbar-click decision (#181), pure. `preflightResult` is the parsed
// GET /preflight body ({supported, extractor, generic_only}) or null when the
// check failed/timed out. In priority order:
// * local hard-refusals — non-http(s) scheme or the booth's own host →
//   "refuse" (mirrors guard.js submitRefusal, which supplies the messages;
//   these never depend on the server's answer);
// * no preflight verdict (error/timeout) → "confirm" — never hard-block a
//   submit on infrastructure;
// * `supported` (a dedicated extractor claims the URL) → "submit";
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
    return "submit";
  }
  return preflightResult.generic_only ? "confirm" : "refuse";
}

// GET /preflight?url=… with a hard AbortController timeout. Resolves to the
// parsed verdict ({supported, extractor, generic_only}) or null on ANY
// failure — timeout, network error, non-2xx, unparseable body — so the caller
// falls back to the confirm state (#181). `fetchImpl` is injectable for tests
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
  CONFIRM_UNAVAILABLE_MESSAGE,
  REFUSE_UNSUPPORTED_MESSAGE,
  classifySubmit,
  fetchPreflight,
};
