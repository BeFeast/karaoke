import { buildJobBody, countYoutubeCookies } from "./cookies.js";
import { MENU_SPEC, submitRefusal } from "./guard.js";

const DEFAULT_BASE_URL = "https://karaoke.oklabs.uk";
const SOURCE = "chrome-extension";
const NOTIFICATION_LINKS_KEY = "notificationLinks";
const NOTIFICATION_ICON = "icons/karaoke-128.png";
const CLEAR_BADGE_ALARM = "clear-karaoke-badge";

// Toolbar badge states per the Doorway icon board (design/m-doorway.jsx:126):
// idle = no badge, working = job progress %, ready = ✓, error = !.
// Working carries the Wave-0 green bake (#e8a93c -> #9fd07a); ready/error are
// the board's own literals (= the booth --accent/--err token values).
const BADGE_WORKING = "#9fd07a";
const BADGE_READY = "#5f7a4a";
const BADGE_ERROR = "#a8442f";
const BADGE_TEXT = "#ffffff";

// Track the most recently submitted job in chrome.storage.session so the
// badge can follow real progress across service-worker restarts. The poll
// alarm fires at Chrome's MV3 minimum interval.
const TRACKED_JOB_KEY = "trackedJob";
const PROGRESS_POLL_ALARM = "karaoke-progress-poll";
const PROGRESS_POLL_MINUTES = 0.5;
const MAX_POLL_FAILURES = 10;
const TERMINAL_BADGE_MS = 5 * 60 * 1000;

// One submit per tab+URL (chrome.storage.session): opening the popup again on
// the same page shows the existing receipt instead of minting a second job.
const SUBMITTED_PREFIX = "submitted:";

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

chrome.runtime.onInstalled.addListener(() => {
  // Table-driven registration off MENU_SPEC (guard.js) — exactly one entry per
  // distinct action. The old page+link pair both rendered on a YouTube video
  // link, doubling the menu (#177).
  chrome.contextMenus.removeAll(() => {
    for (const item of MENU_SPEC) {
      chrome.contextMenus.create(item);
    }
  });

  cleanupLegacyCookieSync();
});

chrome.runtime.onStartup.addListener(() => {
  cleanupLegacyCookieSync();
});

// The toolbar click opens popup.html (manifest `default_popup`), which
// immediately asks this worker to submit the active tab — the popup IS the
// receipt (issue #155). chrome.action.onClicked does not fire with a popup.
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "submit-active-tab") {
    submitActiveTab().then(sendResponse, (error) =>
      sendResponse({ ok: false, error: String(error?.message || error) }),
    );
    return true;
  }
  return undefined;
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.pageUrl || tab?.url || "";

  // Same guard as the toolbar path (#177): non-http(s) schemes and the booth's
  // own pages get a friendly refusal instead of a doomed job.
  let refusal;
  try {
    refusal = submitRefusal(url, (await getConfig()).baseUrl);
  } catch (error) {
    await notifyFailure(error.message || String(error));
    return;
  }
  if (refusal) {
    await notifyFailure(refusal.message);
    return;
  }

  try {
    const { job, cookiesAttached } = await submitToKaraoke(url);
    await notifySuccess(job);
    if (!info.linkUrl && tab?.id != null && tab?.url === url) {
      await rememberTabSubmit(tab.id, url, job.id, cookiesAttached);
    }
  } catch (error) {
    await notifyFailure(error.message || String(error));
    await setErrorBadge();
  }
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
  if (alarm.name === PROGRESS_POLL_ALARM) {
    pollTrackedJob();
    return;
  }
  if (alarm.name === LEGACY_REFRESH_ALARM) {
    // Defensive: an upgraded install may still have the old periodic alarm
    // queued before cleanup ran. Drop it instead of acting on it.
    chrome.alarms.clear(LEGACY_REFRESH_ALARM);
  }
});

// ── popup submit flow ────────────────────────────────────────────────────────

async function submitActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url || "";
  const tabTitle = tab?.title || "";
  // Guard before any job is minted (#177): non-http(s) schemes and the booth's
  // own pages (self-submit minted doomed job #79) get a friendly refusal the
  // popup renders as a note, not an error.
  const refusal = submitRefusal(url, (await getConfig()).baseUrl);
  if (refusal) {
    return { ok: false, reason: refusal.reason, message: refusal.message };
  }

  const dedupKey = `${SUBMITTED_PREFIX}${tab.id}:${url}`;
  const stored = await chrome.storage.session.get(dedupKey);
  const previous = stored[dedupKey];
  if (previous) {
    return {
      ok: true,
      jobId: previous.jobId,
      cookiesAttached: previous.cookiesAttached,
      dismissed: Boolean(previous.dismissed),
      cached: true,
      tabTitle,
      dedupKey,
    };
  }

  const { job, cookiesAttached } = await submitToKaraoke(url);
  await chrome.storage.session.set({
    [dedupKey]: { jobId: job.id, cookiesAttached },
  });
  return { ok: true, job, jobId: job.id, cookiesAttached, tabTitle, dedupKey };
}

async function rememberTabSubmit(tabId, url, jobId, cookiesAttached) {
  await chrome.storage.session.set({
    [`${SUBMITTED_PREFIX}${tabId}:${url}`]: { jobId, cookiesAttached },
  });
}

