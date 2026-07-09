// KARAOKE — the Stage: per-song player room on #/job/:token (Marquee port,
// #154). Literal port of design/claude-export/proto/stage.jsx StageScreen
// (:91-161) — TransportBar/ConsoleModule/SetlistModule live in
// player/KaraokePlayer.tsx around the untouched engine API. The DOM tree and
// inline styles are the design's; the adaptations wire real data only:
// SharePayload (incl. the new completed_at/source_url), the structured lyrics
// payload, real artifact downloads, and the room-scoped ◐ day/night theme
// (booth rooms stay always light). Recorded deviations: cost + receipt are
// OMITTED on this page (not in SharePayload — anonymous share viewers must
// not see cost); the ⤢ Performance control opens the #156 fullscreen overlay
// (components/Perf.tsx), mounted inside the player so the engine persists.

import { type CSSProperties, lazy, type ReactNode, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getLyrics, getShare, type LyricsPayload, type SharePayload } from "../api";
import { artifactHref, artifactView, statusMeta } from "../jobStatus";
import { fmtDuration, formatRelativeTime } from "../lib/jobListUtils";
import { useCoarsePointer, usePhoneLayout } from "../lib/layout";
import { compactUrlLabel, sourceDisplay } from "../lib/source";
import { goDashboard, itemUrl } from "../router";
import { type StageTheme, useStageTheme } from "../theme";
import { MBulbs, MicMark } from "./marks";
import { timeLines } from "./stage-core";
import { parseLrc, SyncedLyrics } from "./SyncedLyrics";
import { Toast } from "./Toast";

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

export function Stage({ token }: { token: string }) {
  const [payload, setPayload] = useState<SharePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [lyrics, setLyrics] = useState<LyricsPayload | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [view, setViewRaw] = useState<StageView>(initialView);
  const [theme, toggleTheme] = useStageTheme();
  const phone = usePhoneLayout();
  const timer = useRef<number | null>(null);

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

  // Structured lyrics (LRC + plain + provenance) once the job is complete.
  useEffect(() => {
    if (payload?.status === "completed") {
      getLyrics(token)
        .then(setLyrics)
        .catch(() => setLyrics(null));
    }
  }, [payload?.status, token]);

  // Same-origin copy path — the public base 401s on the LAN (#131).
  const onCopyLink = useCallback(async () => {
    const url = itemUrl(token);
    try {
      await navigator.clipboard.writeText(url);
      setToast("Link copied");
    } catch {
      // Clipboard API blocked (insecure context / permissions) — fall back.
      window.prompt("Copy this link:", url);
    }
  }, [token]);

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
  }, []);
  const onSeek = useCallback((time: number) => {
    seekRef.current?.(time);
  }, []);
  const reducedMotion = useMemo(
    () =>
      typeof window !== "undefined" &&
      !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,
    [],
  );

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
        lyrics={lyrics}
        view={view}
        setView={setView}
        theme={theme}
        onToggleTheme={toggleTheme}
        currentTime={currentTime}
        onTime={onTime}
        seekRef={seekRef}
        onSeek={onSeek}
        reducedMotion={reducedMotion}
      />
    );
  }

  // StageScreen (stage.jsx:91-161): the room container flips .m-booth (day) /
  // .m-stage (night) — the FINAL token sets are baked into those classes, so
  // the ◐ toggle is a class swap scoped to THIS room only.
  return (
    <div style={{ height: "100%", width: "100%", overflow: "auto", overflowX: "hidden" }}>
      <div className={theme === "day" ? "m-booth" : "m-stage"} style={{ minHeight: "100%", width: "100%", minWidth: 0, display: "flex", flexDirection: "column" }}>
        {/* phone (#184): the row wraps instead of overflowing — same elements,
            same order; the desktop branch is the untouched port. */}
        <div style={phone
          ? { display: "flex", alignItems: "center", gap: 12, padding: "8px 14px", minHeight: 54, borderBottom: "1px solid var(--border)", flexShrink: 0, flexWrap: "wrap", minWidth: 0 }
          : { display: "flex", alignItems: "center", gap: 12, padding: "0 22px", height: 54, borderBottom: "1px solid var(--border)", flexShrink: 0, minWidth: 0 }}>
          <button className="m-btn sm ghost" type="button" onClick={goDashboard}>← booth</button>
          <MicMark size={20} />
          <span style={{ flex: 1, minWidth: 0 }}></span>
          <span className="m-chip info">unlisted share</span>
          <button className="m-btn sm" type="button" onClick={() => void onCopyLink()}>⧉ Copy link</button>
          <button className="m-btn sm ghost" type="button" title="Day / night" onClick={toggleTheme}>◐</button>
        </div>

        <div style={{ flex: 1, maxWidth: 760, width: "100%", margin: "0 auto", padding: phone ? "18px 14px 18px" : "22px 24px 20px", display: "flex", flexDirection: "column", gap: 16, minHeight: 0, minWidth: 0 }}>
          {content}
        </div>
        <Toast message={toast} onDone={() => setToast(null)} />
      </div>
    </div>
  );
}

