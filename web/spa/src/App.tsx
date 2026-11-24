import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import {
  createJob,
  deleteJob,
  type JobOut,
  listJobs,
  type RuntimeConfig,
  uploadJob,
} from "./api";
import { AddSong } from "./components/AddSong";
import { CatalogBrowser } from "./components/Catalog";
import { ConfirmDialog, type ConfirmState } from "./components/ConfirmDialog";
import { Icon } from "./components/Icon";
import { MySongs } from "./components/MySongs";
import { Shell } from "./components/Shell";
import { FeedbackButton } from "./feedback/Feedback";
import { t } from "./i18n";
import { canRetryJob } from "./jobStatus";
import { forgetSong, loadLastPlayed } from "./lib/library";
import { addHash, type CatalogShelf, itemHash, navigate } from "./router";
import { connectJobSocket, isTerminal, type JobEvent } from "./ws";

const POLL_MS = 3000;

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
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [wsOpen, setWsOpen] = useState(false);
  const [catalogQuery, setCatalogQuery] = useState("");
  const [readyToast, setReadyToast] = useState<{ token: string; title: string } | null>(null);
  // Re-read on every visit to My songs: the Stage writes it while singing.
  const last = view === "my-songs" ? loadLastPlayed() : null;
  // Mirror of `jobs` for the WS handler — reading state there would force
  // the socket effect to re-run (and reconnect) on every list change.
  const jobsRef = useRef<JobOut[]>([]);

  const refresh = useCallback(async () => {
    try {
      const data = await listJobs(200);
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
        // Web toast: the song is ready while the singer is elsewhere (step 3).
        const was = jobsRef.current.find((j) => j.id === event.job_id);
        if (event.status === "completed" && was && was.status !== "completed") {
          setReadyToast({ token: was.job_token, title: was.title?.trim() || t("proc.toast") });
        }
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

  // Opening a shelf (See all, All shelves) starts from that shelf, not the
  // last search.
  const shelfKey = shelf ? `${shelf.axis}/${shelf.value}` : "";
  useEffect(() => setCatalogQuery(""), [shelfKey]);

  useEffect(() => {
    if (!readyToast) return;
    const id = window.setTimeout(() => setReadyToast(null), 8000);
    return () => window.clearTimeout(id);
  }, [readyToast]);

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
      void refresh();
      return job;
    },
    [refresh],
  );

  // Audio-file upload (#173) — same receipt path as onCreate.
  const onUpload = useCallback(
    async (file: File) => {
      const job = await uploadJob(file);
      void refresh();
      return job;
    },
    [refresh],
  );

  const onRemove = useCallback((job: JobOut) => {
    const title = job.title?.trim() || t("proc.toast");
    setConfirmState({
      title: t("home.removeTitle"),
      message: t("home.removeBody", { title }),
      confirmLabel: t("action.remove"),
      danger: true,
      onConfirm: () => {
        setJobs((js) => js.filter((j) => j.id !== job.id)); // optimistic
        forgetSong(job.job_token);
        void runAction(() => deleteJob(job.id));
      },
    });
  }, [runAction]);

  const onRetry = useCallback((job: JobOut) => {
    // Upload jobs have no resubmittable URL (#173).
    if (!canRetryJob(job)) return;
    void runAction(async () => {
      const fresh = await createJob({ url: job.source_url, title: job.title ?? undefined });
      navigate(itemHash(fresh.job_token));
    });
  }, [runAction]);

  if (view === "add") {
    return <AddSong key={addUrl} initialUrl={addUrl} catalogEnabled={catalogEnabled} onCreate={onCreate} onUpload={onUpload} />;
  }

  const actionsBar = (
    <>
      <FeedbackButton />
      {authControl}
    </>
  );

  return (
    <Shell tab={view} title={view === "catalog" ? t("tab.catalog") : t("tab.mySongs")} actions={actionsBar}
      catalogEnabled={catalogEnabled}>
      {view === "catalog" ? (
        <>
          <label className="sn-search-inline sn-catalog-search">
            <Icon name="search" />
            <span className="sn-visually-hidden">{t("catalog.search")}</span>
            <input className="catalog-search" type="search" value={catalogQuery} maxLength={200} dir="auto"
              autoComplete="off" onChange={(event) => setCatalogQuery(event.target.value)} placeholder={t("catalog.placeholder")} />
            {catalogQuery && (
              <button type="button" className="sn-iconbtn" aria-label={t("catalog.clear")} onClick={() => setCatalogQuery("")}>
                <Icon name="x" size={20} />
              </button>
            )}
          </label>
          {/* Prepare is one tap (Oleg, 2026-11-24): the card turns into Preparing. */}
          <CatalogBrowser query={catalogQuery} shelf={shelf ?? null} jobs={jobs} prepare={onCreate}
            onAdd={() => navigate(addHash)} />
        </>
      ) : (
        <MySongs jobs={jobs} loaded={loaded} listError={listError} actionError={actionError} last={last}
          catalogEnabled={catalogEnabled} onReload={() => void refresh()} onRetry={onRetry} onRemove={onRemove} />
      )}
      {readyToast && (
        <div className="sn-toast" role="status">
          <Icon name="check" size={20} />
          <span className="sn-grow"><bdi className="sn-t1">{readyToast.title}</bdi><span className="sn-t2">{t("proc.toast")}</span></span>
          <a className="sn-btn sn-btn-small sn-btn-primary" href={itemHash(readyToast.token)} onClick={() => setReadyToast(null)}>
            {t("action.sing")}
          </a>
        </div>
      )}
      <ConfirmDialog state={confirmState} onClose={() => setConfirmState(null)} />
    </Shell>
  );
}
