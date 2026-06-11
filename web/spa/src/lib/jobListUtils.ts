// Pure dashboard list helpers — no React, no side effects. Everything here is
// typecheck-gated and trivially unit-testable: plain functions over the fetched
// JobOut list. All of them tolerate null/undefined/unparseable timestamps
// without throwing — a bad value renders as a fallback, never crashes the tree
// (the /health+/jobs deploy canary does not load /app, so a render error would
// ship invisibly).

import type { JobOut } from "../api";

export type JobSort = "newest" | "oldest" | "title";

/** Parse an ISO timestamp to epoch ms, or null when missing/unparseable. */
function parseTime(value: string | null | undefined): number | null {
  if (!value) return null;
  const ms = Date.parse(value);
  return Number.isNaN(ms) ? null : ms;
}

/**
 * Case-insensitive substring filter over title / artist / track / source_url,
 * applied client-side on the already-fetched list. A blank query returns the
 * input list unchanged.
 */
export function filterJobs(jobs: JobOut[], query: string): JobOut[] {
  const q = query.trim().toLowerCase();
  if (!q) return jobs;
  return jobs.filter((job) =>
    [job.title, job.artist, job.track, job.source_url].some(
      (v) => typeof v === "string" && v.toLowerCase().includes(q),
    ),
  );
}

/**
 * Return a sorted copy (never mutates the input array).
 *
 * - "newest" / "oldest": by created_at; jobs with a missing/unparseable
 *   timestamp sort last in either direction.
 * - "title": locale-aware A–Z; jobs with no (or blank) title sort last.
 *
 * Array.prototype.sort is stable, so equal keys keep their fetch order.
 */
export function sortJobs(jobs: JobOut[], sort: JobSort): JobOut[] {
  const copy = [...jobs];
  if (sort === "title") {
    copy.sort((a, b) => {
      const ta = a.title?.trim() || null;
      const tb = b.title?.trim() || null;
      if (ta === null && tb === null) return 0;
      if (ta === null) return 1;
      if (tb === null) return -1;
      return ta.localeCompare(tb, undefined, { sensitivity: "base" });
    });
    return copy;
  }
  const dir = sort === "oldest" ? 1 : -1;
  copy.sort((a, b) => {
    const ma = parseTime(a.created_at);
    const mb = parseTime(b.created_at);
    if (ma === null && mb === null) return 0;
    if (ma === null) return 1;
    if (mb === null) return -1;
    return (ma - mb) * dir;
  });
  return copy;
}

/**
 * Hand-rolled relative timestamp (zero npm deps): "just now", "5 min ago",
 * "2 h ago", "3 d ago"; past 7 days falls back to a short locale date
 * ("Jun 4", plus the year when it differs from today's). Returns null for
 * missing/unparseable input so callers can simply skip rendering.
 */
export function formatRelativeTime(
  value: string | null | undefined,
  nowMs: number = Date.now(),
): string | null {
  const ms = parseTime(value);
  if (ms === null) return null;
  const elapsed = nowMs - ms;
  // Sub-minute (and small clock skews into the future) read as "just now".
  if (elapsed < 60_000) return "just now";
  const min = Math.floor(elapsed / 60_000);
  if (min < 60) return `${min} min ago`;
  const h = Math.floor(min / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.floor(h / 24);
  if (d <= 7) return `${d} d ago`;
  const then = new Date(ms);
  const sameYear = then.getFullYear() === new Date(nowMs).getFullYear();
  return then.toLocaleDateString(
    undefined,
    sameYear ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" },
  );
}
