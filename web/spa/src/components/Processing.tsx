// Processing (#/job/:token while the job is not ready; Sonereli UI/UX v1
// step 3). Plain stage names instead of worker notes, no ETA, no cost. The
// Stage owns the data (REST poll + the owner's WebSocket) and swaps this
// screen for the player after Start singing.

import type { CSSProperties } from "react";
import type { JobOut, JobStatus } from "../api";
import { t, type StringKey } from "../i18n";
import { coverUrl } from "../lib/library";
import { addHash, mySongsHash, navigate } from "../router";
import type { WsJobStatus } from "../ws";
import { Icon } from "./Icon";

const STAGES: { status: JobStatus; label: StringKey }[] = [
  { status: "downloading", label: "proc.download" },
  { status: "separating", label: "proc.separate" },
  { status: "transcribing", label: "proc.lyrics" },
];

export type ProcessingPhase = "queued" | "running" | "ready" | "failed" | "cancelled";

export function processingPhase(status: WsJobStatus): ProcessingPhase {
  if (status === "queued") return "queued";
  if (status === "completed") return "ready";
  if (status === "failed" || status === "cancelled") return status;
  return "running";
}

/** Index of the stage in progress; STAGES.length once everything is done. */
export function stageIndex(status: WsJobStatus): number {
  if (status === "queued") return -1;
  if (status === "finalizing" || status === "completed") return STAGES.length;
  const index = STAGES.findIndex((stage) => stage.status === status);
  return index < 0 ? -1 : index;
}

/** Private, removed or region-locked sources read differently from crashes. */
export function sourceProblem(error: string | null | undefined): boolean {
  return /private|unavailable|removed|not available|sign in|copyright|region|geo|age|download|404|403/i.test(error ?? "");
}

export function stageLabel(status: WsJobStatus, progress: number): string {
  const index = stageIndex(status);
  if (status === "queued") return t("proc.queued");
  if (status === "finalizing") return t("proc.final");
  const stage = STAGES[index];
  return stage ? `${t(stage.label)} · ${Math.round(progress)}%` : t("proc.running");
}

export interface ProcessingView {
  status: WsJobStatus;
  progress: number;
  title: string;
  artist: string | null;
  sourceUrl: string;
  error: string | null;
}

