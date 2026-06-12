// KARAOKE — the Booth: live dashboard on #/ (Marquee port, #153).
// Literal port of design/claude-export/proto/booth.jsx (BoothScreen :82-150,
// PRunningCard :58-80, infra strip :141-146) plus the filter-chip pattern from
// design/claude-export/sections/m-booth.jsx:105-115 and the select pattern
// from design/claude-export/proto/settings.jsx:38-47. The DOM tree and inline
// styles are the design's; the adaptations wire real data only: jobs/actions
// come from App.tsx, pipeline stage rows derive from status/progress/
// stage_note, costs derive from gpu_cost_micros, and the export's fictional
// copy (STAGE_DETAIL, cost caps, est-costs, teardown "destroyed ✓") is gone.
// Phone widths (#186) restack this shipped structure behind usePhoneLayout()
// — no m-* mobile booth exists in the export (m-booth.jsx is a desktop
// board), so these are responsive adaptations with existing tokens/recipes
// only; the desktop branches are the untouched port.

import { type CSSProperties, type ReactNode, useRef, useState } from "react";
import type { JobOut, JobStatus } from "../api";
import { canRetryJob, type JobFilter, statusMeta } from "../jobStatus";
import { costDollars, fmtDuration, formatRelativeTime, type JobSort } from "../lib/jobListUtils";
import { usePhoneLayout } from "../lib/layout";
import { sourceDisplay } from "../lib/source";
import { itemHash } from "../router";
import { MBulbs, MDuetWave, MicMark, MWipe } from "./marks";

export interface JobActions {
  onDelete: (job: JobOut) => void;
  onCancel: (job: JobOut) => void;
  onRetry: (job: JobOut) => void;
}

