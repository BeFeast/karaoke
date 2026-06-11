const DEFAULT_BASE_URL = "https://karaoke.oklabs.uk";

const form = document.querySelector("#settings-form");
const baseUrlInput = document.querySelector("#base-url");
const bearerTokenInput = document.querySelector("#bearer-token");
const status = document.querySelector("#status");
const versionFooter = document.querySelector("#version");

const manifest = chrome.runtime.getManifest();
versionFooter.textContent = `${manifest.name} v${manifest.version}`;

document.addEventListener("DOMContentLoaded", restoreOptions);
form.addEventListener("submit", saveOptions);

async function restoreOptions() {
  const stored = await chrome.storage.sync.get({
    baseUrl: DEFAULT_BASE_URL,
    bearerToken: "",
  });
  baseUrlInput.value = stored.baseUrl;
  bearerTokenInput.value = stored.bearerToken;
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

function normalizeBaseUrl(value) {
  const trimmed = String(value || DEFAULT_BASE_URL).trim().replace(/\/+$/, "");
  const parsed = new URL(trimmed);
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("Base URL must start with http:// or https://.");
  }
  return parsed.origin + parsed.pathname.replace(/\/+$/, "");
}
