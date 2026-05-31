import { type ReactNode, Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getLyricsText, getShare, type SharePayload } from "../api";
import { artifactView, statusMeta } from "../jobStatus";
import { goDashboard, itemUrl } from "../router";
import { useTheme } from "../theme";
import { StatusChip } from "./StatusChip";
import { Toast } from "./Toast";
import { TopBar } from "./TopBar";

// Lazy so wavesurfer.js (+ its WebAudio engine) only loads on the item route,
// keeping the dashboard bundle lean.
const KaraokePlayer = lazy(() => import("../player/KaraokePlayer"));

const POLL_MS = 3000;

// Wraps the item page in the standard topbar chrome + a centered pane with a
// "Back" affordance. Single-column layout (no dashboard sidebar).
function ItemShell({ authControl, children }: { authControl?: ReactNode; children: ReactNode }) {
  const [theme, toggleTheme] = useTheme();
  return (
    <div className="app app-item">
      <TopBar theme={theme} onToggleTheme={toggleTheme} identity={authControl} />
      <main className="main">
        <div className="pane pane-narrow">
          <button type="button" className="link-btn back-link" onClick={goDashboard}>
            ← Back to jobs
          </button>
          {children}
        </div>
      </main>
    </div>
  );
}

export function ItemPage({ token, authControl }: { token: string; authControl?: ReactNode }) {
  const [payload, setPayload] = useState<SharePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [lyrics, setLyrics] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await getShare(token);
      setPayload(data);
      setError(null);
      setNotFound(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // The endpoint 404s for unknown/unauthorised tokens.
      if (/^404\b/.test(msg)) setNotFound(true);
      else setError(msg);
    } finally {
      setLoaded(true);
    }
  }, [token]);

  // Initial load + poll while the job is still in-flight.
  useEffect(() => {
    setLoaded(false);
    setPayload(null);
    setNotFound(false);
    setError(null);
    setLyrics(null);
    void load();
  }, [load]);

  const active = payload ? statusMeta(payload.status).active : false;
  useEffect(() => {
    if (!active) {
      if (timer.current !== null) {
        window.clearInterval(timer.current);
        timer.current = null;
      }
      return;
    }
    timer.current = window.setInterval(() => void load(), POLL_MS);
    return () => {
      if (timer.current !== null) {
        window.clearInterval(timer.current);
        timer.current = null;
      }
    };
  }, [active, load]);

  // Fetch plain lyrics once a job is complete and a lyrics artifact exists.
  const hasLyrics = payload?.artifacts.some((a) => a.kind === "lyrics") ?? false;
  useEffect(() => {
    if (payload?.status === "completed" && hasLyrics) {
      getLyricsText(token)
        .then(setLyrics)
        .catch(() => setLyrics(null));
    }
  }, [payload?.status, hasLyrics, token]);

  const onShare = useCallback(async () => {
    const url = itemUrl(token);
    try {
      await navigator.clipboard.writeText(url);
      setToast("Link copied");
    } catch {
      // Clipboard API blocked (insecure context / permissions) — fall back.
      window.prompt("Copy this link:", url);
    }
  }, [token]);

  let content: ReactNode;

  if (!loaded) {
    content = (
      <div className="item-skeleton" aria-hidden>
        <span className="skel skel-line s" />
        <span className="skel skel-line l" />
        <span className="skel skel-line m" />
      </div>
    );
  } else if (notFound) {
    content = (
      <div className="empty">
        <div className="empty-title">Job not found</div>
        <div>This share link is invalid, expired, or you don’t have access to it.</div>
      </div>
    );
  } else if (error) {
    content = (
      <div className="empty">
        <div className="empty-title">Couldn’t load this job</div>
        <div className="error">{error}</div>
        <div style={{ marginTop: "12px" }}>
          <button type="button" className="btn sm" onClick={() => void load()}>
            ↻ Retry
          </button>
        </div>
      </div>
    );
  } else if (payload) {
    content = <ItemBody payload={payload} token={token} lyrics={lyrics} onShare={onShare} />;
  }

  return (
    <ItemShell authControl={authControl}>
      {content}
      <Toast message={toast} onDone={() => setToast(null)} />
    </ItemShell>
  );
}

