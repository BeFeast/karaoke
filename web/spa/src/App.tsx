import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getMe, type JobOut, listJobs, type MeOut, type RuntimeConfig } from "./api";
import { JobList } from "./components/JobList";
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
  completed: { title: "No completed jobs", sub: "Finished jobs and their share links land here." },
  failed: { title: "No failed jobs", sub: "Nothing has failed — that's the good kind of empty." },
};

export function App({
  config,
  authControl,
}: {
  config: RuntimeConfig;
  // The auth-control node from the boot shell: a LAN chip, or Clerk's UserButton.
  authControl?: ReactNode;
}) {
  const [theme, toggleTheme] = useTheme();
  const [me, setMe] = useState<MeOut | null>(null);
  const [jobs, setJobs] = useState<JobOut[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [filter, setFilter] = useState<JobFilter>("all");
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

  const onCreated = useCallback(() => {
    setFilter("all"); // make the freshly-queued job visible regardless of current filter
    void refresh();
  }, [refresh]);

  const visible = useMemo(() => jobs.filter((j) => jobMatchesFilter(j, filter)), [jobs, filter]);
  const activeCount = useMemo(() => jobs.filter((j) => statusMeta(j.status).active).length, [jobs]);

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
          </div>

          <SubmitForm onCreated={onCreated} />

          {listError && (
            <div className="form-error" style={{ marginBottom: "16px" }}>
              Couldn’t load jobs: {listError}
            </div>
          )}

          <JobList
            jobs={visible}
            loading={!loaded}
            publicBaseUrl={config.public_base_url}
            emptyTitle={copy.title}
            emptySub={copy.sub}
          />
        </div>
      </main>
    </div>
  );
}
