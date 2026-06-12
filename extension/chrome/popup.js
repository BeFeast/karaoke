// Doorway popup-as-receipt (issue #155, design/m-doorway.jsx MPopupBoard).
//
// Opening the popup IS the toolbar click: the service worker preflights the
// active tab against GET /preflight (#181) and submits it when a dedicated
// yt-dlp extractor matches (once per tab+URL, chrome.storage.session-deduped);
// this page renders the real POST /jobs response as the receipt card, plus
// the real GET /jobs mini-feed ("tonight"). Generic-only / unreachable
// preflights render a confirm state with a Submit-anyway button instead of
// auto-submitting. No fabricated jobs, no invented figures — errors are shown
// as the server/Chrome reported them.

import { extractorReceiptLabel, failedReceiptLine } from "./receipt.js";

const DEFAULT_BASE_URL = "https://karaoke.oklabs.uk";
const ACTIVE_STATUSES = new Set(["queued", "downloading", "separating", "transcribing"]);
const FEED_LIMIT = 6;
const RECEIPT_POLL_MS = 1500;
// How long a feed-row ✕ stays armed ("sure?") before reverting (#177).
const DELETE_ARM_MS = 4000;

let config = { baseUrl: DEFAULT_BASE_URL, bearerToken: "" };
let receiptJobId = null;
let receiptCookieLine = "";
// The matched yt-dlp extractor (preflight #181) — shown as "Youtube ✓" on the
// receipt meta line; null on Submit-anyway receipts.
let receiptExtractor = null;
// The session dedup record behind the receipt — dismissing writes
// `dismissed: true` back onto it so reopening the popup keeps it hidden.
let receiptDedupKey = null;
let receiptRecord = null;

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

async function submitAndRenderReceipt(force = false) {
  let response;
  try {
    response = await chrome.runtime.sendMessage({ type: "submit-active-tab", force });
  } catch (error) {
    setReceiptHint(false);
    renderReceiptError(String(error?.message || error));
    return;
  }

  if (!response) {
    setReceiptHint(false);
    renderReceiptError("The extension service worker did not answer.");
    return;
  }

  if (!response.ok) {
    // Nothing was submitted, so the "already submitted" hint would lie.
    setReceiptHint(false);
    if (response.confirm) {
      // Preflight says "not obviously a video" — or could not say at all
      // (#181). No auto-submit; the user decides with Submit anyway.
      renderConfirm(response.message);
    } else if (response.message) {
      // Guard/preflight refusal (guard.js #177, preflight.js #181) — a
      // friendly note, not an error.
      renderReceiptNote(`nothing submitted — ${response.message}`);
    } else {
      renderReceiptError(response.error || "Submit failed.");
    }
    return;
  }

  receiptCookieLine = response.cookiesAttached
    ? "youtube session ✓ rode along — this job only"
    : "no youtube session — public fetch";
  receiptExtractor = response.extractor || null;
  receiptDedupKey = response.dedupKey || null;
  receiptRecord = {
    jobId: response.jobId ?? response.job?.id ?? null,
    cookiesAttached: Boolean(response.cookiesAttached),
    extractor: receiptExtractor,
  };
  // The static "the toolbar click already submitted this tab" hint is only
  // true on the one-click path — Submit-anyway receipts speak for themselves.
  setReceiptHint(!force);

  if (response.dismissed) {
    // Dismissed on an earlier open — stay hidden; the job rides the feed (#177).
    hideReceipt();
    return;
  }

  if (response.job) {
    renderReceiptJob(response.job, response.tabTitle);
  }
  receiptJobId = response.jobId ?? response.job?.id ?? null;
  if (receiptJobId != null) {
    pollReceipt(response.tabTitle);
  }
}

async function pollReceipt(tabTitle) {
  if (receiptJobId == null) {
    return;
  }
  const job = await fetchJson(`/jobs/${receiptJobId}/status`).catch(() => null);
  if (receiptJobId == null) {
    // Dismissed while the fetch was in flight — stop repainting.
    return;
  }
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
  head.append(dismissButton());
  sign.append(head);

  if (job.status === "failed") {
    // Status lives in the chip alone (#177 — it used to repeat in the wipe).
    // One compact line (stage_note / first error line) replaces the raw
    // PipelineError dump; the full text rides a hover tooltip.
    const line = failedReceiptLine(job);
    if (line) {
      const attrs = {
        class: "m-mono",
        style: "margin-top: 8px; font-size: 10px; color: var(--err); overflow: hidden; text-overflow: ellipsis; white-space: nowrap",
      };
      const full = String(job.error || "").trim();
      if (full) {
        attrs.title = full;
      }
      sign.append(el("div", attrs, line));
    }
  } else if (job.status !== "cancelled") {
    const wipeRow = el("div", { style: "margin-top: 8px" });
    wipeRow.append(wipe(stageText(job), job.progress ?? 0, 11));
    sign.append(wipeRow);
  }

  const meta = el("div", {
    class: "m-mono",
    style: "display: flex; justify-content: space-between; margin-top: 7px; font-size: 10px; color: var(--muted)",
  });
  // "Youtube ✓ · #42" — the preflight-matched extractor rides next to the job
  // id (#181); Submit-anyway receipts have no match and show the id alone.
  const extractorLabel = extractorReceiptLabel(receiptExtractor);
  const idLabel = extractorLabel ? `${extractorLabel} · #${job.id}` : `#${job.id}`;
  meta.append(el("span", {}, receiptCookieLine), el("span", {}, idLabel));
  sign.append(meta);
}

