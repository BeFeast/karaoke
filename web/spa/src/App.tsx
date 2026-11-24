import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  cancelJob,
  clearFailedJobs,
  createJob,
  deleteJob,
  type JobOut,
  listJobs,
  type RuntimeConfig,
  uploadJob,
} from "./api";
import { AddSong } from "./components/AddSong";
import { BoothScreen, type JobActions } from "./components/Booth";
import { CatalogBrowser } from "./components/Catalog";
import { ConfirmDialog, type ConfirmState } from "./components/ConfirmDialog";
import { Shell } from "./components/Shell";
import { FeedbackButton } from "./feedback/Feedback";
import { t } from "./i18n";
import { canRetryJob, jobCounts, type JobFilter, jobMatchesFilter } from "./jobStatus";
import { filterJobs, type JobSort, sortJobs } from "./lib/jobListUtils";
import { addHash, type CatalogShelf, navigate } from "./router";
import { connectJobSocket, isTerminal, type JobEvent } from "./ws";

const POLL_MS = 3000;

const EMPTY_COPY: Record<JobFilter, { title: string; sub: string }> = {
  all: {
    title: "No songs yet",
    sub: "Tap Add song to prepare your first one.",
  },
  active: { title: "Nothing in flight", sub: "Submitted jobs will show their progress here." },
  completed: { title: "No completed jobs", sub: "Finished jobs and their players land here." },
  failed: { title: "No failed jobs", sub: "Nothing has failed — that's the good kind of empty." },
};

export function App(props: {
  // config is still part of the boot contract (clerk_enabled is read in main.tsx);
  // the dashboard itself no longer needs it now that links are same-origin.
  config: RuntimeConfig;
  // The auth-control node from the boot shell: a LAN chip, or the Clerk avatar.
  authControl?: ReactNode;
  catalogEnabled?: boolean;
  view: "my-songs" | "catalog" | "add";
  shelf?: CatalogShelf;
  /** #/add?url=… prefill (iOS Shortcut, share target). */
  addUrl?: string;
}) {
  const { authControl, catalogEnabled = false, view, shelf, addUrl = "" } = props;
  const [jobs, setJobs] = useState<JobOut[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [filter, setFilter] = useState<JobFilter>("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<JobSort>("newest");
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [wsOpen, setWsOpen] = useState(false);
  const [catalogQuery, setCatalogQuery] = useState("");
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

  const onCreate = useCallback(
    async (url: string) => {
      const job = await createJob({ url });
      setFilter("all"); // make the freshly-queued job visible regardless of current filter
      void refresh();
      return job;
    },
    [refresh],
  );

  // Audio-file upload (#173) — same receipt path as onCreate: the queued row
  // arrives via refresh and then updates over the WS flow, no new plumbing.
  const onUpload = useCallback(
    async (file: File) => {
      const job = await uploadJob(file);
      setFilter("all");
      void refresh();
      return job;
    },
    [refresh],
  );

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
      onRetry: (job) => {
        // The Booth hides Retry for upload:// jobs (no resubmittable URL —
        // POST /jobs 422s the sentinel by design); guard here too so no
        // caller can ever resubmit the sentinel (#173).
        if (!canRetryJob(job)) return;
        void runAction(async () => {
          await createJob({ url: job.source_url, title: job.title ?? undefined });
          setFilter("all");
        });
      },
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

  // Status filter (chips) → text search → sort. All client-side, all pure.
  const visible = useMemo(
    () => sortJobs(filterJobs(jobs.filter((j) => jobMatchesFilter(j, filter)), query), sort),
    [jobs, filter, query, sort],
  );
  const counts = useMemo(() => jobCounts(jobs), [jobs]);

  // A non-blank search that matches nothing gets its own empty copy — the
  // status-filter copy ("No jobs yet") would be misleading there.
  const searching = query.trim().length > 0;
  const copy = searching
    ? { title: "No matching jobs", sub: "Try a different search, or clear the query." }
    : EMPTY_COPY[filter];

  if (view === "add") {
    return <AddSong key={addUrl} initialUrl={addUrl} catalogEnabled={catalogEnabled} onCreate={onCreate} onUpload={onUpload} />;
  }

  const actionsBar = (
    <>
      <FeedbackButton />
      {authControl}
    </>
  );
  // Catalog prepare (one tap, no confirmation — Oleg 2026-11-24): the new job
  // shows up in My songs.
  const prepare = async (url: string) => {
    await onCreate(url);
    navigate("#/my-songs");
  };

  return (
    <Shell tab={view} title={view === "catalog" ? t("tab.catalog") : t("tab.mySongs")} actions={actionsBar}
      catalogEnabled={catalogEnabled}>
      {view === "catalog" ? (
        <div className="m-booth sn-legacy">
          <label className="sn-search">
            <span className="sn-visually-hidden">{t("catalog.search")}</span>
            <input className="catalog-search" type="search" value={catalogQuery} maxLength={200} dir="auto"
              onChange={(event) => setCatalogQuery(event.target.value)} placeholder={t("catalog.placeholder")} />
          </label>
          <CatalogBrowser query={catalogQuery} shelf={shelf ?? null} prepare={prepare} pasteUrl={() => navigate(addHash)} />
        </div>
      ) : (
        <BoothScreen
          jobs={visible}
          counts={counts}
          filter={filter}
          onFilter={setFilter}
          query={query}
          onQuery={setQuery}
          sort={sort}
          onSort={setSort}
          loaded={loaded}
          listError={listError}
          actionError={actionError}
          emptyTitle={copy.title}
          emptySub={copy.sub}
          actions={actions}
          onClearFailed={onClearFailed}
        />
      )}
      <ConfirmDialog state={confirmState} onClose={() => setConfirmState(null)} />
    </Shell>
  );
}
