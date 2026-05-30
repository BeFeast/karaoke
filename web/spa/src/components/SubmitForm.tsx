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
    <form className="submit" onSubmit={handleSubmit}>
      <div className="row">
        <input
          type="url"
          required
          placeholder="YouTube / yt-dlp URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={busy}
        />
        <input
          type="text"
          placeholder="Title (optional)"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !url.trim()}>
          {busy ? "Submitting…" : "Submit"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
    </form>
  );
}
