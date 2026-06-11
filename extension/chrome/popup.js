// Doorway popup-as-receipt (issue #155, design/m-doorway.jsx MPopupBoard).
//
// Opening the popup IS the toolbar click: the service worker submits the
// active tab (once per tab+URL, chrome.storage.session-deduped) and this page
// renders the real POST /jobs response as the receipt card, plus the real
// GET /jobs mini-feed ("tonight"). No fabricated jobs, no invented figures —
// errors are shown as the server/Chrome reported them.

const DEFAULT_BASE_URL = "https://karaoke.oklabs.uk";
const ACTIVE_STATUSES = new Set(["queued", "downloading", "separating", "transcribing"]);
const FEED_LIMIT = 6;
const RECEIPT_POLL_MS = 1500;

let config = { baseUrl: DEFAULT_BASE_URL, bearerToken: "" };
let receiptJobId = null;
let receiptCookieLine = "";

init();

async function init() {
  const stored = await chrome.storage.sync.get({
    baseUrl: DEFAULT_BASE_URL,
    bearerToken: "",
  });
  config = {
    baseUrl: String(stored.baseUrl || DEFAULT_BASE_URL).trim().replace(/\/+$/, ""),
    bearerToken: String(stored.bearerToken || "").trim(),
  };

  document.querySelector("#booth-host").textContent = hostLabel(config.baseUrl);
  document.querySelector("#version").textContent = `v${chrome.runtime.getManifest().version}`;
  document.querySelector("#open-booth").href = `${config.baseUrl}/app/`;
  document.querySelector("#open-settings").addEventListener("click", () => {
    chrome.runtime.openOptionsPage();
  });

  renderReceiptNote("submitting this tab…");
  // Feed after the submit settles so the receipt job is excluded from
  // "tonight" deterministically (it is the receipt, not a feed row).
  submitAndRenderReceipt().finally(() => renderFeed());
}

function hostLabel(baseUrl) {
  try {
    return new URL(baseUrl).host;
  } catch {
    return baseUrl;
  }
}

// ── receipt ─────────────────────────────────────────────────────────────────

async function submitAndRenderReceipt() {
  let response;
  try {
    response = await chrome.runtime.sendMessage({ type: "submit-active-tab" });
  } catch (error) {
    renderReceiptError(String(error?.message || error));
    return;
  }

  if (!response) {
    renderReceiptError("The extension service worker did not answer.");
    return;
  }

  if (!response.ok) {
    if (response.reason === "unsupported-page") {
      renderReceiptNote("nothing submitted — open an http(s) video page, then hit the toolbar");
    } else {
      renderReceiptError(response.error || "Submit failed.");
    }
    return;
  }

  receiptCookieLine = response.cookiesAttached
    ? "youtube session ✓ rode along — this job only"
    : "no youtube session — public fetch";

  if (response.job) {
    renderReceiptJob(response.job, response.tabTitle);
  }
  receiptJobId = response.jobId ?? response.job?.id ?? null;
  if (receiptJobId != null) {
    pollReceipt(response.tabTitle);
  }
}

async function pollReceipt(tabTitle) {
  const job = await fetchJson(`/jobs/${receiptJobId}/status`).catch(() => null);
  if (job) {
    renderReceiptJob(job, tabTitle);
    if (!ACTIVE_STATUSES.has(job.status)) {
      return;
    }
  }
  setTimeout(() => pollReceipt(tabTitle), RECEIPT_POLL_MS);
}

// The .m-sign receipt card, inner pattern from m-doorway.jsx:17-29.
function renderReceiptJob(job, tabTitle) {
  const sign = resetReceipt("var(--accent)");

  const head = el("div", { style: "display: flex; align-items: center; gap: 8px" });
  head.append(chipForStatus(job.status));
  head.append(
    el(
      "span",
      { style: "font-weight: 650; font-size: 12.5px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap" },
      job.title || tabTitle || job.source_url,
    ),
  );
  sign.append(head);

  const wipeRow = el("div", { style: "margin-top: 8px" });
  wipeRow.append(wipe(stageText(job), job.progress ?? 0, 11));
  sign.append(wipeRow);

  const meta = el("div", {
    class: "m-mono",
    style: "display: flex; justify-content: space-between; margin-top: 7px; font-size: 10px; color: var(--muted)",
  });
  meta.append(el("span", {}, receiptCookieLine), el("span", {}, `#${job.id}`));
  sign.append(meta);

  if (job.status === "failed" && job.error) {
    sign.append(
      el("div", { class: "m-mono", style: "margin-top: 7px; font-size: 10px; color: var(--err)" }, job.error),
    );
  }
}

function renderReceiptError(message) {
  const sign = resetReceipt("var(--err)");
  const head = el("div", { style: "display: flex; align-items: center; gap: 8px" });
  head.append(chip("err", "not submitted"));
  sign.append(head);
  sign.append(
    el("div", { style: "margin-top: 8px; font-size: 11.5px; line-height: 1.5; color: var(--fg-soft)" }, message),
  );
}

