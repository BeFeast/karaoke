import type { JobStatus } from "./api";

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

/** Short host label for a source URL, e.g. "youtube.com". */
export function sourceLabel(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
