import { useReducer, useState } from "react";
import { saveLyricsReview, type LyricsPayload, type LyricsQuality } from "../api";

export function qualityMessage(quality?: LyricsQuality | null): { title: string; detail: string; warning: boolean } {
  switch (quality?.status) {
    case "reviewed":
      return { title: "Lyrics reviewed", detail: "The owner confirmed the words and timing against the audio.", warning: false };
    case "checked":
      return { title: "Lyrics automatically checked", detail: "Automated checks passed. This is not a listening review.", warning: false };
    case "needs_review":
      return { title: "Lyrics need review", detail: "Words or timing may be incomplete. Listen as a preview until reviewed.", warning: true };
    default:
      return { title: "Lyrics not checked", detail: "These lyrics have no quality report. Words and timing may be incomplete.", warning: true };
  }
}

export function LyricsQualityBanner({ quality, compact = false }: { quality?: LyricsQuality | null; compact?: boolean }) {
  const message = qualityMessage(quality);
  return (
    <div role="status" style={{ padding: compact ? "8px 16px" : "12px 14px", border: "1px solid var(--border)", borderRadius: "var(--radius)", background: "var(--bg-card)", color: "var(--fg)", fontSize: compact ? 12 : 13, lineHeight: 1.5 }}>
      <strong>{message.title}</strong>
      {!compact && <div style={{ color: "var(--fg-soft)" }}>{message.detail}</div>}
    </div>
  );
}

export interface ReviewDraft { lrc: string; confirmed: boolean; saving: boolean; error: string | null }
export type ReviewAction = { type: "edit"; lrc: string } | { type: "confirm"; confirmed: boolean } | { type: "saving" } | { type: "saved" } | { type: "error"; error: string };
export function reviewReducer(state: ReviewDraft, action: ReviewAction): ReviewDraft {
  switch (action.type) {
    case "edit": return { ...state, lrc: action.lrc, confirmed: false, error: null };
    case "confirm": return { ...state, confirmed: action.confirmed };
    case "saved": return { ...state, saving: false, confirmed: false };
    case "saving": return { ...state, saving: true, error: null };
    case "error": return { ...state, saving: false, confirmed: false, error: action.error };
  }
}
export function canSaveReview(draft: ReviewDraft, revision?: string): boolean {
  return !!revision && !!draft.lrc.trim() && draft.confirmed && !draft.saving;
}

export function LyricsReview({ lyrics, onSaved, onSeek, onReload }: {
  lyrics: LyricsPayload;
  onSaved: (lyrics: LyricsPayload) => void;
  onSeek: (time: number) => void;
  onReload: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  // The API is the ownership authority. Public viewers get only the banner.
  if (lyrics.review_job_id == null || !lyrics.revision) return null;
  return <div>
    <button type="button" className="m-btn sm" onClick={() => setOpen(!open)} aria-expanded={open}>
      {open ? "Close lyrics review" : "Review / correct lyrics"}
    </button>
    {open && <LyricsReviewEditor key={lyrics.revision} lyrics={lyrics} onSaved={onSaved} onSeek={onSeek} onReload={onReload} />}
  </div>;
}

export function LyricsReviewEditor({ lyrics, onSaved, onSeek, onReload }: {
  lyrics: LyricsPayload;
  onSaved: (lyrics: LyricsPayload) => void;
  onSeek: (time: number) => void;
  onReload: () => Promise<void>;
}) {
  const [draft, dispatch] = useReducer(reviewReducer, { lrc: lyrics.lrc ?? lyrics.plain ?? "", confirmed: false, saving: false, error: null });
  const [saved, setSaved] = useState(false);
  const issues = lyrics.quality?.issues ?? [];
  const save = async () => {
    if (!canSaveReview(draft, lyrics.revision) || lyrics.review_job_id == null) return;
    dispatch({ type: "saving" });
    try {
      const updated = await saveLyricsReview(lyrics.review_job_id, draft.lrc, lyrics.revision!);
      dispatch({ type: "saved" });
      setSaved(true);
      onSaved(updated);
    } catch (error) {
      dispatch({ type: "error", error: error instanceof Error ? error.message : String(error) });
    }
  };
  const reload = async () => {
    try { await onReload(); }
    catch (error) { dispatch({ type: "error", error: error instanceof Error ? error.message : String(error) }); }
  };
  return (
    <section aria-label="Lyrics review" style={{ marginTop: 12, padding: 14, display: "grid", gap: 12, border: "1px solid var(--border)", borderRadius: "var(--radius)", background: "var(--bg-card)", minWidth: 0 }}>
      <h2 style={{ margin: 0, fontSize: 17 }}>Check words and timing</h2>
      <p style={{ margin: 0, color: "var(--fg-soft)", fontSize: 13 }}>Use the player above with vocals audible. Check the entire song, including repeated lines. Issue positions are suggested listening points, not confirmed timing.</p>
      {issues.length > 0 && <ul style={{ margin: 0, paddingLeft: 20, fontSize: 13 }}>
        {issues.map((issue, i) => <li key={i} style={{ marginBottom: 8, overflowWrap: "anywhere" }}>
          <span>{issue.detail || issue.code.replace(/_/g, " ")}{issue.text ? `: ${issue.text}` : ""}</span>{" "}
          {issue.start != null && Number.isFinite(issue.start) && <button type="button" className="m-btn sm" onClick={() => onSeek(Math.max(0, issue.start! - 2))}>Listen near {Math.floor(issue.start / 60)}:{String(Math.floor(issue.start % 60)).padStart(2, "0")}</button>}
        </li>)}
      </ul>}
      <label style={{ display: "grid", gap: 6, fontSize: 13 }}>
        Lyrics and timestamps (LRC)
        <textarea aria-label="Lyrics and timestamps (LRC)" value={draft.lrc} onChange={(e) => { setSaved(false); dispatch({ type: "edit", lrc: e.target.value }); }} disabled={draft.saving} rows={14} spellCheck={false} style={{ width: "100%", minWidth: 0, boxSizing: "border-box", resize: "vertical", fontFamily: "var(--font-mono)", fontSize: 13, lineHeight: 1.6, padding: 10, background: "var(--bg)", color: "var(--fg)", border: "1px solid var(--border)", borderRadius: "var(--radius)" }} />
      </label>
      <div style={{ fontSize: 12, color: "var(--fg-soft)" }}>One line per timestamp, for example <code>[01:23.45]Words sung here</code>. Keep repeated lines. Existing <code>&lt;01:23.45&gt;</code> tags mark individual words; update or remove those tags if you change their timing.</div>
      <label style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: 13 }}>
        <input type="checkbox" checked={draft.confirmed} disabled={draft.saving} onChange={(e) => dispatch({ type: "confirm", confirmed: e.target.checked })} />
        I listened to the entire song and checked every word, repeated line, and timestamp.
      </label>
      {draft.error && <div role="alert" style={{ color: "var(--err)", fontSize: 13, overflowWrap: "anywhere" }}>
        {/^(409|412)\b/.test(draft.error) ? "Lyrics changed since this review opened. Copy your edits before loading the latest version, then compare and review again." : "Could not save the review. Correct the problem below and try again."}
        <div>{draft.error}</div>
        {/^(409|412)\b/.test(draft.error) && <button type="button" className="m-btn sm" onClick={() => void reload()}>Load latest (replaces draft)</button>}
      </div>}
      {saved && <div role="status">Lyrics review saved.</div>}
      <button type="button" className="m-btn primary" disabled={!canSaveReview(draft, lyrics.revision)} onClick={() => void save()}>{draft.saving ? "Saving…" : "Save reviewed lyrics"}</button>
    </section>
  );
}
