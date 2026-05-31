import { type FormEvent, useState } from "react";
import { createJob } from "../api";

export function SubmitForm({ onCreated }: { onCreated: () => void }) {
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!url.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await createJob({ url: url.trim(), title: title.trim() || undefined });
      setUrl("");
      setTitle("");
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="submit-card" onSubmit={handleSubmit}>
      <div className="submit-row">
        <input
          className="field field-url"
          type="url"
          required
          placeholder="Paste a YouTube / yt-dlp URL…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={busy}
          autoComplete="off"
          spellCheck={false}
        />
        <input
          className="field field-title"
          type="text"
          placeholder="Title (optional)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={busy}
        />
        <button type="submit" className="btn primary" disabled={busy || !url.trim()}>
          {busy ? (
            <>
              <span className="spinner" aria-hidden /> Submitting…
            </>
          ) : (
            "Submit"
          )}
        </button>
      </div>
      <p className="form-note">Splits vocals + instrumental and transcribes the lyrics.</p>
      {error && <div className="form-error">{error}</div>}
    </form>
  );
}