// Topbar composition shared by the booth and the item/settings shells
// (booth.jsx:91-97): mark + wordmark · spacer · authControl. The auth control
// is the LAN chip or the Clerk avatar menu, injected by the boot shell.
export function MarqueeTopBar({ authControl }: { authControl?: ReactNode }) {
  // Phone (#186): the row wraps instead of overflowing — same elements, same
  // order, tighter side padding; the fixed height becomes a minHeight so a
  // wrapped auth control stays visible. Shared with Settings (wave 4/4 gives
  // that page its own pass). Desktop branch byte-identical.
  const phone = usePhoneLayout();
  return (
    <div style={phone
      ? { display: "flex", alignItems: "center", gap: 12, padding: "8px 14px", minHeight: 56, borderBottom: "1px solid var(--border)", flexShrink: 0, flexWrap: "wrap" }
      : { display: "flex", alignItems: "center", gap: 12, padding: "0 24px", height: 56, borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
      <MicMark size={24} />
      <span style={{ fontFamily: "var(--font-display)", fontWeight: 650, fontSize: 17, letterSpacing: "-0.01em" }}>Karaoke</span>
      <span style={{ flex: 1 }}></span>
      {authControl}
    </div>
  );
}

// The real pipeline, in stage order, mapped to the design's marquee stage
// names — an explicit table so the rows derive from JobStatus instead of
// being hardcoded to one screenshot state. `queued` sits before the table
// (all rows todo); terminal states never render the running card.
const PIPELINE: { status: JobStatus; name: string }[] = [
  { status: "downloading", name: "fetch" },
  { status: "separating", name: "split" },
  { status: "transcribing", name: "lyrics" },
];

type StageState = "done" | "run" | "todo";

const FILTERS: { key: JobFilter; label: string }[] = [
  { key: "all", label: "Tonight" },
  { key: "active", label: "Active" },
  { key: "completed", label: "Ready" },
  { key: "failed", label: "Failed" },
];

// Inline status chip: design vocabulary for terminal states ("ready"), the
// statusMeta chip kind for color (.m-chip ok/run/err/info).
function jobChip(job: JobOut): { kind: string; label: string } {
  const meta = statusMeta(job.status);
  const stage = PIPELINE.find((s) => s.status === job.status);
  return {
    kind: meta.chip === "neutral" ? "" : meta.chip,
    label: job.status === "completed" ? "ready" : (stage?.name ?? meta.label),
  };
}

function PStageRow({ name, detail, state, pct }: { name: string; detail: string; state: StageState; pct: number }) {
  // minmax(0, 1fr) instead of the ported 1fr (#186): a long stage_note must
  // shrink-and-ellipsize, never widen the grid past the viewport — the
  // #51-era horizontal-overflow regression class.
  return (
    <div style={{ display: "grid", gridTemplateColumns: "58px minmax(0, 1fr) auto", gap: 12, alignItems: "baseline", padding: "6px 0", borderTop: "1px solid var(--border-soft)" }}>
      <span className="m-mono" style={{ fontSize: 11.5, fontWeight: 700, color: state === "todo" ? "var(--muted)" : "var(--fg)" }}>{name}</span>
      <span className="m-mono" style={{ fontSize: 11.5, color: "var(--muted)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {state === "run" ? <MWipe text={detail} pct={pct} size={11.5} /> : detail}
      </span>
      <span className="m-mono" style={{ fontSize: 11.5, color: state === "done" ? "var(--ok)" : state === "run" ? "var(--accent)" : "var(--muted)" }}>
        {state === "done" ? "✓" : state === "run" ? Math.round(pct) + "%" : "—"}
      </span>
    </div>
  );
}

function PRunningCard({ job, wsOpen, actions }: { job: JobOut; wsOpen: boolean; actions: JobActions }) {
  const chip = jobChip(job);
  const stageIdx = PIPELINE.findIndex((s) => s.status === job.status);
  const pct = Math.max(0, Math.min(100, Math.round(job.progress)));
  const cost = costDollars(job.gpu_cost_micros);
  const title = job.title?.trim() || sourceDisplay(job.source_url).label;
  return (
    <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "13px 16px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className={`m-chip ${chip.kind}`}><span className="m-dot"></span>{chip.label}</span>
        <span style={{ fontSize: 14, fontWeight: 650, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
        {cost && <span className="m-mono" style={{ fontSize: 12.5, color: "var(--accent)", fontWeight: 600 }}>{cost}</span>}
        {wsOpen && <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>live</span>}
        <button className="m-btn sm ghost" type="button" onClick={() => actions.onCancel(job)}>Cancel</button>
      </div>
      <div style={{ margin: "10px 0 0" }}>
        {PIPELINE.map((s, i) => {
          const state: StageState = stageIdx < 0 ? "todo" : i < stageIdx ? "done" : i === stageIdx ? "run" : "todo";
          const note = statusMeta(s.status).note ?? "";
          // The active stage's detail is the worker's stage_note, verbatim.
          const detail = state === "run" ? job.stage_note || note : note;
          return <PStageRow key={s.status} name={s.name} detail={detail} state={state} pct={pct} />;
        })}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--border-soft)" }}>
        <div className="m-wipebar" style={{ "--wipe": pct + "%", flex: 1, maxWidth: 220 } as CSSProperties}><i></i></div>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>{pct}%</span>
        <span style={{ flex: 1 }}></span>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>instance dies when the song's done</span>
      </div>
    </div>
  );
}

// Terminal rows: ready / failed / cancelled (booth.jsx:122-137).
function CompactRow({ job, actions }: { job: JobOut; actions: JobActions }) {
  // Phone (#186): the single ported row packed chip + title + meta + up to
  // four trailing controls + a fixed-width sparkline — at 390px the title's
  // minWidth: 0 ellipsis squeezed to ~nothing. Two lines instead: chip +
  // title, then meta + right-aligned actions. Desktop branch byte-identical.
  const phone = usePhoneLayout();
  const chip = jobChip(job);
  const source = sourceDisplay(job.source_url);
  const title = job.title?.trim() || source.label;
  const ready = job.status === "completed";
  const metaText = ready
    ? [fmtDuration(job.duration), formatRelativeTime(job.completed_at), costDollars(job.gpu_cost_micros)]
        .filter(Boolean)
        .join(" · ")
    : (formatRelativeTime(job.created_at) ?? "");
  // Shared between the two layouts so the controls can't drift apart.
  // Uploads have no external source to open, and no URL to resubmit —
  // the ↗ anchor and the Retry control are URL-job affordances (#173).
  const openOriginal = source.kind === "url" && (
    <a href={job.source_url} target="_blank" rel="noopener" title="Original video" className="m-btn sm ghost" style={{ textDecoration: "none" }}>↗</a>
  );
  const singAction = ready && <a className="m-btn sm primary" href={itemHash(job.job_token)} style={{ textDecoration: "none" }}>▸ Sing</a>;
  const retryAction = canRetryJob(job) && <button className="m-btn sm" type="button" onClick={() => actions.onRetry(job)}>↻ Retry</button>;
  const removeAction = <button className="m-btn sm ghost" type="button" title="Remove job + artifacts" onClick={() => actions.onDelete(job)}>✕</button>;
  const failedBox = job.status === "failed" && job.error && (
    <div className="m-mono" style={{ marginTop: 8, padding: "8px 10px", borderRadius: "var(--radius)", background: "color-mix(in oklab, var(--err) 9%, var(--bg))", border: "1px solid color-mix(in oklab, var(--err) 24%, transparent)", color: "var(--err)", fontSize: 11, lineHeight: 1.5, overflowWrap: "anywhere" }}>
      {job.error}
    </div>
  );
  if (phone) {
    // MDuetWave deliberately not rendered here — the m-booth.jsx CompactJob
    // sparkline is decorative and starves the title at 390px (recorded
    // adaptation decision, #186); the failed-error box spans the card.
    return (
      <div style={{ padding: "11px 16px", background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span className={`m-chip ${chip.kind}`}><span className="m-dot"></span>{chip.label}</span>
          <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 6 }}>
          <span className="m-mono" style={{ minWidth: 0, fontSize: 10.5, color: "var(--muted)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {metaText}
            {job.gpu_instance_id && <span> · receipt: #{job.gpu_instance_id}</span>}
          </span>
          <span style={{ flex: 1 }}></span>
          {openOriginal}
          {singAction}
          {retryAction}
          {removeAction}
        </div>
        {failedBox}
      </div>
    );
  }
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 16px", background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)" }}>
      <span className={`m-chip ${chip.kind}`}><span className="m-dot"></span>{chip.label}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</div>
        <div className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {metaText}
          {job.gpu_instance_id && <span> · receipt: #{job.gpu_instance_id}</span>}
        </div>
        {failedBox}
      </div>
      {openOriginal}
      {ready && <MDuetWave seed={job.id} w={100} h={22} />}
      {singAction}
      {retryAction}
      {removeAction}
    </div>
  );
}

// Extensions POST /jobs/upload accepts (mirror of the server allowlist).
// Deliberately no `audio/*`: it would invite .opus/.aac/.webm picks the
// server 415s anyway (#173).
const UPLOAD_ACCEPT = ".mp3,.m4a,.wav,.flac,.ogg";

// request() errors lead with the HTTP status ("413 …: <body>") — map the two
// upload-specific rejections to short readable strings (the raw body may be
// an HTML error page from the edge); everything else surfaces verbatim.
function uploadErrorText(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  if (raw.startsWith("413")) return "File too large — the server rejected it (413).";
  if (raw.startsWith("415")) return "Unsupported file type — use mp3, m4a, wav, flac or ogg (415).";
  return raw;
}

// Loading placeholder shaped like the compact rows — marquee bulbs, no spinner.
function BoothSkeleton() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }} aria-hidden>
      {[0, 1, 2].map((i) => (
        <div key={i} style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 16px", background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)" }}>
          <MBulbs n={3} lit={0} size={4} gap={5} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ height: 10, width: "55%", borderRadius: "var(--radius-sm)", background: "var(--bg-soft)" }}></div>
            <div style={{ height: 8, width: "35%", marginTop: 8, borderRadius: "var(--radius-sm)", background: "var(--bg-soft)" }}></div>
          </div>
        </div>
      ))}
    </div>
  );
}

