import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { getMe, type JobOut, listJobs, type MeOut, type RuntimeConfig } from "./api";
import { JobList } from "./components/JobList";
import { SubmitForm } from "./components/SubmitForm";

const POLL_MS = 3000;

export function App({
  config,
  headerExtra,
}: {
  config: RuntimeConfig;
  // Auth control rendered in the header (Clerk UserButton, or a LAN chip).
  headerExtra?: ReactNode;
}) {
  const [me, setMe] = useState<MeOut | null>(null);
  const [jobs, setJobs] = useState<JobOut[]>([]);
  const [listError, setListError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await listJobs();
      setJobs(data);
      setListError(null);
    } catch (err) {
      setListError(err instanceof Error ? err.message : String(err));
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

  return (
    <div className="container">
      <header className="app-header">
        <h1>Karaoke Submitter</h1>
        {headerExtra}
      </header>

      <div className="identity">
        {me ? (
          <>
            signed in as <strong>{me.email || me.subject}</strong>
            {me.is_admin && <span className="badge badge-admin">admin</span>}
          </>
        ) : (
          "resolving identity…"
        )}
      </div>

      <SubmitForm onCreated={refresh} />

      {listError && <div className="error">jobs: {listError}</div>}
      <JobList jobs={jobs} publicBaseUrl={config.public_base_url} />
    </div>
  );
}
