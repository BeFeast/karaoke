import type { JobOut, JobStatus } from "./api";

export type ChipKind = "ok" | "err" | "run" | "info" | "neutral";

export interface StatusMeta {
  label: string;
  chip: ChipKind;
  /** Show a spinning glyph (work actively running on the GPU/coordinator). */
  spinner: boolean;
  /** In-flight: render the progress bar. */
  active: boolean;
  /** Human-readable stage note shown beside the bar. */
  note: string | null;
}

const META: Record<JobStatus, StatusMeta> = {
  queued: { label: "queued", chip: "info", spinner: false, active: true, note: "Waiting in queue…" },
  downloading: { label: "downloading", chip: "run", spinner: true, active: true, note: "Downloading audio…" },
  separating: { label: "separating", chip: "run", spinner: true, active: true, note: "Separating vocals…" },
  transcribing: { label: "transcribing", chip: "run", spinner: true, active: true, note: "Transcribing lyrics…" },
  completed: { label: "completed", chip: "ok", spinner: false, active: false, note: null },
  failed: { label: "failed", chip: "err", spinner: false, active: false, note: null },
  cancelled: { label: "cancelled", chip: "neutral", spinner: false, active: false, note: "Cancelled" },
};

const FALLBACK: StatusMeta = { label: "unknown", chip: "neutral", spinner: false, active: false, note: null };

export function statusMeta(status: JobStatus): StatusMeta {
  return META[status] ?? FALLBACK;
}

// Dashboard status filters (ex-Sidebar logic, relocated here when the booth
// port replaced the sidebar with filter chips — #153).
export type JobFilter = "all" | "active" | "completed" | "failed";

export function jobMatchesFilter(job: JobOut, filter: JobFilter): boolean {
  switch (filter) {
    case "active":
      return statusMeta(job.status).active;
    case "completed":
      return job.status === "completed";
    case "failed":
      return job.status === "failed";
    default:
      return true;
  }
}

/** Per-filter counts for the chip badges. */
export function jobCounts(jobs: JobOut[]): Record<JobFilter, number> {
  return {
    all: jobs.length,
    active: jobs.filter((j) => statusMeta(j.status).active).length,
    completed: jobs.filter((j) => j.status === "completed").length,
    failed: jobs.filter((j) => j.status === "failed").length,
  };
}

/** Short host label for a source URL, e.g. "youtube.com". */
export function sourceLabel(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

// Same-origin links. We deliberately do NOT use job.share_url (the public
// https://karaoke.oklabs.uk/... base) — on the LAN that 401s through the CF
// tunnel; the SPA is served from the same host as these routes, so relative
// paths open correctly. (Real "sharing" only matters once auth is wired.)
export function resultHref(token: string): string {
  return `/share/${token}`;
}

export function artifactHref(token: string, name: string): string {
  return `/share/${token}/${name}`;
}

/** Downloadable outputs of a completed job (server allowlist names). */
export const ARTIFACTS: { name: string; label: string }[] = [
  { name: "karaoke.mp3", label: "instrumental" },
  { name: "vocals.mp3", label: "vocals" },
  { name: "lyrics.txt", label: "lyrics" },
];

// Human labels keyed by the server artifact `kind` (see worker_stub._MOCK_ARTIFACTS
// and routes._render_share_html: kinds are "karaoke", "vocals", "lyrics").
const ARTIFACT_KIND_LABEL: Record<string, string> = {
  karaoke: "instrumental",
  vocals: "vocals",
  lyrics: "lyrics",
};

export interface ArtifactView {
  /** Server `kind`, e.g. "karaoke" / "vocals" / "lyrics". */
  kind: string;
  /** Human label for buttons/cards. */
  label: string;
  /** Same-origin download/stream URL: /share/{token}/{basename}. */
  href: string;
  /** Bare allowlisted filename, e.g. "karaoke.mp3". */
  name: string;
  /** True when the artifact is audio and should get an <audio> player. */
  isAudio: boolean;
}

/**
 * Resolve a SharePayload artifact into a renderable view.
 *
 * The payload's `relative_path` is `{token}/{name}`; the share-artifact
 * endpoint expects only the bare allowlisted `{name}` (it always reads from
 * `exports/`), so we link by basename, never by the raw relative path.
 */
export function artifactView(
  token: string,
  artifact: { kind: string; relative_path: string; content_type: string | null },
): ArtifactView {
  const name = artifact.relative_path.split("/").pop() || artifact.relative_path;
  const label = ARTIFACT_KIND_LABEL[artifact.kind] ?? artifact.kind;
  const isAudio = (artifact.content_type ?? "").startsWith("audio/") || name.endsWith(".mp3");
  return { kind: artifact.kind, label, href: artifactHref(token, name), name, isAudio };
}

/** A failed/cancelled job can be retried by resubmitting its source URL. */
export function canRetry(status: JobStatus): boolean {
  return status === "failed" || status === "cancelled";
}