function StageBody({
  payload,
  token,
  lyrics,
  view,
  setView,
  theme,
  onToggleTheme,
  currentTime,
  onTime,
  seekRef,
  onSeek,
  reducedMotion,
}: {
  payload: SharePayload;
  token: string;
  lyrics: LyricsPayload | null;
  view: StageView;
  setView: (v: StageView) => void;
  theme: StageTheme;
  /** Flip the room's persisted ◐ theme — the perf overlay shares it (#156). */
  onToggleTheme: () => void;
  currentTime: number;
  onTime: (t: number) => void;
  seekRef: React.MutableRefObject<((time: number) => void) | null>;
  onSeek: (time: number) => void;
  reducedMotion: boolean;
}) {
  const meta = statusMeta(payload.status);
  const isComplete = payload.status === "completed";
  const source = sourceDisplay(payload.source_url);
  // Mobile adaptations (#184): phone shrinks the title, coarse pointers get
  // bigger view-toggle hit targets. No m-* mobile source for this page — the
  // desktop Marquee structure is the design; these only resize/rewrap it.
  const phone = usePhoneLayout();
  const coarse = useCoarsePointer();
  const title = payload.title?.trim() || `Job ${payload.job_token.slice(0, 8)}`;
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
  const timed = useMemo(() => timeLines(lines, payload.duration), [lines, payload.duration]);

  // Meta row, real data only: duration · split <relative completed_at>.
  // Cost + receipt are deliberately absent (recorded deviation — they are not
  // in SharePayload; anonymous share viewers must not see cost).
  const completedRel = formatRelativeTime(payload.completed_at);
  const metaBits = [fmtDuration(payload.duration), completedRel ? `split ${completedRel}` : null]
    .filter(Boolean)
    .join(" · ");

  return (
    <>
      {/* header block: meta row + title + view toggle (stage.jsx:105-124) */}
      <div style={{ display: "flex", alignItems: "flex-end", gap: 14, flexWrap: "wrap", minWidth: 0 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="m-mono" style={{ fontSize: 11, color: "var(--muted)", display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
            {metaBits && <span>{metaBits}</span>}
            {payload.owner_display_name && <span style={{ minWidth: 0, overflowWrap: "anywhere" }}>shared by {payload.owner_display_name}</span>}
            {/* uploads (#173) have no external source — show the filename, no link.
                Single-line ellipsis on both widths (#184): the raw URL used to
                wrap across ~4 lines; the full URL stays in the href. */}
            {source.kind === "url" ? (
              <a href={payload.source_url} target="_blank" rel="noopener" className="m-mono" style={{ color: "var(--info)", textDecoration: "none", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>
                source: {compactUrlLabel(source.label)} ↗
              </a>
            ) : (
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "100%" }}>source: {source.label}</span>
            )}
          </div>
          <h1 style={{ margin: "6px 0 0", fontFamily: "var(--font-display)", fontWeight: 650, fontSize: phone ? 22 : 28, letterSpacing: "-0.02em", lineHeight: 1.1, overflowWrap: "anywhere" }}>{title}</h1>
          {(payload.artist || payload.album) && (
            <div style={{ marginTop: 2, fontSize: 13, color: "var(--muted)", overflowWrap: "anywhere" }}>
              {[payload.artist, payload.album].filter(Boolean).join(" — ")}
            </div>
          )}
        </div>
        {/* view toggle */}
        {isComplete && (
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
            onTime={onTime}
            seekRef={seekRef}
            view={view}
            lines={timed}
            theme={theme}
            title={title}
            artist={payload.artist}
            plainLyrics={lyrics?.plain ?? null}
            onToggleTheme={onToggleTheme}
          />
        </Suspense>
      )}
      {isComplete && !instrumental && (
        <StageEmpty title="No audio tracks" sub="No audio tracks were produced for this job." />
      )}

      {/* full synced-lyrics panel (LRC highlight + click-to-seek, #59/#145) */}
      {isComplete && (
        <SyncedLyrics
          lrc={lyrics?.lrc ?? null}
          source={lyrics?.source ?? null}
          plainLyrics={lyrics?.plain ?? null}
          currentTime={currentTime}
          onSeek={onSeek}
          reducedMotion={reducedMotion}
        />
      )}

      {/* take home (stage.jsx:150-157) — real downloads via artifactHref */}
      {isComplete && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: "auto", borderTop: "1px dashed var(--border)", paddingTop: 13, flexWrap: "wrap", minWidth: 0 }}>
          <span className="m-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)", marginRight: 6 }}>take home</span>
          {vocals && <a className="m-btn sm" href={vocals.href} download><span className="m-stem vox"></span>vocals.mp3</a>}
          {instrumental && <a className="m-btn sm" href={instrumental.href} download><span className="m-stem inst"></span>karaoke.mp3</a>}
          {lyrics?.synced ? (
            <a className="m-btn sm" href={artifactHref(token, "lyrics.lrc")} download>≡ lyrics.lrc</a>
          ) : lyricsFile ? (
            <a className="m-btn sm" href={lyricsFile.href} download>≡ {lyricsFile.name}</a>
          ) : null}
          <span style={{ flex: 1, minWidth: 0 }}></span>
          {source.kind === "url" && (
            <a className="m-btn sm ghost" href={payload.source_url} target="_blank" rel="noopener">▶ original ↗</a>
          )}
        </div>
      )}
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