function BoothEmpty({ title, sub }: { title: string; sub: string }) {
  return (
    <div style={{ textAlign: "center", padding: "48px 20px", color: "var(--muted)" }}>
      <div style={{ display: "flex", justifyContent: "center", marginBottom: 14 }}>
        <MBulbs n={7} lit={0} size={5} gap={8} />
      </div>
      <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: "var(--fg)", marginBottom: 5 }}>{title}</div>
      <div style={{ fontSize: 12.5 }}>{sub}</div>
    </div>
  );
}

export function BoothScreen({
  jobs,
  counts,
  filter,
  onFilter,
  query,
  onQuery,
  sort,
  onSort,
  loaded,
  listError,
  actionError,
  emptyTitle,
  emptySub,
  wsOpen,
  version,
  todaySpend,
  onCreate,
  onUpload,
  actions,
  onClearFailed,
  authControl,
  children,
}: {
  /** Filtered + sorted rows to render. */
  jobs: JobOut[];
  /** Per-filter counts over the full list (chip badges). */
  counts: Record<JobFilter, number>;
  filter: JobFilter;
  onFilter: (f: JobFilter) => void;
  query: string;
  onQuery: (q: string) => void;
  sort: JobSort;
  onSort: (s: JobSort) => void;
  loaded: boolean;
  listError: string | null;
  actionError: string | null;
  emptyTitle: string;
  emptySub: string;
  wsOpen: boolean;
  /** Running version from GET /health, or null while loading. */
  version: string | null;
  /** Σ gpu_cost_micros over today's jobs (client-side). */
  todaySpend: number;
  onCreate: (url: string) => Promise<void>;
  /** Upload a local audio file — same receipt path as onCreate (#173). */
  onUpload: (file: File) => Promise<unknown>;
  actions: JobActions;
  onClearFailed: () => void;
  authControl?: ReactNode;
  /** Overlays (ConfirmDialog) rendered inside the booth's token scope. */
  children?: ReactNode;
}) {
  // Phone (#186): the submit console stacks — input on its own full-width
  // row, then the two actions side by side. Live matchMedia so rotation
  // re-lays-out. Desktop branch byte-identical.
  const phone = usePhoneLayout();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const doSubmit = () => {
    const trimmed = url.trim();
    if (!trimmed || busy || uploading) return;
    setBusy(true);
    setSubmitError(null);
    onCreate(trimmed)
      .then(() => setUrl(""))
      .catch((err) => setSubmitError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(false));
  };
  // One interaction: picking a file uploads it immediately. Indeterminate
  // busy state on purpose — fetch exposes no upload-progress events.
  const doUpload = (file: File) => {
    if (busy || uploading) return;
    setUploading(true);
    setSubmitError(null);
    onUpload(file)
      .catch((err) => setSubmitError(uploadErrorText(err)))
      .finally(() => {
        // Always clear the picker so re-selecting the same file fires onChange.
        if (fileRef.current) fileRef.current.value = "";
        setUploading(false);
      });
  };

  // Submit-console controls, shared by both layouts so they can't drift.
  // The input keeps the ported var(--font-mono) / 10px 13px / 14px verbatim;
  // on phone it drops flex: 1 (a column child stretches full width instead)
  // and the primary action grows to fill its row.
  const urlInput = (
    <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Paste a YouTube link…"
      onKeyDown={(e) => e.key === "Enter" && doSubmit()}
      disabled={busy || uploading} autoComplete="off" spellCheck={false} aria-label="Source URL"
      style={{ flex: phone ? undefined : 1, minWidth: 0, padding: "10px 13px", border: "1px solid var(--border)", borderRadius: "var(--radius)", background: "var(--bg)", color: "var(--fg)", fontSize: 14, fontFamily: "var(--font-mono)", outline: "none" }}></input>
  );
  const submitAction = (
    <button className="m-btn primary" type="button" onClick={doSubmit} disabled={busy || uploading || !url.trim()} style={{ fontSize: 14, padding: "8px 18px", flex: phone ? 1 : undefined }}>
      {busy ? "Staging…" : "Put it on stage"}
    </button>
  );
  const uploadAction = (
    <button className="m-btn ghost" type="button" onClick={() => fileRef.current?.click()} disabled={busy || uploading} style={{ fontSize: 14, padding: "8px 14px", whiteSpace: "nowrap" }}>
      {uploading ? "Uploading…" : "Upload audio"}
    </button>
  );
  const filePicker = (
    <input ref={fileRef} type="file" accept={UPLOAD_ACCEPT} style={{ display: "none" }}
      onChange={(e) => {
        const file = e.currentTarget.files?.[0];
        if (file) doUpload(file);
      }} />
  );

  return (
    <div className="m-booth" style={{ minHeight: "100%", display: "flex", flexDirection: "column" }}>
      <MarqueeTopBar authControl={authControl} />

      <div style={{ flex: 1, padding: "24px 24px 16px", maxWidth: 780, width: "100%", margin: "0 auto", display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)", padding: 16 }}>
          {phone ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {urlInput}
              <div style={{ display: "flex", gap: 10 }}>
                {submitAction}
                {uploadAction}
              </div>
              {filePicker}
            </div>
          ) : (
            <div style={{ display: "flex", gap: 10 }}>
              {urlInput}
              {submitAction}
              {uploadAction}
              {filePicker}
            </div>
          )}
          <div className="m-mono" style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 11, color: "var(--muted)", alignItems: "center", flexWrap: "wrap" }}>
            <span>one link →</span>
            <span className="m-stem vox">vocals.mp3</span>
            <span className="m-stem inst">karaoke.mp3</span>
            <span style={{ color: "var(--fg-soft)" }}>≡ lyrics.lrc</span>
          </div>
          {submitError && (
            <div className="m-mono" style={{ marginTop: 8, fontSize: 11.5, color: "var(--err)", overflowWrap: "anywhere" }}>{submitError}</div>
          )}
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 4, margin: "20px 0 11px", flexWrap: "wrap" }}>
          {FILTERS.map((f) => {
            const on = filter === f.key;
            return (
              <button key={f.key} type="button" className="m-btn sm" onClick={() => onFilter(f.key)} style={{
                border: "1px solid " + (on ? "var(--accent)" : "transparent"),
                background: on ? "var(--accent-soft)" : "transparent",
                color: on ? "var(--accent)" : "var(--fg-soft)", fontWeight: on ? 650 : 500,
              }}>{f.label} <span className="m-mono" style={{ fontSize: 10.5, opacity: 0.75 }}>{counts[f.key]}</span></button>
            );
          })}
          <span style={{ flex: 1 }}></span>
          {counts.failed > 0 && (
            <button className="m-btn sm ghost" type="button" onClick={onClearFailed}>Clear failed</button>
          )}
        </div>

        <div style={{ display: "flex", gap: 8, marginBottom: 11 }}>
          <input type="search" value={query} onChange={(e) => onQuery(e.target.value)}
            placeholder="Search title, artist or URL…" aria-label="Search jobs"
            style={{ flex: 1, minWidth: 0, padding: "7px 11px", border: "1px solid var(--border)", borderRadius: "var(--radius)", background: "var(--bg)", color: "var(--fg)", fontSize: 12.5, fontFamily: "var(--font-mono)", outline: "none" }} />
          <select value={sort} onChange={(e) => onSort(e.target.value as JobSort)} aria-label="Sort jobs" style={{
            appearance: "none", padding: "7px 28px 7px 11px", border: "1px solid var(--border)", borderRadius: "var(--radius)",
            background: "var(--bg-card)", color: "var(--fg)", fontSize: 12.5, fontFamily: "var(--font-mono)", cursor: "pointer",
            backgroundImage: "linear-gradient(45deg, transparent 50%, var(--muted) 50%), linear-gradient(135deg, var(--muted) 50%, transparent 50%)",
            backgroundPosition: "calc(100% - 14px) 55%, calc(100% - 9px) 55%", backgroundSize: "5px 5px", backgroundRepeat: "no-repeat",
          }}>
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="title">Title A–Z</option>
          </select>
        </div>

        {listError && (
          <div className="m-mono" style={{ marginBottom: 10, fontSize: 11.5, color: "var(--err)", overflowWrap: "anywhere" }}>Couldn’t load jobs: {listError}</div>
        )}
        {actionError && (
          <div className="m-mono" style={{ marginBottom: 10, fontSize: 11.5, color: "var(--err)", overflowWrap: "anywhere" }}>Action failed: {actionError}</div>
        )}

        {!loaded && jobs.length === 0 ? (
          <BoothSkeleton />
        ) : jobs.length === 0 ? (
          <BoothEmpty title={emptyTitle} sub={emptySub} />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {jobs.map((j) =>
              statusMeta(j.status).active ? (
                <PRunningCard key={j.id} job={j} wsOpen={wsOpen} actions={actions} />
              ) : (
                <CompactRow key={j.id} job={j} actions={actions} />
              ),
            )}
          </div>
        )}

        <div className="m-mono" style={{ marginTop: "auto", paddingTop: 16, display: "flex", gap: 16, fontSize: 11, color: "var(--muted)", borderTop: "1px dashed var(--border-soft)", alignItems: "center" }}>
          <span><span style={{ color: wsOpen ? "var(--ok)" : "var(--err)" }}>{wsOpen ? "●" : "○"}</span> ws {wsOpen ? "live" : "down"}</span>
          <span>today {costDollars(todaySpend) ?? "$0.00"}</span>
          {version && <span style={{ marginLeft: "auto" }}>karaoke v{version}</span>}
        </div>
      </div>
      {children}
    </div>
  );
}
