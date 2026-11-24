// KARAOKE — the Stage: per-song player room on #/job/:token (Marquee port,
// #154). Literal port of design/claude-export/proto/stage.jsx StageScreen
// (:91-161) — TransportBar/ConsoleModule/SetlistModule live in
// player/KaraokePlayer.tsx around the untouched engine API. The DOM tree and
// inline styles are the design's; the adaptations wire real data only:
// SharePayload (incl. the new completed_at/source_url), the structured lyrics
// payload, real artifact downloads, and the dark Sonereli room
// (Day/Night removed, UI/UX v1 step 0). Recorded deviations: cost + receipt are
// OMITTED on this page (not in SharePayload — anonymous share viewers must
// not see cost); the ⤢ Performance control opens the #156 fullscreen overlay
// (components/Perf.tsx), mounted inside the player so the engine persists.

import { FeedbackButton } from "../feedback/Feedback";
import { type CSSProperties, lazy, type ReactNode, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { cancelJob, createJob, fetchProtectedArtifact, getLyrics, getShare, type JobOut, listJobs, type LyricsPayload, type SharePayload } from "../api";
import { artifactHref, artifactView, canRetryJob, statusMeta } from "../jobStatus";
import { t } from "../i18n";
import { saveLastPlayed } from "../lib/library";
import { connectJobSocket, isTerminal, type WsJobStatus } from "../ws";
import { Processing, stageIndex } from "./Processing";
import { fmtDuration, formatRelativeTime } from "../lib/jobListUtils";
import { useCoarsePointer, usePhoneLayout } from "../lib/layout";
import { compactUrlLabel, sourceDisplay } from "../lib/source";
import { addHash, clerkStageToken, goDashboard, itemHash, mySongsHash, navigate, publicShareUrl, replaceHash } from "../router";
import { MBulbs, SonereliMark } from "./marks";
import { timeLines } from "./stage-core";
import { parseLrc, sungLines, SyncedLyrics } from "./SyncedLyrics";
import { Toast } from "./Toast";
import { LyricsReview } from "./LyricsQuality";

// Lazy so wavesurfer.js (+ its WebAudio engine) only loads on the stage route,
// keeping the booth bundle lean.
const KaraokePlayer = lazy(() => import("../player/KaraokePlayer"));

const POLL_MS = 3000;

// View toggle console|setlist — persisted; console default (FINAL.defaultView).
type StageView = "console" | "setlist";
const VIEW_KEY = "karaoke-stage-view";

function initialView(): StageView {
  try {
    if (localStorage.getItem(VIEW_KEY) === "setlist") return "setlist";
  } catch {
    /* localStorage unavailable — fall through to the default */
  }
  return "console";
}

export function Stage({ token, publicBaseUrl, shared = false, startAt }: {
  token: string;
  publicBaseUrl: string;
  shared?: boolean;
  /** Resume position from Continue singing (#/job/:token?t=…). */
  startAt?: number;
}) {
  const protectedStage = clerkStageToken(token);
  const [payload, setPayload] = useState<SharePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [lyrics, setLyrics] = useState<LyricsPayload | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [view, setViewRaw] = useState<StageView>(initialView);
  const phone = usePhoneLayout();
  const timer = useRef<number | null>(null);
  // Processing (UI/UX v1 step 3): a job that was not ready when opened shows
  // the Processing screen until the singer taps Start singing.
  const [sawProcessing, setSawProcessing] = useState<boolean | null>(null);
  const [started, setStarted] = useState(false);
  const [owner, setOwner] = useState<JobOut | null>(null);
  const [live, setLive] = useState<{ status: WsJobStatus; progress: number; error: string | null } | null>(null);
  const [failedStage, setFailedStage] = useState(-1);
  const [socket, setSocket] = useState<"idle" | "open" | "dropped">("idle");
  const [lastUpdate, setLastUpdate] = useState(() => Date.now());
  const [now, setNow] = useState(() => Date.now());

  const setView = useCallback((v: StageView) => {
    try {
      localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* ignore persistence failure */
    }
    setViewRaw(v);
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await getShare(token);
      setPayload(data);
      setLastUpdate(Date.now());
      setSawProcessing((seen) => seen ?? data.status !== "completed");
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

  // The owner's row (id, error) enables Cancel/Try again and the live socket.
  const processing = sawProcessing === true && !started;
  useEffect(() => {
    if (shared || !processing) return;
    let cancelled = false;
    listJobs(200).then((rows) => {
      if (!cancelled) setOwner(rows.find((row) => row.job_token === token) ?? null);
    }, () => undefined);
    return () => {
      cancelled = true;
    };
  }, [shared, processing, token, payload?.status]);

  const ownerId = owner?.id ?? null;
  const watching = processing && ownerId !== null && !!payload && statusMeta(payload.status).active;
  useEffect(() => {
    if (!watching || ownerId === null) return;
    return connectJobSocket({
      onEvent: (event) => {
        if (event.job_id !== ownerId) return;
        setLastUpdate(Date.now());
        setLive((prev) => ({
          status: event.status,
          progress: event.progress ?? prev?.progress ?? 0,
          error: event.type === "stage_change" ? event.error : prev?.error ?? null,
        }));
        if (isTerminal(event.status)) void load();
      },
      onOpenChange: (open) => setSocket((prev) => (open ? "open" : prev === "idle" ? "idle" : "dropped")),
    });
  }, [watching, ownerId, load]);

  useEffect(() => {
    if (socket !== "dropped") return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [socket]);

  // Live status: the socket's frame when it is newer, else the REST payload.
  const liveStatus: WsJobStatus | null = payload
    ? live && !(isTerminal(payload.status) && !isTerminal(live.status)) ? live.status : payload.status
    : null;
  useEffect(() => {
    if (liveStatus && !isTerminal(liveStatus)) {
      const index = stageIndex(liveStatus);
      if (index >= 0) setFailedStage(Math.min(index, 2));
    }
  }, [liveStatus]);

  const retry = useCallback(() => {
    if (!owner || !canRetryJob(owner)) {
      navigate(addHash);
      return;
    }
    createJob({ url: owner.source_url, title: owner.title ?? undefined })
      .then((job) => replaceHash(itemHash(job.job_token)), () => setToast(t("err.generic")));
  }, [owner]);

  const cancel = useCallback(() => {
    if (!owner) return;
    cancelJob(owner.id).then(() => load(), () => setToast(t("err.generic")));
  }, [owner, load]);

  // Remember the song and position for Continue singing (step 4).
  const payloadRef = useRef<SharePayload | null>(null);
  payloadRef.current = payload;
  const savedAt = useRef(-10);

  // Structured lyrics (LRC + plain + provenance) once the job is complete.
  useEffect(() => {
    if (payload?.status === "completed") {
      getLyrics(token)
        .then(setLyrics)
        .catch(() => setLyrics(null));
    }
  }, [payload?.status, token]);

  const reloadLyrics = useCallback(async () => {
    const updated = await getLyrics(token);
    if (!updated) throw new Error("Could not load the latest lyrics. Your draft is unchanged.");
    setLyrics(updated);
  }, [token]);

  const onCopyLink = useCallback(async () => {
    const url = publicShareUrl(publicBaseUrl, token);
    try {
      await navigator.clipboard.writeText(url);
      setToast("Link copied");
    } catch {
      // Clipboard API blocked (insecure context / permissions) — fall back.
      window.prompt("Copy this link:", url);
    }
  }, [publicBaseUrl, token]);

  const download = useCallback(async (url: string, filename: string) => {
    try {
      const response = await fetchProtectedArtifact(url);
      if (!response.ok) throw new Error("Download request failed");
      const objectUrl = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch {
      setToast("Couldn’t download this file");
    }
  }, []);

  // ── Player ↔ lyrics transport seam (#59) ─────────────────────────────────
  // `onTime` pushes the live playhead into state that only feeds the lyrics
  // highlight (the player owns its own clock, so this doesn't drive playback).
  // `seekRef` is the player's imperative seek, shared so a lyrics-line click
  // can drive the transport. Both are stable across renders.
  const [currentTime, setCurrentTime] = useState(0);
  const seekRef = useRef<((time: number) => void) | null>(null);
  const onTime = useCallback((t: number) => {
    // Quantise to ~10 fps so timeupdate frames don't trigger a re-render storm;
    // the active-line lookup is far coarser than that anyway.
    setCurrentTime((prev) => (Math.abs(prev - t) < 0.1 ? prev : t));
    const song = payloadRef.current;
    if (!shared && song && t > 0 && Math.abs(t - savedAt.current) >= 1) {
      savedAt.current = t;
      saveLastPlayed({
        token, title: song.title?.trim() || "Untitled", artist: song.artist, sourceUrl: song.source_url,
        position: t, duration: song.duration, at: Date.now(),
      });
    }
  }, [shared, token]);
  const onSeek = useCallback((time: number) => {
    seekRef.current?.(time);
  }, []);
  const reducedMotion = useMemo(
    () =>
      typeof window !== "undefined" &&
      !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    [],
  );

  if (payload && liveStatus && (payload.status !== "completed" || processing)) {
    return (
      <>
        <Processing
          view={{
            status: liveStatus,
            progress: live && liveStatus === live.status ? live.progress : payload.progress,
            title: payload.title?.trim() || "Your song",
            artist: payload.artist,
            sourceUrl: payload.source_url,
            error: owner?.error ?? live?.error ?? null,
          }}
          owner={shared ? null : owner}
          failedStage={failedStage}
          staleSeconds={socket === "dropped" ? Math.max(0, Math.round((now - lastUpdate) / 1000)) : null}
          onStart={() => setStarted(true)}
          onCancel={cancel}
          onRetry={retry}
        />
        <Toast message={toast} onDone={() => setToast(null)} />
      </>
    );
  }

  let content: ReactNode;
  if (!loaded) {
    content = <StageSkeleton />;
  } else if (notFound) {
    content = (
      <StageEmpty
        title="Job not found"
        sub="This share link is invalid, expired, or you don’t have access to it."
      />
    );
  } else if (error) {
    content = (
      <div style={{ textAlign: "center", padding: "48px 20px", color: "var(--muted)" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: "var(--fg)", marginBottom: 5 }}>Couldn’t load this job</div>
        <div className="m-mono" style={{ fontSize: 11.5, color: "var(--err)", overflowWrap: "anywhere" }}>{error}</div>
        <div style={{ marginTop: 12 }}>
          <button type="button" className="m-btn sm" onClick={() => void load()}>↻ Retry</button>
        </div>
      </div>
    );
  } else if (payload) {
    content = (
      <StageBody
        payload={payload}
        token={token}
        shared={shared}
        protectedStage={protectedStage}
        download={download}
        lyrics={lyrics}
        onLyricsSaved={setLyrics}
        onReloadLyrics={reloadLyrics}
        view={view}
        setView={setView}
        currentTime={currentTime}
        onTime={onTime}
        seekRef={seekRef}
        onSeek={onSeek}
        reducedMotion={reducedMotion}
        startAt={startAt}
      />
    );
  }

  // StageScreen (stage.jsx:91-161): one dark room (Sonereli UI/UX v1).
  return (
    <div className="stage-scroll">
      <div className={`m-stage${shared ? " shared-stage" : ""}`} style={{ minHeight: "100%", width: "100%", minWidth: 0, display: "flex", flexDirection: "column" }}>
        {/* phone (#184): the row wraps instead of overflowing — same elements,
            same order; the desktop branch is the untouched port. */}
        <div style={phone
          ? { display: "flex", alignItems: "center", gap: 8, padding: "8px 14px", minHeight: 54, borderBottom: "1px solid var(--border)", flexShrink: 0, flexWrap: "wrap", minWidth: 0 }
          : { display: "flex", alignItems: "center", gap: 12, padding: "0 22px", height: 54, borderBottom: "1px solid var(--border)", flexShrink: 0, minWidth: 0 }}>
          {!shared && <button className="m-btn sm ghost" type="button" onClick={() => token.startsWith("clerk.") ? navigate(mySongsHash) : goDashboard()}>← Booth</button>}
          <SonereliMark size={20} />
          <span style={{ flex: 1, minWidth: 0 }}></span>
          {protectedStage ? <span className="m-chip info">Catalog</span> : (
            <>
              <span className="m-chip info">Unlisted</span>
              <button className="m-btn sm" type="button" onClick={() => void onCopyLink()}>Copy link</button>
            </>
          )}
          <FeedbackButton />
        </div>

        <main className={shared ? "shared-stage-body" : undefined} style={shared ? undefined : { flex: 1, maxWidth: 760, width: "100%", margin: "0 auto", padding: phone ? "18px 14px 18px" : "22px 24px 20px", display: "flex", flexDirection: "column", gap: 16, minHeight: 0, minWidth: 0 }}>
          {content}
        </main>
        <Toast message={toast} onDone={() => setToast(null)} />
      </div>
    </div>
  );
}

function StageBody({
  payload,
  token,
  shared,
  protectedStage,
  download,
  lyrics,
  onLyricsSaved,
  onReloadLyrics,
  view,
  setView,
  currentTime,
  onTime,
  seekRef,
  onSeek,
  reducedMotion,
  startAt,
}: {
  payload: SharePayload;
  token: string;
  shared: boolean;
  protectedStage: boolean;
  download: (url: string, filename: string) => Promise<void>;
  lyrics: LyricsPayload | null;
  onLyricsSaved: (lyrics: LyricsPayload) => void;
  onReloadLyrics: () => Promise<void>;
  view: StageView;
  setView: (v: StageView) => void;
  currentTime: number;
  onTime: (t: number) => void;
  seekRef: React.MutableRefObject<((time: number) => void) | null>;
  onSeek: (time: number) => void;
  reducedMotion: boolean;
  startAt?: number;
}) {
  const meta = statusMeta(payload.status);
  const isComplete = payload.status === "completed";
  const source = sourceDisplay(payload.source_url);
  // Mobile adaptations (#184): phone shrinks the title, coarse pointers get
  // bigger view-toggle hit targets. No m-* mobile source for this page — the
  // desktop Marquee structure is the design; these only resize/rewrap it.
  const phone = usePhoneLayout();
  const coarse = useCoarsePointer();
  const titleFallback = source.kind === "upload" ? source.label
    : shared ? "Shared song" : `Job ${token.slice(0, 8)}`;
  const title = payload.title?.trim() || titleFallback;
  const pct = Math.max(0, Math.min(100, Math.round(payload.progress)));

  // Resolve the two stems for the real player. `karaoke` = instrumental (the
  // master + waveform), `vocals` = the follower. We prefer kind, then fall back
  // to filename for older jobs whose artifacts lack a precise content_type.
  const views = payload.artifacts.map((a) => artifactView(token, a));
  const audio = views.filter((v) => v.isAudio);
  const instrumental = useMemo(
    () => audio.find((v) => v.kind === "karaoke") ?? audio.find((v) => v.name.startsWith("karaoke")) ?? null,
    [audio],
  );
  const vocals = useMemo(
    () => audio.find((v) => v.kind === "vocals") ?? audio.find((v) => v.name.startsWith("vocals")) ?? null,
    [audio],
  );
  const lyricsFile = views.find((v) => v.kind === "lyrics") ?? null;

  // Timed lyric lines for the wipe / setlist modules — LRC start stamps with
  // derived sing durations (stage-core.timeLines).
  const lines = useMemo(() => (lyrics?.lrc ? parseLrc(lyrics.lrc) : []), [lyrics?.lrc]);
  // Speech lines (#283) are listed but never sung: the player's wipe and the
  // performance view get the sung lines only.
  const timed = useMemo(() => timeLines(sungLines(lines), payload.duration), [lines, payload.duration]);

  // Meta row, real data only: duration · split <relative completed_at>.
  // Cost + receipt are deliberately absent (recorded deviation — they are not
  // in SharePayload; anonymous share viewers must not see cost).
  const completedRel = formatRelativeTime(payload.completed_at);
  const metaBits = [fmtDuration(payload.duration), completedRel ? `split ${completedRel}` : null]
    .filter(Boolean)
    .join(" · ");
  const lyricsPanel = isComplete && (
    <SyncedLyrics
      lrc={lyrics?.lrc ?? null}
      source={lyrics?.source ?? null}
      plainLyrics={lyrics?.plain ?? null}
      currentTime={currentTime}
      onSeek={onSeek}
      reducedMotion={reducedMotion}
    />
  );
  const downloadControl = (url: string, name: string, label: ReactNode) => protectedStage
    ? <button className="m-btn sm" type="button" onClick={() => void download(url, name)}>{label}</button>
    : <a className="m-btn sm" href={url} download>{label}</a>;
  const downloads = (
    <>
      {vocals && downloadControl(vocals.href, "vocals.mp3", <><span className="m-stem vox"></span>vocals.mp3</>)}
      {instrumental && downloadControl(instrumental.href, "karaoke.mp3", <><span className="m-stem inst"></span>karaoke.mp3</>)}
      {lyrics?.synced
        ? downloadControl(artifactHref(token, "lyrics.lrc"), "lyrics.lrc", "≡ lyrics.lrc")
        : lyricsFile && downloadControl(lyricsFile.href, lyricsFile.name, `≡ ${lyricsFile.name}`)}
    </>
  );
  const original = source.kind === "url" && (
    <a className="m-btn sm ghost" href={payload.source_url} target="_blank" rel="noopener">▶ original ↗</a>
  );

  return (
    <>
      {/* header block: meta row + title + view toggle (stage.jsx:105-124) */}
      <div className={shared ? "shared-stage-song" : undefined} style={{ display: "flex", alignItems: "flex-end", gap: 14, flexWrap: "wrap", minWidth: 0 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="m-mono stage-song-meta" style={{ fontSize: 11, color: "var(--muted)", display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
            {metaBits && <span>{metaBits}</span>}
            {payload.owner_display_name && <span data-feedback-private style={{ minWidth: 0, overflowWrap: "anywhere" }}>shared by {payload.owner_display_name}</span>}
            {/* uploads (#173) have no external source — show the filename, no link.
                Single-line ellipsis on both widths (#184): the raw URL used to
                wrap across ~4 lines; the full URL stays in the href. */}
            {shared ? null : source.kind === "url" ? (
              <a href={payload.source_url} target="_blank" rel="noopener" className="m-mono" style={{ color: "var(--info)", textDecoration: "none", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>
                source: {compactUrlLabel(source.label)} ↗
              </a>
            ) : (
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>source: {source.label}</span>
            )}
          </div>
          <h1 dir="auto" style={{ margin: "6px 0 0", fontFamily: "var(--font-display)", fontWeight: 650, fontSize: phone ? 20 : 28, letterSpacing: "-0.02em", lineHeight: 1.1, overflowWrap: "anywhere" }}>{title}</h1>
          {(payload.artist || payload.album) && (
            <div className="stage-song-artist" style={{ marginTop: 2, fontSize: 13, color: "var(--muted)", overflowWrap: "anywhere" }}>
              {[payload.artist, payload.album].filter(Boolean).join(" — ")}
            </div>
          )}
        </div>
        {/* view toggle */}
        {isComplete && !shared && (
          <div style={{ display: "flex", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
            {(["console", "setlist"] as const).map((v) => (
              <button key={v} type="button" onClick={() => setView(v)} className="m-mono" style={{
                appearance: "none", border: "none", cursor: "pointer", padding: coarse ? "10px 14px" : "6px 13px", fontSize: 11,
                background: view === v ? "var(--accent)" : "var(--bg-card)",
                color: view === v ? "var(--accent-fg)" : "var(--fg-soft)", fontWeight: 650,
              }}>{v}</button>
            ))}
          </div>
        )}
      </div>

      {/* in-flight: stage chip + worker note + wipebar, refreshed by the poll */}
      {meta.active && (
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "13px 16px", display: "grid", gap: 10, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
            <span className={`m-chip ${meta.chip === "neutral" ? "" : meta.chip}`}><span className="m-dot"></span>{meta.label}</span>
            <span className="m-mono" style={{ fontSize: 11.5, color: "var(--muted)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {payload.stage_note || meta.note}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
            <div className="m-wipebar" style={{ "--wipe": pct + "%", flex: 1 } as CSSProperties}><i></i></div>
            <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>{pct}%</span>
          </div>
        </div>
      )}

      {payload.status === "failed" && (
        <div className="m-mono" style={{ padding: "10px 12px", borderRadius: "var(--radius)", background: "color-mix(in oklab, var(--err) 9%, var(--bg))", border: "1px solid color-mix(in oklab, var(--err) 24%, transparent)", color: "var(--err)", fontSize: 11.5, lineHeight: 1.5 }}>
          This job failed to process. Try resubmitting the source URL from the booth.
        </div>
      )}

      {!isComplete && !meta.active && payload.status !== "failed" && (
        <StageEmpty title={meta.label} sub="Results will appear here once the job finishes." />
      )}


      {shared && lyricsPanel}

      {/* player: waveform card + console/setlist modules (KaraokePlayer.tsx) */}
      {isComplete && instrumental && (
        <Suspense
          fallback={
            <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "14px 16px" }} aria-hidden>
              <div className="ksplayer-wave-wrap"><div className="ksplayer-loading" /></div>
            </div>
          }
        >
          <KaraokePlayer
            instrumentalUrl={instrumental.href}
            vocalsUrl={vocals?.href ?? null}
            fetchStem={protectedStage ? fetchProtectedArtifact : undefined}
            onTime={onTime}
            seekRef={seekRef}
            view={shared ? "compact" : view}
            lines={timed}
            title={title}
            artist={payload.artist}
            plainLyrics={lyrics?.plain ?? null}
            startAt={startAt}
          />
        </Suspense>
      )}
      {isComplete && !instrumental && (
        <StageEmpty title="No audio tracks" sub="No audio tracks were produced for this job." />
      )}

      {!shared && isComplete && lyrics && <LyricsReview lyrics={lyrics} onSaved={onLyricsSaved} onSeek={onSeek} onReload={onReloadLyrics} />}

      {!shared && lyricsPanel}

      {/* take home (stage.jsx:150-157) — real downloads via artifactHref */}
      {isComplete && (shared ? (
        <details className="shared-stage-downloads">
          <summary>Downloads</summary>
          <div>{downloads}{original}</div>
        </details>
      ) : (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: "auto", borderTop: "1px dashed var(--border)", paddingTop: 13, flexWrap: "wrap", minWidth: 0 }}>
          <span className="m-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)", marginRight: 6 }}>take home</span>
          {downloads}
          <span style={{ flex: 1, minWidth: 0 }}></span>
          {original}
        </div>
      ))}
    </>
  );
}

// Loading: the design's shimmer/skeleton primitives — no spinners.
function StageSkeleton() {
  return (
    <div aria-hidden style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div>
        <span className="skel skel-line s" />
        <span className="skel skel-line m" style={{ height: "1.5rem" }} />
      </div>
      <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "14px 16px" }}>
        <span className="skel" style={{ display: "block", height: 96 }} />
      </div>
      <span className="skel skel-line l" />
    </div>
  );
}

// Empty/terminal states built from marquee primitives (bulbs), no spinners.
function StageEmpty({ title, sub }: { title: string; sub: string }) {
  return (
    <div style={{ textAlign: "center", padding: "48px 20px", color: "var(--muted)" }}>
      <div style={{ display: "flex", justifyContent: "center", marginBottom: 14 }}>
        <MBulbs n={7} lit={0} size={5} gap={8} />
      </div>
      <div style={{ fontFamily: "var(--font-display)", fontSize: 16, fontWeight: 600, color: "var(--fg)", marginBottom: 5 }}>{title}</div>
      <div style={{ fontSize: 12.5 }}>{sub}</div>
    </div>
  );
}

export default Stage;
