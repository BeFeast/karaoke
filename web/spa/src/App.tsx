import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelJob,
  clearFailedJobs,
  createJob,
  deleteJob,
  getMe,
  type JobOut,
  listJobs,
  type MeOut,
  type RuntimeConfig,
} from "./api";
import { ConfirmDialog, type ConfirmState } from "./components/ConfirmDialog";
import { type JobActions, JobList } from "./components/JobList";
import { type JobFilter, jobMatchesFilter, Sidebar } from "./components/Sidebar";
import { SubmitForm } from "./components/SubmitForm";
import { TopBar } from "./components/TopBar";
import { statusMeta } from "./jobStatus";
import { filterJobs, type JobSort, sortJobs } from "./lib/jobListUtils";
import { useTheme } from "./theme";
import { connectJobSocket, isTerminal, type JobEvent } from "./ws";

const POLL_MS = 3000;

const EMPTY_COPY: Record<JobFilter, { title: string; sub: string }> = {
  all: {
    title: "No jobs yet",
    sub: "Paste a URL above to split vocals and transcribe the lyrics.",
  },
  active: { title: "Nothing in flight", sub: "Submitted jobs will show their progress here." },
  completed: { title: "No completed jobs", sub: "Finished jobs and their players land here." },
  failed: { title: "No failed jobs", sub: "Nothing has failed — that's the good kind of empty." },
};

