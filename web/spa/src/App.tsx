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
import { useTheme } from "./theme";

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
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const timer = useRef<number | null>(null);

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
    getMe()
      .then(setMe)
      .catch(() => setMe(null));
  }, []);

  useEffect(() => {
    void refresh();
    timer.current = window.setInterval(() => void refresh(), POLL_MS);
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current);
    };
  }, [refresh]);

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

  const visible = useMemo(() => jobs.filter((j) => jobMatchesFilter(j, filter)), [jobs, filter]);
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

  const copy = EMPTY_COPY[filter];

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
