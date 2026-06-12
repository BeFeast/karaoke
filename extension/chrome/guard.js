// Submit policy for the Karaoke Submitter extension (issue #177).
//
// Pure and free of any `chrome.*` reference so it unit-tests under `bun test`
// (see guard.test.js). The service worker imports MENU_SPEC to register the
// context menu and `submitRefusal` to gate every submit path (toolbar popup
// and context menu) before a job is minted.

// Right-click on the toolbar icon (#181): a shortcut straight to the booth
// dashboard at `<base>/app/`. The popup footer keeps its "open the booth →"
// link; this is the one entry on the action icon's own context menu.
const OPEN_BOOTH_MENU_ID = "open-booth";

// Context-menu registry — exactly one registration per distinct action. The
// old page+link pair both rendered on a YouTube video link, so the menu showed
// two entries for the same submit (#177). One item with merged contexts covers
// both right-click targets; the click handler prefers `info.linkUrl` over
// `info.pageUrl`.
const MENU_SPEC = [
  {
    id: "submit-video",
    title: "Submit video to Karaoke",
    contexts: ["page", "link"],
  },
  {
    id: OPEN_BOOTH_MENU_ID,
    title: "Open Karaoke booth",
    contexts: ["action"],
  },
];

// Why a URL must not be submitted, or null when it may. Refuses (a) non-http(s)
// schemes (chrome://, about:, file:, …) and (b) the configured booth itself —
// submitting the app's own pages mints a doomed yt-dlp job (#177, job #79).
// `baseUrl` is the configured Karaoke base URL; a missing/garbled base only
// disables the self-submit check, never the scheme check.
function submitRefusal(url, baseUrl) {
  let parsed;
  try {
    parsed = new URL(String(url || ""));
  } catch {
    return {
      reason: "not-http",
      message: "only http(s) pages and links can be submitted — open a video page and try again",
    };
  }

  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    return {
      reason: "not-http",
      message: "only http(s) pages and links can be submitted — open a video page and try again",
    };
  }

  let baseHost = "";
  try {
    baseHost = new URL(String(baseUrl || "")).host;
  } catch {
    baseHost = "";
  }
  if (baseHost && parsed.host === baseHost) {
    return {
      reason: "own-booth",
      message: "that's the karaoke booth itself — it can't be submitted as a job",
    };
  }

  return null;
}

export { MENU_SPEC, OPEN_BOOTH_MENU_ID, submitRefusal };
