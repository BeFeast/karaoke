const DEFAULT_BASE_URL = "http://10.10.0.13:13140";
const COOKIE_SYNC_STATE_KEY = "youtubeCookieSync";

const form = document.querySelector("#settings-form");
const baseUrlInput = document.querySelector("#base-url");
const bearerTokenInput = document.querySelector("#bearer-token");
const status = document.querySelector("#status");
const syncCookiesButton = document.querySelector("#sync-cookies");
const cookieStatus = document.querySelector("#cookie-status");

document.addEventListener("DOMContentLoaded", restoreOptions);
form.addEventListener("submit", saveOptions);
syncCookiesButton.addEventListener("click", syncCookiesNow);

async function restoreOptions() {
  const stored = await chrome.storage.sync.get({
    baseUrl: DEFAULT_BASE_URL,
    bearerToken: "",
  });
  baseUrlInput.value = stored.baseUrl;
  bearerTokenInput.value = stored.bearerToken;
  await renderCookieSyncState();
}

async function saveOptions(event) {
  event.preventDefault();
  status.textContent = "";

  let baseUrl;
  try {
    baseUrl = normalizeBaseUrl(baseUrlInput.value);
  } catch (error) {
    status.textContent = error.message;
    status.style.color = "#b3261e";
    return;
  }

  const originPattern = `${new URL(baseUrl).origin}/*`;
  const granted = await chrome.permissions.request({ origins: [originPattern] });
  if (!granted) {
    status.textContent = `Chrome did not grant access to ${new URL(baseUrl).origin}.`;
    status.style.color = "#b3261e";
    return;
  }

  await chrome.storage.sync.set({
    baseUrl,
    bearerToken: bearerTokenInput.value.trim(),
  });
  status.style.color = "#137333";
  status.textContent = "Saved.";
}

async function syncCookiesNow() {
  cookieStatus.style.color = "#5b6472";
  cookieStatus.textContent = "Syncing…";
  syncCookiesButton.disabled = true;
  try {
    const result = await chrome.runtime.sendMessage({ type: "refresh-youtube-cookies" });
    if (result?.ok) {
      cookieStatus.style.color = "#137333";
      const count = result.youtube ?? result.cookies;
      cookieStatus.textContent = count
        ? `Sent ${count} YouTube cookies.`
        : "Cookies sent.";
    } else {
      cookieStatus.style.color = "#b3261e";
      cookieStatus.textContent = `Sync failed: ${describeReason(result?.reason || result?.error)}`;
    }
  } catch (error) {
    cookieStatus.style.color = "#b3261e";
    cookieStatus.textContent = `Sync failed: ${error.message || String(error)}`;
  } finally {
    syncCookiesButton.disabled = false;
    await renderCookieSyncState();
  }
}

async function renderCookieSyncState() {
  const stored = await chrome.storage.local.get({ [COOKIE_SYNC_STATE_KEY]: null });
  const state = stored[COOKIE_SYNC_STATE_KEY];
  if (!state || !state.at) {
    return;
  }
  const when = new Date(state.at).toLocaleString();
  if (state.ok) {
    cookieStatus.style.color = "#137333";
    const count = state.youtube ?? state.cookies;
    cookieStatus.textContent = `Last sync ${when}: sent ${count ?? "?"} YouTube cookies.`;
  } else {
    cookieStatus.style.color = "#b3261e";
    cookieStatus.textContent = `Last sync ${when}: ${describeReason(state.reason)}`;
  }
}

function describeReason(reason) {
  switch (reason) {
    case "no-token":
      return "no bearer token configured.";
    case "not-logged-in":
      return "not logged in to YouTube in this browser.";
    case undefined:
    case null:
    case "":
      return "unknown error.";
    default:
      return String(reason);
  }
}

function normalizeBaseUrl(value) {
  const trimmed = String(value || DEFAULT_BASE_URL).trim().replace(/\/+$/, "");
  const parsed = new URL(trimmed);
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("Base URL must start with http:// or https://.");
  }
  return parsed.origin + parsed.pathname.replace(/\/+$/, "");
}
