import { buildJobBody } from "./cookies.js";

const DEFAULT_BASE_URL = "https://karaoke.oklabs.uk";
const SOURCE = "chrome-extension";
const NOTIFICATION_LINKS_KEY = "notificationLinks";
const NOTIFICATION_ICON = "icons/karaoke-128.png";
const CLEAR_BADGE_ALARM = "clear-karaoke-badge";

// Domains whose cookies make up a logged-in YouTube session. youtube.com is the
// primary jar; google.com carries the shared Google account auth cookies
// (SAPISID/__Secure-3PSID/…) that strengthen yt-dlp's logged-in requests. The
// merged jar is attached to each submit as `youtube_cookies` (issue #77).
const COOKIE_DOMAINS = ["youtube.com", "google.com"];

// Obsolete state from the central-jar rotation model (issues #73/#74), now
// replaced by per-job ephemeral cookies (#77). Cleared on install/startup so
// upgraded installs stop firing the 6h alarm and drop the stale sync record.
const LEGACY_REFRESH_ALARM = "refresh-youtube-cookies";
const LEGACY_SYNC_STATE_KEY = "youtubeCookieSync";

const HTTP_URL = /^https?:\/\//i;

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "submit-page",
      title: "Submit this video page to Karaoke",
      contexts: ["page"],
    });

    chrome.contextMenus.create({
      id: "submit-link",
      title: "Submit video link to Karaoke",
      contexts: ["link"],
    });
  });

  cleanupLegacyCookieSync();
});

chrome.runtime.onStartup.addListener(() => {
  cleanupLegacyCookieSync();
});

chrome.action.onClicked.addListener(async (tab) => {
  if (!isSubmittableUrl(tab.url || "")) {
    await notifyFailure("Open an http(s) video page before using the toolbar action.");
    return;
  }

  await submitToKaraoke(tab.url);
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.pageUrl || tab?.url || "";
  if (!isSubmittableUrl(url)) {
    await notifyFailure("Use this menu on an http(s) video page or link.");
    return;
  }

  await submitToKaraoke(url);
});

chrome.notifications.onClicked.addListener(async (notificationId) => {
  const links = await getNotificationLinks();
  const url = links[notificationId];
  if (!url) {
    return;
  }

  await chrome.tabs.create({ url });
  delete links[notificationId];
  await chrome.storage.local.set({ [NOTIFICATION_LINKS_KEY]: links });
  chrome.notifications.clear(notificationId);
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === CLEAR_BADGE_ALARM) {
    chrome.action.setBadgeText({ text: "" });
    return;
  }
  if (alarm.name === LEGACY_REFRESH_ALARM) {
    // Defensive: an upgraded install may still have the old periodic alarm
    // queued before cleanup ran. Drop it instead of acting on it.
    chrome.alarms.clear(LEGACY_REFRESH_ALARM);
  }
});

async function submitToKaraoke(url) {
  setBadge("...", "#5b6472");

  try {
    const config = await getConfig();
    await ensureHostPermission(config.baseUrl);
    // Read the user's logged-in YouTube/Google cookies at submit time and send
    // them with THIS job only (issue #77). No central jar, no always-on sync —
    // the cookies ride the request and the server never persists them.
    const cookies = await collectSessionCookies();
    const result = await createJob(config, url, cookies);
    await notifySuccess(config.baseUrl, result);
    setBadge("OK", "#137333");
  } catch (error) {
    await notifyFailure(error.message || String(error));
    setBadge("ERR", "#b3261e");
  }
}

function isSubmittableUrl(url) {
  return HTTP_URL.test(String(url || ""));
}

// Drop the central-jar rotation alarm + stored sync state left by #73/#74. The
// per-job model (#77) needs neither. Best-effort and idempotent.
function cleanupLegacyCookieSync() {
  try {
    chrome.alarms.clear(LEGACY_REFRESH_ALARM);
    chrome.storage.local.remove(LEGACY_SYNC_STATE_KEY);
  } catch {
    // Ignore — cleanup is best-effort.
  }
}

async function getConfig() {
  const stored = await chrome.storage.sync.get({
    baseUrl: DEFAULT_BASE_URL,
    bearerToken: "",
  });

  return {
    baseUrl: normalizeBaseUrl(stored.baseUrl),
    bearerToken: String(stored.bearerToken || "").trim(),
  };
}

function normalizeBaseUrl(value) {
  const trimmed = String(value || DEFAULT_BASE_URL).trim().replace(/\/+$/, "");
  const parsed = new URL(trimmed);
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("Karaoke base URL must start with http:// or https://.");
  }
  return parsed.origin + parsed.pathname.replace(/\/+$/, "");
}

async function ensureHostPermission(baseUrl) {
  const originPattern = `${new URL(baseUrl).origin}/*`;
  const hasPermission = await chrome.permissions.contains({ origins: [originPattern] });
  if (hasPermission) {
    return;
  }

  throw new Error(
    `Chrome has not granted access to ${new URL(baseUrl).origin}. Open extension settings and save the Karaoke base URL.`,
  );
}