export function Processing({ view, owner, failedStage, staleSeconds, onStart, onCancel, onRetry }: {
  view: ProcessingView;
  /** The owner's job row: enables Cancel and Try again. Guests only watch. */
  owner: JobOut | null;
  /** The stage that was running when the job failed (for the red row). */
  failedStage: number;
  /** Seconds since the last update while the live socket reconnects. */
  staleSeconds: number | null;
  onStart: () => void;
  onCancel: () => void;
  onRetry: () => void;
}) {
  const phase = processingPhase(view.status);
  const current = stageIndex(view.status);
  const pct = Math.max(0, Math.min(100, Math.round(view.progress)));
  const cover = coverUrl(view.sourceUrl);
  const heading = {
    queued: t("proc.queued"), running: t("proc.running"), ready: t("proc.ready"),
    failed: t("proc.failed"), cancelled: t("proc.cancelled"),
  }[phase];
  const sub = {
    queued: t("proc.subQueued"), running: t("proc.subRunning"), ready: t("proc.subReady"),
    failed: sourceProblem(view.error) ? t("proc.subSource") : t("proc.subFailed"),
    cancelled: t("proc.subCancelled"),
  }[phase];
  const display = phase === "queued" || phase === "running" || phase === "ready";

  return (
    <div className="sn-app sn-modal sn-processing" data-phase={phase}>
      <div className="sn-navbar">
        <button type="button" className="sn-iconbtn" aria-label={t("action.back")} onClick={() => navigate(mySongsHash)}>
          <Icon name="left" />
        </button>
        <span className="sn-grow" />
        {owner && (phase === "queued" || phase === "running") && (
          <button type="button" className="sn-btn sn-btn-small sn-btn-ghost sn-proc-cancel" onClick={onCancel}>
            {t("action.cancel")}
          </button>
        )}
      </div>
      <main className="sn-screen">
        <div className="sn-proc-hero">
          <span className="sn-cover sn-cover-xl">{cover ? <img src={cover} alt="" referrerPolicy="no-referrer" /> : <Icon name="note" size={40} />}</span>
          <span className="sn-grow">
            <bdi className="sn-t1 sn-wrap">{view.title}</bdi>
            {view.artist && <bdi className="sn-t2">{view.artist}</bdi>}
          </span>
        </div>
        <h1 className={display ? "sn-display sn-proc-title" : "sn-proc-plain"} tabIndex={-1}>{heading}</h1>
        <p className={`sn-lead${phase === "failed" ? " sn-err" : ""}`}>{sub}</p>
        {staleSeconds !== null && (phase === "queued" || phase === "running") && (
          <p className="sn-note is-warn" role="status"><Icon name="alert" size={20} />{t("proc.reconnecting", { s: staleSeconds })}</p>
        )}
        <ol className="sn-steps" aria-label={t("proc.stages")}>
          {STAGES.map((stage, index) => {
            const done = phase === "ready" || index < current;
            const failed = (phase === "failed" || phase === "cancelled") && index === failedStage;
            const now = phase === "running" && index === current;
            const state = done ? "done" : failed ? "fail" : now ? "now" : "todo";
            return (
              <li key={stage.status} className={`sn-step is-${state}`} data-stage={stage.status}
                aria-current={now ? "step" : undefined} style={now ? { "--p": pct } as CSSProperties : undefined}>
                <span className="sn-dot">{done ? <Icon name="check" size={16} /> : failed ? <Icon name="x" size={16} /> : null}</span>
                <span className="sn-step-name">
                  {t(stage.label)}
                  {phase === "queued" && index === 0 && <span className="sn-step-sub">{t("proc.queued")}</span>}
                </span>
                <span className="sn-step-val">{done ? t("proc.done") : failed ? t("proc.stopped") : now ? `${pct}%` : ""}</span>
              </li>
            );
          })}
          <li className={`sn-step is-${phase === "ready" ? "done" : view.status === "finalizing" ? "now" : "todo"}`} data-stage="finalizing"
            style={view.status === "finalizing" ? { "--p": 100 } as CSSProperties : undefined}>
            <span className="sn-dot">{phase === "ready" ? <Icon name="check" size={16} /> : null}</span>
            <span className="sn-step-name">{t("proc.final")}</span>
            <span className="sn-step-val">{phase === "ready" ? t("proc.done") : ""}</span>
          </li>
        </ol>
        {(phase === "queued" || phase === "running") && (
          <p className="sn-note"><Icon name="songs" size={20} />{t("proc.hint")}</p>
        )}
      </main>
      <footer className="sn-footer sn-footer-stack">
        {phase === "ready" ? (
          <button type="button" className="sn-btn sn-btn-primary sn-block sn-start-singing" onClick={onStart} autoFocus>
            <Icon name="play" size={22} />{t("action.sing")}
          </button>
        ) : phase === "failed" ? (
          <>
            <button type="button" className="sn-btn sn-btn-primary sn-block" onClick={() => navigate(addHash)}>{t("proc.tryAnother")}</button>
            {owner && <button type="button" className="sn-btn sn-block" onClick={onRetry}><Icon name="retry" size={20} />{t("action.retry")}</button>}
          </>
        ) : phase === "cancelled" ? (
          owner && <button type="button" className="sn-btn sn-btn-primary sn-block" onClick={onRetry}>{t("proc.prepareAgain")}</button>
        ) : (
          <button type="button" className="sn-btn sn-block" onClick={() => navigate(mySongsHash)}>{t("proc.keepBrowsing")}</button>
        )}
      </footer>
    </div>
  );
}
