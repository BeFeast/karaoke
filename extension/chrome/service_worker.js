const DEFAULT_BASE_URL = "http://10.10.0.13:13140";
const SOURCE = "chrome-extension";
const NOTIFICATION_LINKS_KEY = "notificationLinks";
const NOTIFICATION_ICON = "icons/karaoke-128.png";
const CLEAR_BADGE_ALARM = "clear-karaoke-badge";

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
  }
});

async function submitToKaraoke(url) {
  setBadge("...", "#5b6472");

  try {
    const config = await getConfig();
    await ensureHostPermission(config.baseUrl);
    const result = await createJob(config, url);
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