export function App(props: {
  // config is still part of the boot contract (clerk_enabled is read in main.tsx);
  // the dashboard itself no longer needs it now that links are same-origin.
  config: RuntimeConfig;
  // The auth-control node from the boot shell: a LAN chip, or Clerk's UserButton.
  authControl?: ReactNode;
}) {
  const { authControl } = props;
  const [theme, toggleTheme] = useTheme();
  const [me, setMe] = useState<MeOut | null>(null);
  const [jobs, setJobs] = useState<JobOut[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [filter, setFilter] = useState<JobFilter>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<JobSort>("newest");
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [wsOpen, setWsOpen] = useState(false);
  // Mirror of `jobs` for the WS handler — reading state there would force
  // the socket effect to re-run (and reconnect) on every list change.
  const jobsRef = useRef<JobOut[]>([]);

  const refresh = useCallback(async () => {
    try {
      const data = await listJobs();
      setJobs(data);
      setListError(null);
    } catch (err) {
      setListError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    jobsRef.current = jobs;
  }, [jobs]);

  useEffect(() => {
    getMe()
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Merge one WS frame into the list in place. Frames carry only
  // status/progress/stage_note/error — never the full row — so unknown jobs
  // (submitted from another tab or the extension) and terminal transitions
  // (which add artifacts/completed_at) fall back to a one-shot refresh.
  const applyEvent = useCallback(
    (event: JobEvent) => {
      if (!jobsRef.current.some((j) => j.id === event.job_id)) {
        void refresh();
        return;
      }
      setJobs((js) =>
        js.map((j) => {
          if (j.id !== event.job_id) return j;
          // `finalizing` is WS-only (no DB enum value) — keep the persisted
          // status so the list always shows what a refresh would show.
          const status = event.status === "finalizing" ? j.status : event.status;
          if (event.type === "heartbeat") {
            return { ...j, status, progress: event.progress ?? j.progress };
          }
          return {
            ...j,
            status,
            progress: event.progress,
            stage_note: event.stage_note,
            error: event.error,
          };
        }),
      );
      if (event.type === "stage_change" && isTerminal(event.status)) {
        void refresh();
      }
    },
    [refresh],
  );

  // Live updates over WS /ws. On every (re)connect one refresh closes any
  // gap the socket missed while down; the client reconnects on its own with
  // capped exponential backoff.
  useEffect(
    () =>
      connectJobSocket({
        onEvent: applyEvent,
        onOpenChange: (open) => {
          setWsOpen(open);
          if (open) void refresh();
        },
      }),
    [applyEvent, refresh],
  );

  // Fallback polling: the pre-WS full-list refresh, now only while the
  // socket is down, at the original cadence.
  useEffect(() => {
    if (wsOpen) return;
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [wsOpen, refresh]);

  const runAction = useCallback(
    async (fn: () => Promise<unknown>) => {
      setActionError(null);
      try {
        await fn();
      } catch (err) {
        setActionError(err instanceof Error ? err.message : String(err));
      } finally {
        await refresh();
      }
    },
    [refresh],
  );

  const onCreated = useCallback(() => {
    setFilter("all"); // make the freshly-queued job visible regardless of current filter
    void refresh();
  }, [refresh]);

  const actions: JobActions = useMemo(
    () => ({
      onDelete: (job) => {
        setConfirmState({
          title: "Remove job",
          message: `Remove job #${job.id} and its artifacts? This can’t be undone.`,
          confirmLabel: "Remove",
          danger: true,
          onConfirm: () => {
            setJobs((js) => js.filter((j) => j.id !== job.id)); // optimistic
            void runAction(() => deleteJob(job.id));
          },
        });
      },
      onCancel: (job) => void runAction(() => cancelJob(job.id)),
      onRetry: (job) =>
        runAction(async () => {
          await createJob({ url: job.source_url, title: job.title ?? undefined });
          setFilter("all");
        }),
    }),
    [runAction],
  );

  const onClearFailed = useCallback(() => {
    const n = jobs.filter((j) => j.status === "failed").length;
    if (n === 0) return;
    setConfirmState({
      title: "Clear failed",
      message: `Remove all ${n} failed job${n === 1 ? "" : "s"} and their artifacts?`,
      confirmLabel: "Clear failed",
      danger: true,
      onConfirm: () => void runAction(() => clearFailedJobs()),
    });
  }, [jobs, runAction]);

  // Status filter (sidebar) → text search → sort. All client-side, all pure.
  const visible = useMemo(
    () => sortJobs(filterJobs(jobs.filter((j) => jobMatchesFilter(j, filter)), query), sort),
    [jobs, filter, query, sort],
  );
  const activeCount = useMemo(() => jobs.filter((j) => statusMeta(j.status).active).length, [jobs]);
  const failedCount = useMemo(() => jobs.filter((j) => j.status === "failed").length, [jobs]);

  const subtitle = `${jobs.length} ${jobs.length === 1 ? "job" : "jobs"}${
    activeCount > 0 ? ` · ${activeCount} active` : ""
  }`;

  const identity = (
    <>
      {me && <span className="identity-name">{me.email || me.subject}</span>}
      {me?.is_admin && <span className="chip admin">admin</span>}
      {authControl}
    </>
  );

  // A non-blank search that matches nothing gets its own empty copy — the
  // status-filter copy ("No jobs yet") would be misleading there.
  const searching = query.trim().length > 0;
  const copy = searching
    ? { title: "No matching jobs", sub: "Try a different search, or clear the query." }
    : EMPTY_COPY[filter];

  return (
    <div className="app">
      <TopBar theme={theme} onToggleTheme={toggleTheme} identity={identity} />
      <Sidebar jobs={jobs} filter={filter} onFilter={setFilter} />
      <main className="main">
        <div className="pane pane-narrow">
          <div className="pane-header">
            <div>
              <h1 className="pane-h1">Jobs</h1>
              <p className="pane-sub">{subtitle}</p>
            </div>
            {failedCount > 0 && (
              <div className="pane-actions">
                <button type="button" className="btn sm" onClick={onClearFailed}>
                  Clear failed ({failedCount})
                </button>
              </div>
            )}
          </div>

          <SubmitForm onCreated={onCreated} />

          <div className="list-toolbar">
            <input
              type="search"
              className="field field-search"
              placeholder="Search title, artist or URL…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Search jobs"
            />
            <select
              className="field field-sort"
              value={sort}
              onChange={(e) => setSort(e.target.value as JobSort)}
              aria-label="Sort jobs"
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="title">Title A–Z</option>
            </select>
          </div>

          {listError && (
            <div className="form-error" style={{ marginBottom: "16px" }}>
              Couldn’t load jobs: {listError}
            </div>
          )}
          {actionError && (
            <div className="form-error" style={{ marginBottom: "16px" }}>
              Action failed: {actionError}
            </div>
          )}

          <JobList
            jobs={visible}
            loading={!loaded}
            actions={actions}
            emptyTitle={copy.title}
            emptySub={copy.sub}
          />
        </div>
      </main>
      <ConfirmDialog state={confirmState} onClose={() => setConfirmState(null)} />
    </div>
  );
}