async function createJob(config, url, cookies) {
  const headers = {
    "Content-Type": "application/json",
  };
  if (config.bearerToken) {
    headers.Authorization = `Bearer ${config.bearerToken}`;
  }

  // buildJobBody attaches `youtube_cookies` only when youtube.com cookies are
  // present; otherwise the field is omitted and the server does a public fetch.
  const body = buildJobBody({ url, source: SOURCE, cookies });

  let response;
  try {
    response = await fetch(`${config.baseUrl}/jobs`, {
      method: "POST",
      headers,
      body: JSON.stringify(body),
    });
  } catch (error) {
    throw new Error(`Could not reach Karaoke at ${config.baseUrl}: ${error.message}`);
  }

  let parsed = null;
  const text = await response.text();
  if (text) {
    try {
      parsed = JSON.parse(text);
    } catch {
      parsed = { detail: text };
    }
  }

  if (!response.ok) {
    throw new Error(formatHttpError(response.status, parsed, Boolean(config.bearerToken)));
  }

  return parsed || {};
}

// Collect the logged-in YouTube/Google session cookies. Cookie values never
// touch chrome.storage, the badge, or any log — they go straight into the
// per-job request body and nowhere else.
async function collectSessionCookies() {
  const seen = new Set();
  const merged = [];
  for (const domain of COOKIE_DOMAINS) {
    let batch = [];
    try {
      batch = await chrome.cookies.getAll({ domain });
    } catch {
      batch = [];
    }
    for (const cookie of batch) {
      const key = `${cookie.domain}\t${cookie.path}\t${cookie.name}`;
      if (seen.has(key)) {
        continue;
      }
      seen.add(key);
      merged.push(cookie);
    }
  }
  return merged;
}

async function notifySuccess(baseUrl, result) {
  // The coordinator returns JobOut: { id, job_token, share_url, status, ... }.
  // Accept the legacy `job_id` too in case an older API is in front.
  const jobId = result.id ?? result.job_id;
  if (!jobId) {
    throw new Error("Karaoke responded OK but returned no job ID.");
  }

  // Prefer the server-provided share_url; otherwise build the SPA item route
  // (hash-routed `/app/#/job/{token}`) or fall back to the share path.
  const jobUrl =
    result.share_url ||
    (result.job_token
      ? `${baseUrl}/app/#/job/${result.job_token}`
      : `${baseUrl}/app/#/jobs/${jobId}`);
  const title = result.deduplicated ? "Already known to Karaoke" : "Submitted to Karaoke";
  const status = result.status ? `Status: ${result.status}. ` : "";
  const message = `${status}Click to open job #${jobId}.`;
  const notificationId = `karaoke-job-${jobId}-${Date.now()}`;

  const links = await getNotificationLinks();
  links[notificationId] = jobUrl;
  await chrome.storage.local.set({ [NOTIFICATION_LINKS_KEY]: links });

  chrome.notifications.create(notificationId, {
    type: "basic",
    iconUrl: NOTIFICATION_ICON,
    title,
    message,
    priority: 1,
  });
}

async function notifyFailure(message) {
  chrome.notifications.create(`karaoke-error-${Date.now()}`, {
    type: "basic",
    iconUrl: NOTIFICATION_ICON,
    title: "Karaoke submit failed",
    message: truncate(message, 240),
    priority: 2,
  });
}

async function getNotificationLinks() {
  const stored = await chrome.storage.local.get({ [NOTIFICATION_LINKS_KEY]: {} });
  return stored[NOTIFICATION_LINKS_KEY] || {};
}

function formatDetail(body) {
  if (!body) {
    return "No response body.";
  }
  if (typeof body.detail === "string") {
    return body.detail;
  }
  if (Array.isArray(body.detail)) {
    return body.detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  return JSON.stringify(body);
}

function formatHttpError(status, body, tokenConfigured) {
  if (status === 401) {
    const guidance = tokenConfigured
      ? "The saved bearer token was rejected. Check the ktx_ token in extension settings."
      : "This Karaoke URL requires authentication. Add a ktx_ bearer token in extension settings.";
    return `Karaoke authentication required (401): ${guidance}`;
  }

  if (status === 403) {
    const guidance = tokenConfigured
      ? "The saved bearer token is invalid or does not allow this request. Check the ktx_ token in extension settings."
      : "This Karaoke URL is protected. Add a ktx_ bearer token in extension settings.";
    return `Karaoke authorization failed (403): ${guidance}`;
  }

  return `Karaoke rejected the URL (${status}): ${formatDetail(body)}`;
}

function setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
  if (text !== "...") {
    chrome.alarms.create(CLEAR_BADGE_ALARM, { when: Date.now() + 3500 });
  }
}

function truncate(value, limit) {
  const text = String(value);
  return text.length > limit ? `${text.slice(0, limit - 1)}...` : text;
}

// Note: status polling and live progress should subscribe to GET /jobs/{id}/status
// and the WebSocket channel at `${baseUrl.replace(/^http/, 'ws')}/ws` once the SPA
// or a popup UI is wired in. The toolbar/contextmenu submit flow above only needs
// POST /jobs and a notification linking back to the SPA job page.
