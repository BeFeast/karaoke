import { serializeNetscapeCookies } from "./cookies.js";

const DEFAULT_BASE_URL = "http://10.10.0.13:13140";
const SOURCE = "chrome-extension";
const NOTIFICATION_LINKS_KEY = "notificationLinks";
const NOTIFICATION_ICON = "icons/karaoke-128.png";
const CLEAR_BADGE_ALARM = "clear-karaoke-badge";

// YouTube cookie rotation (issue #73): keep the coordinator's logged-in jar
// fresh so session-gated videos keep downloading without a manual re-export.
const REFRESH_COOKIES_ALARM = "refresh-youtube-cookies";
const COOKIE_REFRESH_PERIOD_MINUTES = 360; // every 6h
const COOKIE_SYNC_STATE_KEY = "youtubeCookieSync";
// Domains whose cookies make up a logged-in YouTube session. youtube.com is the
// primary jar; google.com carries the shared Google account auth cookies
// (SAPISID/__Secure-3PSID/…) that strengthen yt-dlp's logged-in requests.
const COOKIE_DOMAINS = ["youtube.com", "google.com"];

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

  ensureCookieAlarm();
  // Best-effort initial sync so a freshly-installed extension hands the
  // coordinator a jar without waiting for the first submit or alarm.
  refreshYoutubeCookies().catch(() => {});
});

chrome.runtime.onStartup.addListener(() => {
  ensureCookieAlarm();
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
  if (alarm.name === REFRESH_COOKIES_ALARM) {
    refreshYoutubeCookies().catch(() => {});
  }
});

// Let the options page trigger an on-demand "Sync YouTube cookies now".
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "refresh-youtube-cookies") {
    refreshYoutubeCookies()
      .then((result) => sendResponse({ ok: true, ...result }))
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true; // keep the message channel open for the async response
  }
  return false;
});

async function submitToKaraoke(url) {
  setBadge("...", "#5b6472");

  try {
    const config = await getConfig();
    await ensureHostPermission(config.baseUrl);
    const result = await createJob(config, url);
    await notifySuccess(config.baseUrl, result);
    setBadge("OK", "#137333");
    // Piggy-back a cookie refresh on every submit (best-effort, non-blocking):
    // the user is clearly logged in and active right now.
    refreshYoutubeCookies(config).catch(() => {});
  } catch (error) {
    await notifyFailure(error.message || String(error));
    setBadge("ERR", "#b3261e");
  }
}

function isSubmittableUrl(url) {
  return HTTP_URL.test(String(url || ""));
}

function ensureCookieAlarm() {
  chrome.alarms.get(REFRESH_COOKIES_ALARM, (existing) => {
    if (!existing) {
      chrome.alarms.create(REFRESH_COOKIES_ALARM, {
        periodInMinutes: COOKIE_REFRESH_PERIOD_MINUTES,
        delayInMinutes: 1,
      });
    }
  });
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

async function createJob(config, url) {
  const headers = {
    "Content-Type": "application/json",
  };
  if (config.bearerToken) {
    headers.Authorization = `Bearer ${config.bearerToken}`;
  }

  let response;
  try {
    response = await fetch(`${config.baseUrl}/jobs`, {
      method: "POST",
      headers,
      body: JSON.stringify({ url, source: SOURCE }),
    });
  } catch (error) {
    throw new Error(`Could not reach Karaoke at ${config.baseUrl}: ${error.message}`);
  }

  let body = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text };
    }
  }

  if (!response.ok) {
    throw new Error(formatHttpError(response.status, body, Boolean(config.bearerToken)));
  }

  return body || {};
}

// Collect the logged-in YouTube/Google session cookies and POST them to the
// coordinator's /cookies/youtube endpoint. Best-effort: failures are recorded
// for the options page but never surfaced as noisy notifications. Cookie values
// never touch chrome.storage or the badge — only counts + status.
async function refreshYoutubeCookies(config) {
  const cfg = config || (await getConfig());
  if (!cfg.bearerToken) {
    await recordCookieSync({ ok: false, reason: "no-token" });
    return { ok: false, reason: "no-token" };
  }

  const cookies = await collectSessionCookies();
  const youtubeCount = cookies.filter((c) => String(c.domain || "").includes("youtube.com")).length;
  if (youtubeCount === 0) {
    // Not logged in to YouTube in this browser — nothing useful to send.
    await recordCookieSync({ ok: false, reason: "not-logged-in" });
    return { ok: false, reason: "not-logged-in" };
  }

  const blob = serializeNetscapeCookies(cookies);

  let response;
  try {
    await ensureHostPermission(cfg.baseUrl);
    response = await fetch(`${cfg.baseUrl}/cookies/youtube`, {
      method: "POST",
      headers: {
        "Content-Type": "text/plain; charset=utf-8",
        Authorization: `Bearer ${cfg.bearerToken}`,
      },
      body: blob,
    });
  } catch (error) {
    await recordCookieSync({ ok: false, reason: `network: ${error.message}` });
    return { ok: false, reason: "network" };
  }

  if (!response.ok) {
    await recordCookieSync({ ok: false, reason: `http-${response.status}` });
    return { ok: false, reason: `http-${response.status}` };
  }

  let result = {};
  try {
    result = await response.json();
  } catch {
    result = {};
  }
  await recordCookieSync({
    ok: true,
    cookies: result.cookies ?? cookies.length,
    youtube: result.youtube_cookies ?? youtubeCount,
  });
  return { ok: true, cookies: result.cookies, youtube: result.youtube_cookies };
}

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

async function recordCookieSync(state) {
  await chrome.storage.local.set({
    [COOKIE_SYNC_STATE_KEY]: { ...state, at: Date.now() },
  });
}

async function notifySuccess(baseUrl, result) {
  if (!result.job_id) {
    throw new Error("Karaoke responded OK but returned no job ID.");
  }

  const jobUrl = `${baseUrl}/#/jobs/${result.job_id}`;
  const title = result.deduplicated ? "Already known to Karaoke" : "Submitted to Karaoke";
  const status = result.status ? `Status: ${result.status}. ` : "";
  const message = `${status}Click to open job #${result.job_id}.`;
  const notificationId = `karaoke-job-${result.job_id}-${Date.now()}`;

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
