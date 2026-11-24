import { createContext, useContext, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { flushSync } from "react-dom";
import { getConfig } from "../api";
import { feedbackInstallId, feedbackLog, feedbackMetadata, redactFeedbackText } from "./diagnostics";

const DEFAULT_BASE = "https://pomoi.co";
const OpenFeedback = createContext<((button: HTMLButtonElement) => void) | null>(null);
type Screenshot = Awaited<ReturnType<typeof import("./screenshot")["captureViewport"]>>;
type Receipt = { number: number; url: string };

/** Open the feedback sheet from any control (the Stage ⋯ menu). */
export function useOpenFeedback(): ((button: HTMLButtonElement) => void) | null {
  return useContext(OpenFeedback);
}

export function FeedbackButton() {
  const open = useContext(OpenFeedback);
  return (
    <button type="button" className="m-btn ghost feedback-trigger"
      onClick={(event) => open?.(event.currentTarget)}>
      Feedback
    </button>
  );
}

export function FeedbackProvider({ baseUrl, children }: { baseUrl?: string; children: ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);
  const metadata = useRef<ReturnType<typeof feedbackMetadata>>();
  const screenshot = useRef<Screenshot | null>(null);
  const sending = useRef(false);
  const titleId = useId();
  const [night, setNight] = useState(false);
  const [thumbnail, setThumbnail] = useState<string>();
  const [message, setMessage] = useState("");
  const [kind, setKind] = useState<"bug" | "fr">("bug");
  const [photo, setPhoto] = useState<File | null>(null);
  const [includeLog, setIncludeLog] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [receipt, setReceipt] = useState<Receipt | null>(null);

  async function open(button: HTMLButtonElement) {
    if (trigger.current || !dialog.current) return;
    trigger.current = button;
    button.setAttribute("aria-busy", "true");
    setNight(button.closest(".m-booth, .m-stage")?.classList.contains("m-stage") ?? false);
    metadata.current = feedbackMetadata();
    let preview: string | undefined;
    try {
      const { captureViewport } = await import("./screenshot");
      screenshot.current = await captureViewport();
      preview = screenshot.current.toDataURL("image/png");
    } catch {
      screenshot.current = null;
    }
    try {
      // Commit the preview before showing the dialog so Send is immediately stable.
      flushSync(() => setThumbnail(preview));
      if (!dialog.current) {
        reset();
        return;
      }
      dialog.current.showModal();
      input.current?.focus();
    } catch {
      reset();
    } finally {
      button.removeAttribute("aria-busy");
    }
  }

  function reset() {
    trigger.current?.focus({ preventScroll: true });
    trigger.current = null;
    screenshot.current = null;
    setThumbnail(undefined);
    setMessage("");
    setPhoto(null);
    setKind("bug");
    setIncludeLog(true);
    setError("");
    setReceipt(null);
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (sending.current || !metadata.current) return;
    sending.current = true;
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.set("text", redactFeedbackText(message));
      form.set("kind", kind);
      form.set("install_id", feedbackInstallId());
      form.set("meta", JSON.stringify(await metadata.current));
      if (includeLog) form.set("log", feedbackLog());

      if (screenshot.current || photo) {
        try {
          const { feedbackPng } = await import("./screenshot");
          const png = await feedbackPng(screenshot.current, photo);
          if (png) form.set("screenshot", png, "feedback.png");
        } catch {
          setError(photo ? "Could not attach this photo. Choose another image or remove it and retry."
            : "Could not attach the screenshot. Remove it and retry.");
          return;
        }
      }

      // Public share pages bootstrap without /config. Resolve their override
      // before sending so a config failure cannot send to the wrong endpoint.
      const configured = baseUrl ?? (await getConfig()).feedback_base_url;
      const response = await fetch(`${(configured || DEFAULT_BASE).replace(/\/+$/, "")}/api/feedback/karaoke`, {
        method: "POST",
        body: form,
        credentials: "omit",
        redirect: "error",
        referrerPolicy: "no-referrer",
        signal: AbortSignal.timeout(40_000),
      });
      if (response.status !== 201) throw new Error("Feedback was rejected");
      const { issue_number, issue_url } = await response.json();
      const url = new URL(issue_url);
      if (!Number.isSafeInteger(issue_number) || issue_number <= 0 || url.protocol !== "https:") {
        throw new Error("Invalid feedback receipt");
      }
      setReceipt({ number: issue_number, url: url.href });
    } catch {
      setError("Could not send feedback. Your draft is still here. Please try again.");
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  return (
    <OpenFeedback.Provider value={open}>
      {children}
      <dialog ref={dialog} className={`feedback-sheet ${night ? "m-stage" : "m-booth"}`}
        aria-labelledby={titleId} data-feedback-private
        onCancel={(event) => { if (sending.current) event.preventDefault(); }}
        onClose={reset}>
        <div className="feedback-heading">
          <h2 id={titleId}>Feedback</h2>
          <button type="button" className="m-btn ghost feedback-close" aria-label="Close feedback"
            disabled={busy} onClick={() => dialog.current?.close()}>×</button>
        </div>
        {receipt ? (
          <p role="status">Feedback sent. <a href={receipt.url} target="_blank" rel="noopener noreferrer">
            Issue #{receipt.number}
          </a></p>
        ) : (
          <form className="feedback-form" onSubmit={send}>
            <textarea ref={input} aria-label="Feedback message" placeholder="What happened? (optional)"
              rows={2} maxLength={10000} disabled={busy} value={message}
              onChange={(event) => setMessage(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing
                    && !window.matchMedia("(pointer: coarse)").matches) {
                  event.preventDefault();
                  event.currentTarget.form?.requestSubmit();
                }
              }} />
            <div className="feedback-options" role="group" aria-label="Feedback type">
              <button type="button" className={`m-btn ${kind === "bug" ? "primary" : ""}`}
                aria-pressed={kind === "bug"} disabled={busy} onClick={() => setKind("bug")}>Bug</button>
              <button type="button" className={`m-btn ${kind === "fr" ? "primary" : ""}`}
                aria-pressed={kind === "fr"} disabled={busy} onClick={() => setKind("fr")}>Idea</button>
            </div>
            {thumbnail && (
              <div className="feedback-attachment">
                <img src={thumbnail} alt="Screenshot from before Feedback opened" />
                <button type="button" className="m-btn sm ghost" disabled={busy}
                  onClick={() => { screenshot.current = null; setThumbnail(undefined); }}>Remove screenshot</button>
              </div>
            )}
            <div className="feedback-attachment">
              <label className="m-btn ghost feedback-photo">
                {photo ? photo.name : "Attach photo"}
                <input type="file" accept="image/*" aria-label="Attach photo" disabled={busy}
                  onChange={(event) => {
                    const file = event.currentTarget.files?.[0];
                    if (file) setPhoto(file);
                    event.currentTarget.value = "";
                  }} />
              </label>
              {photo && <button type="button" className="m-btn sm ghost" disabled={busy}
                onClick={() => setPhoto(null)}>Remove photo</button>}
            </div>
            <label className="feedback-logs">
              <input type="checkbox" checked={includeLog} disabled={busy}
                onChange={(event) => setIncludeLog(event.currentTarget.checked)} />
              Include private diagnostic logs
            </label>
            {error && <p className="feedback-error" role="alert">{error}</p>}
            <button className="m-btn primary feedback-send" type="submit" disabled={busy}>
              {busy ? "Sending…" : "Send"}
            </button>
          </form>
        )}
      </dialog>
    </OpenFeedback.Provider>
  );
}
