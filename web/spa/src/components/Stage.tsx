// Sonereli — the Stage: the per-song player on #/job/:token and /share/:token
// (UI/UX v1 steps 6–7). Lyrics fill the screen; scrub, transport and Original
// vocals sit in the bottom dock in thumb reach. The top bar has minimize, the
// song title and ⋯; Lyrics size, Performance view, Fix lyrics, downloads,
// Copy share link and Report a problem live in the ⋯ sheet. A guest share
// link shows the same Stage with the Sonereli mark instead of minimize and
// without owner items. A job that was not ready when opened shows Processing
// until the singer taps Start singing (step 3).

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { cancelJob, createJob, fetchProtectedArtifact, getLyrics, getShare, type JobOut, listJobs, type LyricsPayload, type SharePayload } from "../api";
import { useOpenFeedback } from "../feedback/Feedback";
import { t } from "../i18n";
import { artifactHref, artifactView, canRetryJob, statusMeta } from "../jobStatus";
import { coverUrl } from "../lib/library";
import { loadPref } from "../lib/prefs";
import { sourceDisplay } from "../lib/source";
import { usePlayerSession } from "../player/PlayerProvider";
import type { KaraokePlayerApi } from "../player/useKaraokePlayer";
import { addHash, clerkStageToken, closeModalRoute, itemHash, navigate, publicShareUrl, replaceHash } from "../router";
import { connectJobSocket, isTerminal, type WsJobStatus } from "../ws";
import { Icon } from "./Icon";
import { LyricsRebuild, LyricsReviewEditor } from "./LyricsQuality";
import { SonereliMark } from "./marks";
import { Processing, stageIndex } from "./Processing";
import { Sheet } from "./Sheet";
import { StageDock } from "./StageDock";
import { StageLyrics } from "./StageLyrics";
import { LYRICS_SIZES, type LyricsSize, loadLyricsSize, saveLyricsSize } from "./stageLyricsModel";
import { parseLrc, sungLines } from "./SyncedLyrics";
import { Toast } from "./Toast";

const POLL_MS = 3000;

const noop = () => undefined;
/** Stands in until the app player has switched to this Stage's song. */
const IDLE_PLAYER: KaraokePlayerApi = {
  ready: false, playing: false, duration: 0, currentTime: 0, rate: 1, vocalLevel: 0, loop: false,
  region: { a: null, b: null }, hasVocals: true, ended: false, error: null,
  playPause: noop, seek: noop, skip: noop, setRate: noop, setVocalLevel: noop, setMix: noop, toggleLoop: noop,
  markA: noop, markB: noop, clearRegion: noop, subscribeTime: () => noop,
};

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
  const title = payload?.title?.trim() || (shared ? t("stage.lyrics") : "");
  let content: React.ReactNode = null;
  if (!loaded) {
    content = <StageSkeleton />;
  } else if (notFound) {
    content = <StageMessage title={t("stage.notFound")} lead={t("stage.notFoundLead")} />;
  } else if (error) {
    content = (
      <StageMessage title={t("stage.loadError")} lead={t("err.generic")}
        action={<button type="button" className="sn-btn sn-btn-small" onClick={() => void load()}>{t("action.retry")}</button>} />
    );
  }

  if (content || !payload) {
    return (
      <div className={`sn-app sn-stage${shared ? " shared-stage" : ""}`}>
        <StageTop shared={shared} title={title} artist={null} />
        <div className="sn-stage-main">{content}</div>
      </div>
    );
  }

  return (
    <StageBody
      key={token}
      payload={payload}
      token={token}
      shared={shared}
      protectedStage={protectedStage}
      lyrics={lyrics}
      onLyricsSaved={setLyrics}
      onReloadLyrics={reloadLyrics}
      publicBaseUrl={publicBaseUrl}
      startAt={startAt}
    />
  );
}