// ── submit ──────────────────────────────────────────────────────────────────

async function submitToKaraoke(url) {
  setWorkingBadge("…");

  try {
    const config = await getConfig();
    await ensureHostPermission(config.baseUrl);
    // Read the user's logged-in YouTube/Google cookies at submit time and send
    // them with THIS job only (issue #77). No central jar, no always-on sync —
    // the cookies ride the request and the server never persists them.
    const cookies = await collectSessionCookies();
    const job = await createJob(config, url, cookies);
    if (job.id == null) {
      throw new Error("Karaoke responded OK but returned no job ID.");
    }
    await startTrackingJob(job);
    return { job, cookiesAttached: countYoutubeCookies(cookies) > 0 };
  } catch (error) {
    await setErrorBadge();
    throw error;
  }
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

function authHeaders(config) {
  const headers = {};
  if (config.bearerToken) {
    headers.Authorization = `Bearer ${config.bearerToken}`;
  }
  return headers;
}

async function createJob(config, url, cookies) {
  const headers = {
    "Content-Type": "application/json",
    ...authHeaders(config),
  };

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

// ── badge — idle / working(%) / ready / error per the icon board ───────────

async function startTrackingJob(job) {
  setWorkingBadge(String(job.progress ?? 0));
  await chrome.storage.session.set({
    [TRACKED_JOB_KEY]: { jobId: job.id, failures: 0 },
  });
  chrome.alarms.create(PROGRESS_POLL_ALARM, {
    periodInMinutes: PROGRESS_POLL_MINUTES,
  });
}

async function stopTrackingJob() {
  await chrome.storage.session.remove(TRACKED_JOB_KEY);
  chrome.alarms.clear(PROGRESS_POLL_ALARM);
}

async function pollTrackedJob() {
  const stored = await chrome.storage.session.get(TRACKED_JOB_KEY);
  const tracked = stored[TRACKED_JOB_KEY];
  if (!tracked) {
    chrome.alarms.clear(PROGRESS_POLL_ALARM);
    return;
  }

  let job;
  try {
    const config = await getConfig();
    const response = await fetch(`${config.baseUrl}/jobs/${tracked.jobId}/status`, {
      headers: authHeaders(config),
    });
    if (response.status === 404) {
      // Gone (deleted, or another owner's view) — nothing left to follow.
      await stopTrackingJob();
      chrome.action.setBadgeText({ text: "" });
      return;
    }
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    job = await response.json();
  } catch {
    const failures = (tracked.failures || 0) + 1;
    if (failures >= MAX_POLL_FAILURES) {
      await stopTrackingJob();
      chrome.action.setBadgeText({ text: "" });
    } else {
      await chrome.storage.session.set({
        [TRACKED_JOB_KEY]: { ...tracked, failures },
      });
    }
    return;
  }

  if (job.status === "completed") {
    await stopTrackingJob();
    setBadge("✓", BADGE_READY);
    return;
  }
  if (job.status === "failed" || job.status === "cancelled") {
    await stopTrackingJob();
    setBadge("!", BADGE_ERROR);
    return;
  }
  setWorkingBadge(String(job.progress ?? 0));
  await chrome.storage.session.set({
    [TRACKED_JOB_KEY]: { ...tracked, failures: 0 },
  });
}

function setWorkingBadge(text) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color: BADGE_WORKING });
  chrome.action.setBadgeTextColor({ color: BADGE_TEXT });
}

async function setErrorBadge() {
  await stopTrackingJob();
  setBadge("!", BADGE_ERROR);
}

// Terminal badge (✓ / !): linger, then return to idle (empty badge).
function setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
  chrome.action.setBadgeTextColor({ color: BADGE_TEXT });
  chrome.alarms.create(CLEAR_BADGE_ALARM, { when: Date.now() + TERMINAL_BADGE_MS });
}

// ── notifications (context-menu submits — the popup is its own receipt) ─────

async function notifySuccess(job) {
  // The coordinator returns JobOut: { id, job_token, share_url, status, ... }.
  const jobUrl = job.share_url || `${(await getConfig()).baseUrl}/app/#/job/${job.job_token}`;
  const status = job.status ? `Status: ${job.status}. ` : "";
  const message = `${status}Click to open job #${job.id}.`;
  const notificationId = `karaoke-job-${job.id}-${Date.now()}`;

  const links = await getNotificationLinks();
  links[notificationId] = jobUrl;
  await chrome.storage.local.set({ [NOTIFICATION_LINKS_KEY]: links });

  chrome.notifications.create(notificationId, {
    type: "basic",
    iconUrl: NOTIFICATION_ICON,
    title: "Submitted to Karaoke",
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

function truncate(value, limit) {
  const text = String(value);
  return text.length > limit ? `${text.slice(0, limit - 1)}...` : text;
}

// Note: the popup follows its receipt job via GET /jobs/{id}/status while
// open; this worker's poll alarm keeps the toolbar badge honest afterwards.
// The WebSocket channel at `${baseUrl.replace(/^http/, 'ws')}/ws` remains the
// canonical live-progress feed for the SPA.