function ItemBody({
  payload,
  token,
  lyrics,
  onShare,
}: {
  payload: SharePayload;
  token: string;
  lyrics: string | null;
  onShare: () => void;
}) {
  const meta = statusMeta(payload.status);
  const title = payload.title?.trim() || `Job ${payload.job_token.slice(0, 8)}`;
  const pct = Math.max(0, Math.min(100, Math.round(payload.progress)));
  const indeterminate = meta.active && pct <= 0;

  const views = payload.artifacts.map((a) => artifactView(token, a));
  const audio = views.filter((v) => v.isAudio);
  const downloads = views; // all artifacts are downloadable
  const isComplete = payload.status === "completed";

  // Resolve the two stems for the real player. `karaoke` = instrumental (the
  // master + waveform), `vocals` = the follower. We prefer kind, then fall back
  // to filename for older jobs whose artifacts lack a precise content_type.
  const instrumental = useMemo(
    () => audio.find((v) => v.kind === "karaoke") ?? audio.find((v) => v.name.startsWith("karaoke")) ?? null,
    [audio],
  );
  const vocals = useMemo(
    () => audio.find((v) => v.kind === "vocals") ?? audio.find((v) => v.name.startsWith("vocals")) ?? null,
    [audio],
  );

  return (
    <>
      <div className="item-header">
        <div className="item-meta-top">
          <StatusChip status={payload.status} />
          {payload.owner_display_name && (
            <span className="item-owner">· {payload.owner_display_name}</span>
          )}
        </div>
        <h1 className="pane-h1 item-title" title={title}>
          {title}
        </h1>

        {meta.active && (
          <>
            <div className={`progressbar active${indeterminate ? " indeterminate" : ""}`}>
              <div style={{ width: indeterminate ? undefined : `${pct}%` }} />
            </div>
            <div className="stage-note">
              {meta.note}
              {!indeterminate && ` · ${pct}%`}
            </div>
          </>
        )}

        {payload.status === "failed" && (
          <div className="job-error">
            <div className="err-label">failed</div>
            This job failed to process. Try resubmitting the source URL from the dashboard.
          </div>
        )}
      </div>

      <div className="item-actions">
        <button type="button" className="btn" onClick={onShare} title="Copy the share link">
          ⧉ Share
        </button>
        {isComplete &&
          downloads.map((v) => (
            <a key={v.name} className="btn sm" href={v.href} download>
              ↓ {v.label}
            </a>
          ))}
      </div>

      {/* Player slot — the real karaoke player (wavesurfer waveform + dual-stem
          transport). Falls back to bare <audio> only when no instrumental stem
          exists, and to an empty state when there's no audio at all. */}
      {isComplete && (
        <section className="players">
          <div className="sec-label">player</div>
          {instrumental ? (
            <Suspense fallback={<div className="player-card ksplayer-fallback" aria-hidden />}>
              <KaraokePlayer instrumentalUrl={instrumental.href} vocalsUrl={vocals?.href ?? null} />
            </Suspense>
          ) : audio.length > 0 ? (
            audio.map((v) => (
              <div className="player-card" key={v.name}>
                <div className="player-label">{v.label}</div>
                <audio controls preload="none" src={v.href} />
              </div>
            ))
          ) : (
            <div className="empty">
              <div>No audio tracks were produced for this job.</div>
            </div>
          )}
        </section>
      )}

      {/* Lyrics slot — plain text for THIS issue. */}
      {isComplete && (
        <section className="lyrics-panel">
          <div className="sec-label">lyrics</div>
          {lyrics ? (
            <pre className="lyrics-text">{lyrics}</pre>
          ) : (
            <div className="empty">
              <div>No lyrics available for this job.</div>
            </div>
          )}
        </section>
      )}

      {!isComplete && !meta.active && payload.status !== "failed" && (
        <div className="empty">
          <div className="empty-title">{meta.label}</div>
          <div>Results will appear here once the job finishes.</div>
        </div>
      )}
    </>
  );
}