function renderReceiptNote(text) {
  const sign = resetReceipt("var(--border)");
  sign.append(el("div", { class: "m-mono", style: "font-size: 10.5px; color: var(--muted)" }, text));
}

function resetReceipt(borderColor) {
  const sign = document.querySelector("#receipt");
  sign.replaceChildren();
  sign.style.borderColor = borderColor;
  return sign;
}

// ── tonight — the real feed ────────────────────────────────────────────────

async function renderFeed() {
  const feed = document.querySelector("#feed");
  let jobs;
  try {
    jobs = await fetchJson(`/jobs?limit=${FEED_LIMIT}`);
  } catch (error) {
    feed.replaceChildren(feedNote(feedErrorText(error)));
    return;
  }

  const rows = (Array.isArray(jobs) ? jobs : []).filter((j) => j.id !== receiptJobId).slice(0, FEED_LIMIT - 1);
  if (!rows.length) {
    feed.replaceChildren(feedNote("nothing else tonight — this submit opens the list"));
    return;
  }
  feed.replaceChildren(...rows.map(feedRow));
}

function feedErrorText(error) {
  const status = error?.httpStatus;
  if (status === 401 || status === 403) {
    return "tonight's list needs a stage pass — add a ktx_ pass in settings";
  }
  return `tonight's list unavailable — ${error?.message || error}`;
}

function feedNote(text) {
  return el("div", { class: "m-mono", style: "font-size: 10.5px; color: var(--muted); padding: 4px 2px" }, text);
}

// Feed row, inner pattern from m-doorway.jsx:43-51 — real jobs only.
function feedRow(job) {
  const row = el("div", {
    style:
      "display: grid; gap: 4px; padding: 8px 10px; border-radius: 8px; background: var(--bg-card); border: 1px solid var(--border-soft); cursor: pointer",
    title: job.source_url || "",
  });
  row.addEventListener("click", () => {
    chrome.tabs.create({ url: `${config.baseUrl}/app/#/job/${job.job_token}` });
  });

  const head = el("div", { style: "display: flex; align-items: center; gap: 8px" });
  head.append(
    el(
      "span",
      { style: "font-size: 12px; font-weight: 550; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap" },
      job.title || job.source_url,
    ),
  );

  const running = ACTIVE_STATUSES.has(job.status);
  if (job.status === "completed") {
    head.append(el("span", { class: "m-mono", style: "font-size: 10.5px; color: var(--ok)" }, "ready ▸"));
  } else if (job.status === "failed") {
    head.append(el("span", { class: "m-mono", style: "font-size: 10.5px; color: var(--err)" }, "failed ↻"));
  } else if (job.status === "cancelled") {
    head.append(el("span", { class: "m-mono", style: "font-size: 10.5px; color: var(--muted)" }, "cancelled"));
  } else {
    head.append(el("span", { class: "m-mono", style: "font-size: 10.5px; color: var(--accent)" }, `${job.progress ?? 0}%`));
  }
  row.append(head);

  if (running) {
    row.append(wipe(stageText(job), job.progress ?? 0, 10.5));
  }
  return row;
}

// ── shared bits ─────────────────────────────────────────────────────────────

function stageText(job) {
  return job.stage_note || job.status;
}

function chipForStatus(status) {
  if (status === "completed") {
    return chip("ok", "ready");
  }
  if (status === "failed") {
    return chip("err", "failed");
  }
  if (status === "cancelled") {
    return chip("", "cancelled");
  }
  return chip("run", "on stage");
}

function chip(kind, text) {
  const span = el("span", { class: kind ? `m-chip ${kind}` : "m-chip" });
  span.append(el("span", { class: "m-dot" }), document.createTextNode(text));
  return span;
}

// MWipe (design/claude-export/sections/m-brand.jsx:4-11) as plain DOM.
function wipe(text, pct, size) {
  const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
  const span = el("span", {
    class: "m-wipe",
    style: `font-size: ${size}px; font-family: var(--font-mono); font-weight: 500`,
  });
  span.append(el("span", { class: "w-dim" }, text));
  const fill = el("span", { class: "w-fill", "aria-hidden": "true" }, text);
  fill.style.width = `${clamped}%`;
  span.append(fill);
  return span;
}

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "class") {
      node.className = value;
    } else if (key === "style") {
      node.style.cssText = value;
    } else {
      node.setAttribute(key, value);
    }
  }
  if (text != null) {
    node.textContent = text;
  }
  return node;
}

async function fetchJson(path) {
  const headers = {};
  if (config.bearerToken) {
    headers.Authorization = `Bearer ${config.bearerToken}`;
  }
  let response;
  try {
    response = await fetch(`${config.baseUrl}${path}`, { headers });
  } catch (error) {
    throw new Error(`the booth at ${hostLabel(config.baseUrl)} didn't answer (${error.message})`);
  }
  if (!response.ok) {
    const error = new Error(`HTTP ${response.status}`);
    error.httpStatus = response.status;
    throw error;
  }
  return response.json();
}
