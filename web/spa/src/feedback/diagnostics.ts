import { getHealth } from "../api";

const INSTALL_KEY = "karaoke-feedback-install-id";
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const LOG_LINES = 250;
let installId: string | undefined;
const logs: string[] = [];

export function feedbackInstallId(): string {
  if (installId) return installId;
  try {
    const saved = localStorage.getItem(INSTALL_KEY);
    if (saved && UUID_V4.test(saved)) return (installId = saved);
  } catch {
    // Storage may be blocked. Keep the ID for this tab.
  }

  // getRandomValues also works on the trusted LAN's HTTP origin.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  installId = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  try {
    localStorage.setItem(INSTALL_KEY, installId);
  } catch {
    // Keep the in-memory fallback.
  }
  return installId;
}

export function redactFeedbackText(text: string): string {
  return text
    .replace(/[\w.!#$%&'*+/=?^`{|}~-]+@[\w.-]+\.[a-z]{2,}/gi, "[email]")
    .replace(/(?:\/share\/|#\/?job\/)[^\s/?#]+|\b(?:ktx_|Bearer\s+)[\w.-]+/gi, "[credential]")
    .replace(/\beyJ[\w-]+\.[\w-]+\.[\w-]+\b|\b[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\b/gi,
      "[credential]")
    // Job share tokens are generated from 24 bytes as unpadded base64url.
    .replace(/(?<![\w-])[\w-]{32}(?![\w-])/g, "[credential]");
}

export function startFeedbackLog(): void {
  for (const level of ["debug", "log", "info", "warn", "error"] as const) {
    const original = console[level].bind(console);
    console[level] = (...args: unknown[]) => {
      original(...args);
      // Objects can contain full API responses, including account data.
      const message = redactFeedbackText(args.filter((arg) => typeof arg === "string").join(" ")).slice(0, 240);
      for (const line of message.split(/\r?\n/).filter(Boolean)) logs.push(`${level}: ${line}`);
      if (logs.length > LOG_LINES) logs.splice(0, logs.length - LOG_LINES);
    };
  }
}

export function feedbackLog(): string {
  return logs.join("\n");
}

export async function feedbackMetadata() {
  const { pathname, hash } = window.location;
  const viewport = { width: window.innerWidth, height: window.innerHeight };
  const route = {
    pathname: pathname.replace(/^(\/share\/)[^/]+/, "$1:token"),
    hash: hash ? hash.replace(/^(#\/?job\/)[^/]+/, "$1:token") : undefined,
  };
  const health = await getHealth().catch(() => null);
  return {
    version: health?.version ?? "unknown",
    build: health?.build,
    user_agent: navigator.userAgent,
    ...route,
    viewport,
    locale: navigator.language,
  };
}