function StageTop({ shared, title, artist, onMore }: {
  shared: boolean;
  title: string;
  artist: string | null;
  onMore?: (button: HTMLButtonElement) => void;
}) {
  return (
    <header className={`sn-stage-top${shared ? " shared-stage-song" : ""}`}>
      {shared ? (
        <span className="sn-stage-brand"><SonereliMark size={26} /></span>
      ) : (
        <button type="button" className="sn-iconbtn" onClick={closeModalRoute} aria-label={t("stage.minimize")}
          data-sn-minimize="">
          <Icon name="down" size={28} />
        </button>
      )}
      <div className="sn-stage-title">
        <h1><bdi className="sn-t1" dir="auto">{title}</bdi></h1>
        {artist && <bdi className="sn-t2" dir="auto">{artist}</bdi>}
      </div>
      {onMore ? (
        <button type="button" className="sn-iconbtn" data-sn-more="" aria-haspopup="dialog" aria-label={t("stage.more")}
          onClick={(e) => onMore(e.currentTarget)}>
          <Icon name="more" size={28} />
        </button>
      ) : <span className="sn-iconbtn" aria-hidden="true" />}
    </header>
  );
}

type SheetName = "menu" | "fix" | "downloads" | null;

function StageBody({ payload, token, shared, protectedStage, lyrics, onLyricsSaved, onReloadLyrics, publicBaseUrl, startAt }: {
  payload: SharePayload;
  token: string;
  shared: boolean;
  protectedStage: boolean;
  lyrics: LyricsPayload | null;
  onLyricsSaved: (lyrics: LyricsPayload) => void;
  onReloadLyrics: () => Promise<void>;
  publicBaseUrl: string;
  startAt?: number;
}) {
  const source = sourceDisplay(payload.source_url);
  const title = payload.title?.trim() || (source.kind === "upload" ? source.label : t("stage.lyrics"));
  const views = payload.artifacts.map((a) => artifactView(token, a));
  const audio = views.filter((v) => v.isAudio);
  const instrumental = audio.find((v) => v.kind === "karaoke") ?? audio.find((v) => v.name.startsWith("karaoke")) ?? null;
  const vocals = audio.find((v) => v.kind === "vocals") ?? audio.find((v) => v.name.startsWith("vocals")) ?? null;
  const lyricsFile = views.find((v) => v.kind === "lyrics") ?? null;

  const lines = useMemo(() => (lyrics?.lrc ? parseLrc(lyrics.lrc) : []), [lyrics?.lrc]);
  const sung = useMemo(() => sungLines(lines), [lines]);

  // The song plays in the app-level player (step 8): opening the Stage starts
  // (or rejoins) its session; minimizing keeps it playing.
  const { session, player: sessionPlayer, open, vocalPct, setVocalPct, toggleVocals } = usePlayerSession();
  useLayoutEffect(() => {
    if (!instrumental) return;
    open({
      token, title, artist: payload.artist, sourceUrl: payload.source_url, duration: payload.duration,
      instrumentalUrl: instrumental.href, vocalsUrl: vocals?.href ?? null, protectedStage, shared, lines: sung,
    });
  }, [open, token, title, payload.artist, payload.source_url, payload.duration, instrumental?.href, vocals?.href,
    protectedStage, shared, sung]);
  const player = session?.token === token ? sessionPlayer : IDLE_PLAYER;
  const reducedMotion = useMemo(
    () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    [],
  );
  const [size, setSizeRaw] = useState<LyricsSize>(loadLyricsSize);
  const setSize = (next: LyricsSize) => {
    saveLyricsSize(next);
    setSizeRaw(next);
  };
  const [sheet, setSheet] = useState<SheetName>(null);
  const [perf, setPerf] = useState(false);
  const countIn = loadPref("countIn");
  const [toast, setToast] = useState<string | null>(null);
  const moreRef = useRef<HTMLButtonElement | null>(null);
  const openFeedback = useOpenFeedback();
  const canFix = !shared && !!lyrics && lyrics.review_job_id != null && !!lyrics.revision;

  // Resume where the singer stopped, once, when the stems are ready.
  const resumed = useRef(false);
  useEffect(() => {
    if (resumed.current || !player.ready || !startAt) return;
    resumed.current = true;
    player.seek(startAt);
  }, [player.ready, player.seek, startAt]);

  // Keep the screen awake while singing (Settings), and always in Performance view.
  const keepAwake = perf || (player.playing && loadPref("keepAwake"));
  useEffect(() => {
    if (!keepAwake || !navigator.wakeLock) return;
    let cancelled = false;
    let pending = false;
    let lock: WakeLockSentinel | undefined;
    const keepScreenOn = async () => {
      if (document.hidden || pending || (lock && !lock.released)) return;
      pending = true;
      try {
        const next = await navigator.wakeLock.request("screen");
        if (cancelled) await next.release();
        else lock = next;
      } catch {
        // The browser or OS can refuse a wake lock even while the tab is visible.
      } finally {
        pending = false;
      }
    };
    void keepScreenOn();
    document.addEventListener("visibilitychange", keepScreenOn);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", keepScreenOn);
      void lock?.release().catch(() => {});
    };
  }, [keepAwake]);

  // Desktop keys: Space play/pause, ←/→ ±5 s. Not while typing or in a sheet.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      if (perf && e.key === "Escape") {
        setPerf(false);
        return;
      }
      if (sheet || e.metaKey || e.ctrlKey || e.altKey) return;
      if (el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName) || el.closest("dialog"))) return;
      if (e.key === " " && el?.tagName !== "BUTTON") {
        e.preventDefault();
        player.playPause();
      } else if (e.key === "ArrowLeft" && !el?.closest("[role=slider]")) {
        e.preventDefault();
        player.skip(-5);
      } else if (e.key === "ArrowRight" && !el?.closest("[role=slider]")) {
        e.preventDefault();
        player.skip(5);
      } else if ((e.key === "ArrowUp" || e.key === "ArrowDown") && !el?.closest("[role=slider]")) {
        e.preventDefault();
        setVocalPct(vocalPct + (e.key === "ArrowUp" ? 5 : -5));
      } else if (e.key === "m" || e.key === "M") {
        toggleVocals();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [player, sheet, perf, vocalPct, setVocalPct, toggleVocals]);

  const onMore = (button: HTMLButtonElement) => {
    moreRef.current = button;
    setSheet("menu");
  };
  const report = () => {
    setSheet(null);
    const trigger = moreRef.current ?? document.querySelector<HTMLButtonElement>("[data-sn-more]");
    // Let the sheet close first so the screenshot shows the Stage.
    window.setTimeout(() => trigger && openFeedback?.(trigger), 0);
  };
  const copyLink = async () => {
    setSheet(null);
    const url = publicShareUrl(publicBaseUrl, token);
    try {
      await navigator.clipboard.writeText(url);
      setToast(t("stage.copied"));
    } catch {
      // Clipboard API blocked (insecure context / permissions) — fall back.
      window.prompt(t("stage.copyPrompt"), url);
    }
  };
  const download = async (url: string, filename: string) => {
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
      setToast(t("stage.downloadFailed"));
    }
  };
  const downloadItem = (url: string, name: string) => protectedStage ? (
    <button type="button" className="sn-menu-item" onClick={() => void download(url, name)}>
      <Icon name="download" /><span className="sn-grow">{name}</span>
    </button>
  ) : (
    <a className="sn-menu-item" href={url} download>
      <Icon name="download" /><span className="sn-grow">{name}</span>
    </a>
  );

  const noLyrics = (
    <div className="sn-nolyrics">
      {coverUrl(payload.source_url)
        ? <img className="sn-nolyrics-cover" src={coverUrl(payload.source_url) ?? undefined} alt="" />
        : <span className="sn-nolyrics-cover" aria-hidden="true"><Icon name="note" size={48} /></span>}
      <p className="sn-empty-title">{t("stage.noLyrics")}</p>
      <button type="button" className="sn-btn sn-btn-small" onClick={(e) => openFeedback?.(e.currentTarget)}>
        <Icon name="flag" size={20} />{t("stage.reportLyrics")}
      </button>
    </div>
  );

  if (perf) {
    // Performance view: lyrics centered and larger, × / play / mic, no scrub
    // (no accidental seeks), the screen stays on.
    return (
      <div className="sn-app sn-stage sn-perf" role="dialog" aria-label={t("stage.perf")}>
        <div className="sn-stage-main">
          <StageLyrics lines={lines} duration={payload.duration} plain={lyrics?.plain ?? null}
            currentTime={player.currentTime} onSeek={player.seek} size="perf" reducedMotion={reducedMotion}
            noLyrics={noLyrics} countIn={countIn} />
        </div>
        <div className="sn-perf-bar">
          <button type="button" className="sn-iconbtn sn-perf-btn" onClick={() => setPerf(false)}
            aria-label={t("stage.perfExit")} data-sn-perf-exit="">
            <Icon name="x" size={24} />
          </button>
          <button type="button" className="sn-play sn-play-perf" onClick={player.playPause} disabled={!player.ready}
            data-sn-play="" aria-label={player.playing ? t("stage.pause") : t("stage.play")}>
            <Icon name={player.playing ? "pause" : "play"} size={28} />
          </button>
          <button type="button" className="sn-iconbtn sn-perf-btn" onClick={toggleVocals} disabled={!player.hasVocals}
            aria-label={t("stage.vocalsMute")} aria-pressed={vocalPct === 0} data-sn-mic="">
            <Icon name={vocalPct > 0 ? "mic" : "micoff"} size={24} />
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className={`sn-app sn-stage${shared ? " shared-stage" : ""}`}>
      <StageTop shared={shared} title={title} artist={payload.artist} onMore={onMore} />
      <div className="sn-stage-main">
        {!instrumental ? (
          <StageMessage title={t("stage.noAudio")} />
        ) : player.ended ? (
          <EndCard onAgain={() => {
            player.seek(0);
            player.playPause();
          }} shared={shared} />
        ) : lyrics === null && payload.status === "completed" ? (
          <StageSkeleton />
        ) : (
          <StageLyrics lines={lines} duration={payload.duration} plain={lyrics?.plain ?? null}
            currentTime={player.currentTime} onSeek={player.seek} size={size} reducedMotion={reducedMotion}
            noLyrics={noLyrics} countIn={countIn} />
        )}
      </div>
      {instrumental && (
        <StageDock player={player} lines={sung} vocalPct={vocalPct} onVocals={setVocalPct} onToggleVocals={toggleVocals}>
          {player.error ? (
            <p className="sn-stage-status sn-err" role="alert">
              {t("stage.audioError")}
              <button type="button" className="sn-btn sn-btn-small sn-btn-ghost" onClick={() => window.location.reload()}>
                {t("action.retry")}
              </button>
            </p>
          ) : !player.ready ? <p className="sn-stage-status" role="status">{t("stage.loading")}</p> : null}
        </StageDock>
      )}

      <Sheet open={sheet === "menu"} title={title} onClose={() => setSheet(null)}>
        <div className="sn-sheet-group">
          <span className="sn-label">{t("stage.size")}</span>
          <div className="sn-segmented" role="radiogroup" aria-label={t("stage.size")}>
            {LYRICS_SIZES.map((value) => (
              <button key={value} type="button" role="radio" aria-checked={size === value}
                className={size === value ? "is-on" : undefined} onClick={() => setSize(value)}>
                {value.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        <div className="sn-menu">
          <button type="button" className="sn-menu-item" data-sn-perf="" disabled={!player.ready}
            onClick={() => {
              setSheet(null);
              setPerf(true);
            }}>
            <Icon name="expand" /><span className="sn-grow">{t("stage.perf")}</span>
          </button>
          {canFix && (
            <button type="button" className="sn-menu-item" onClick={() => setSheet("fix")}>
              <Icon name="edit" /><span className="sn-grow">{t("stage.fix")}</span>
            </button>
          )}
          <button type="button" className="sn-menu-item" onClick={() => setSheet("downloads")}>
            <Icon name="download" /><span className="sn-grow">{t("stage.downloads")}</span>
          </button>
          {!shared && !protectedStage && (
            <button type="button" className="sn-menu-item" onClick={() => void copyLink()}>
              <Icon name="link" /><span className="sn-grow">{t("stage.copyLink")}</span>
            </button>
          )}
          <button type="button" className="sn-menu-item feedback-trigger" onClick={report}>
            <Icon name="flag" /><span className="sn-grow">{t("stage.report")}</span>
          </button>
        </div>
      </Sheet>

      <Sheet open={sheet === "downloads"} title={t("stage.downloads")} onClose={() => setSheet(null)}>
        <div className="sn-menu">
          {vocals && downloadItem(vocals.href, "vocals.mp3")}
          {instrumental && downloadItem(instrumental.href, "karaoke.mp3")}
          {lyrics?.synced
            ? downloadItem(artifactHref(token, "lyrics.lrc"), "lyrics.lrc")
            : lyricsFile && downloadItem(lyricsFile.href, lyricsFile.name)}
          {source.kind === "url" && (
            <a className="sn-menu-item" href={payload.source_url} target="_blank" rel="noopener">
              <Icon name="share" /><span className="sn-grow">{t("stage.original")}</span>
            </a>
          )}
        </div>
      </Sheet>

      {canFix && lyrics && (
        <Sheet open={sheet === "fix"} title={t("stage.fix")} onClose={() => setSheet(null)} className="sn-sheet-tall">
          <div className="sn-fix m-stage">
            <LyricsRebuild lyrics={lyrics} onReload={onReloadLyrics} />
            <LyricsReviewEditor key={lyrics.revision} lyrics={lyrics} onSaved={onLyricsSaved} onSeek={player.seek}
              onReload={onReloadLyrics} />
          </div>
        </Sheet>
      )}

      <Toast message={toast} onDone={() => setToast(null)} />
    </div>
  );
}

function EndCard({ onAgain, shared }: { onAgain: () => void; shared: boolean }) {
  const [songs, setSongs] = useState<JobOut[]>([]);
  useEffect(() => {
    if (shared) return;
    let cancelled = false;
    listJobs(50).then((rows) => {
      if (!cancelled) setSongs(rows.filter((row) => row.status === "completed").slice(0, 3));
    }, () => undefined);
    return () => {
      cancelled = true;
    };
  }, [shared]);
  return (
    <div className="sn-endcard">
      <h2 className="sn-display sn-end-title">{t("stage.end")}</h2>
      <button type="button" className="sn-btn sn-btn-primary sn-block" onClick={onAgain}>
        <Icon name="retry" size={22} />{t("stage.again")}
      </button>
      {songs.length > 0 && (
        <ul className="sn-list">
          {songs.map((song) => (
            <li key={song.id}>
              <a className="sn-row" href={itemHash(song.job_token)}>
                <span className="sn-grow"><bdi className="sn-t1" dir="auto">{song.title?.trim() || song.source_url}</bdi>
                  {song.artist && <bdi className="sn-t2" dir="auto">{song.artist}</bdi>}</span>
                <Icon name="play" size={20} className="sn-row-play" />
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Loading: static skeleton lines, no shimmer.
function StageSkeleton() {
  return (
    <div className="sn-stage-skeleton" aria-hidden="true">
      {[70, 85, 60, 90, 75, 55].map((w, i) => <span key={i} className="sn-skel" style={{ width: `${w}%` }} />)}
    </div>
  );
}

function StageMessage({ title, lead, action }: { title: string; lead?: string; action?: React.ReactNode }) {
  return (
    <div className="sn-empty">
      <p className="sn-empty-title">{title}</p>
      {lead && <p className="sn-lead">{lead}</p>}
      {action}
    </div>
  );
}

export default Stage;