// Preflight confirm state (#181): yt-dlp would only reach this page through
// its catch-all Generic extractor (or the preflight could not answer), so
// nothing was submitted. One click on Submit anyway re-enters the normal
// submit path with force=true and the receipt takes over.
function renderConfirm(message) {
  const sign = resetReceipt("var(--warn)");
  sign.append(
    el("div", { style: "font-size: 11.5px; line-height: 1.5; color: var(--fg-soft)" }, message),
  );
  const row = el("div", { style: "margin-top: 9px" });
  const btn = el("button", { class: "m-btn sm primary", type: "button" }, "Submit anyway");
  btn.addEventListener("click", () => {
    btn.disabled = true;
    renderReceiptNote("submitting this tab…");
    submitAndRenderReceipt(true).finally(() => renderFeed());
  });
  row.append(btn);
  sign.append(row);
}

function setReceiptHint(visible) {
  document.querySelector("#receipt-hint").hidden = !visible;
}

// ✕ on the receipt card — local-state dismiss (#177): hide the card, remember
// the choice on the session dedup record so reopening the popup keeps it
// hidden, and let the job surface as a normal "tonight" row. No server call.
function dismissButton() {
  const btn = el(
    "button",
    {
      class: "m-btn sm ghost",
      type: "button",
      title: "Dismiss this receipt",
      style: "padding: 0 5px; flex: none; font-size: 11px",
    },
    "✕",
  );
  btn.addEventListener("click", async () => {
    hideReceipt();
    if (receiptDedupKey && receiptRecord) {
      await chrome.storage.session.set({
        [receiptDedupKey]: { ...receiptRecord, dismissed: true },
      });
    }
    renderFeed();
  });
  return btn;
}

function hideReceipt() {
  document.querySelector("#receipt-wrap").hidden = true;
  receiptJobId = null;
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

  // min-width: 0 on the grid child too — without it the nowrap title sets the
  // track's min-content width and the row overflows the popup (#163).
  const head = el("div", { style: "display: flex; align-items: center; gap: 8px; min-width: 0" });
  head.append(
    el(
      "span",
      { style: "font-size: 12px; font-weight: 550; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap" },
      job.title || job.source_url,
    ),
  );

  const running = ACTIVE_STATUSES.has(job.status);
  if (job.status === "completed") {
    head.append(el("span", { class: "m-mono", style: "font-size: 10.5px; color: var(--ok); flex: none" }, "ready ▸"));
  } else if (job.status === "failed") {
    head.append(el("span", { class: "m-mono", style: "font-size: 10.5px; color: var(--err); flex: none" }, "failed ↻"));
  } else if (job.status === "cancelled") {
    head.append(el("span", { class: "m-mono", style: "font-size: 10.5px; color: var(--muted); flex: none" }, "cancelled"));
  } else {
    head.append(el("span", { class: "m-mono", style: "font-size: 10.5px; color: var(--accent); flex: none" }, `${job.progress ?? 0}%`));
  }
  head.append(deleteButton(job));
  row.append(head);

  if (running) {
    row.append(wipe(stageText(job), job.progress ?? 0, 10.5));
  }
  return row;
}

// ✕ on a feed row → DELETE /jobs/{id} (owner-scoped, #51) with an inline
// two-step confirm: first click arms the button ("sure?"), second click
// deletes; the arm reverts after a beat. Inline instead of window.confirm —
// native modal dialogs are unreliable inside MV3 action popups.
function deleteButton(job) {
  const idleStyle = "padding: 0 5px; flex: none; font-size: 10.5px; color: var(--muted)";
  const btn = el(
    "button",
    { class: "m-btn sm ghost", type: "button", title: `Delete job #${job.id}`, style: idleStyle },
    "✕",
  );
  let armed = false;
  let disarmTimer = null;
  const disarm = () => {
    armed = false;
    btn.textContent = "✕";
    btn.style.color = "var(--muted)";
  };
  btn.addEventListener("click", async (event) => {
    event.stopPropagation();
    if (!armed) {
      armed = true;
      btn.textContent = "sure?";
      btn.style.color = "var(--err)";
      disarmTimer = setTimeout(disarm, DELETE_ARM_MS);
      return;
    }
    clearTimeout(disarmTimer);
    btn.disabled = true;
    btn.textContent = "…";
    try {
      await fetchJson(`/jobs/${job.id}`, { method: "DELETE" });
    } catch (error) {
      btn.disabled = false;
      btn.title = `Delete failed — ${error?.message || error}`;
      disarm();
      return;
    }
    renderFeed();
  });
  return btn;
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

async function fetchJson(path, init = {}) {
  const headers = {};
  if (config.bearerToken) {
    headers.Authorization = `Bearer ${config.bearerToken}`;
  }
  let response;
  try {
    response = await fetch(`${config.baseUrl}${path}`, { ...init, headers });
  } catch (error) {
    throw new Error(`the booth at ${hostLabel(config.baseUrl)} didn't answer (${error.message})`);
  }
  if (!response.ok) {
    const error = new Error(`HTTP ${response.status}`);
    error.httpStatus = response.status;
    throw error;
  }
  if (response.status === 204) {
    // DELETE /jobs/{id} answers 204 No Content.
    return null;
  }
  return response.json();
}
