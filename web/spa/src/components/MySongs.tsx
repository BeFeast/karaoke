// My songs (#/, #/my-songs; Sonereli UI/UX v1 step 4). Continue singing on
// top, then songs being prepared, songs that need attention, and the ready
// list ordered by when they were last sung. No cost, receipts or job ids.

import { useMemo, useState } from "react";
import type { JobOut } from "../api";
import { t } from "../i18n";
import { canRetryJob, statusMeta } from "../jobStatus";
import { coverUrl, fmtClock, type LastPlayed, playedAt } from "../lib/library";
import { sourceDisplay } from "../lib/source";
import { catalogHash, itemHash } from "../router";
import { Icon } from "./Icon";
import { stageLabel, sourceProblem } from "./Processing";
import { MicMark } from "./Shell";

const SEARCH_FROM = 12;

function songTitle(job: JobOut): string {
  return job.title?.trim() || job.track?.trim() || sourceDisplay(job.source_url).label;
}

export function Cover({ sourceUrl, size = "md" }: { sourceUrl: string; size?: "md" | "lg" }) {
  const [failed, setFailed] = useState(false);
  const src = coverUrl(sourceUrl);
  return (
    <span className={`sn-cover${size === "lg" ? " sn-cover-lg" : ""}`}>
      {src && !failed ? <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setFailed(true)} />
        : <Icon name="note" />}
    </span>
  );
}

export function sortLibrary(jobs: JobOut[], played: Record<string, number>): JobOut[] {
  const time = (job: JobOut) => played[job.job_token] ?? 0;
  const done = (job: JobOut) => Date.parse(job.completed_at ?? job.created_at) || 0;
  return [...jobs].sort((a, b) => time(b) - time(a) || done(b) - done(a));
}

export function MySongs({ jobs, loaded, listError, actionError, last, catalogEnabled, onReload, onRetry, onRemove }: {
  jobs: JobOut[];
  loaded: boolean;
  listError: string | null;
  actionError: string | null;
  last: LastPlayed | null;
  catalogEnabled: boolean;
  onReload: () => void;
  onRetry: (job: JobOut) => void;
  onRemove: (job: JobOut) => void;
}) {
  const [query, setQuery] = useState("");
  const preparing = jobs.filter((job) => statusMeta(job.status).active);
  const attention = jobs.filter((job) => job.status === "failed" || job.status === "cancelled");
  const ready = useMemo(() => sortLibrary(jobs.filter((job) => job.status === "completed"), playedAt()), [jobs]);
  const needle = query.trim().toLowerCase();
  const shown = needle
    ? ready.filter((job) => [songTitle(job), job.artist ?? ""].some((value) => value.toLowerCase().includes(needle)))
    : ready;

  if (!loaded && jobs.length === 0) {
    return (
      <div className="sn-skeleton" data-my-songs aria-busy="true">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="sn-skel-row" aria-hidden="true">
            <span className="sn-skel sn-skel-cover" />
            <span className="sn-grow"><span className="sn-skel sn-skel-l1" /><span className="sn-skel sn-skel-l2" /></span>
          </div>
        ))}
        <p className="sn-hint sn-left" role="status">{t("home.loading")}</p>
      </div>
    );
  }

  if (listError && jobs.length === 0) {
    return (
      <div data-my-songs>
        <p className="sn-note is-err" role="alert"><Icon name="alert" size={20} />{t("home.error")}</p>
        <button type="button" className="sn-btn sn-block sn-gap-top" onClick={onReload}><Icon name="retry" size={20} />{t("action.retry")}</button>
      </div>
    );
  }

  const resume = last && last.position > 1 ? last : null;
  if (jobs.length === 0 && !resume) {
    return (
      <div className="sn-empty" data-my-songs>
        <MicMark size={84} />
        <h2 className="sn-display">{t("home.emptyTitle")}</h2>
        <p className="sn-lead">{t("home.emptyLead")}</p>
        {catalogEnabled && <a className="sn-btn" href={catalogHash()}><Icon name="catalog" size={20} />{t("home.browse")}</a>}
      </div>
    );
  }

  return (
    <div data-my-songs>
      {actionError && <p className="sn-note is-err" role="alert"><Icon name="alert" size={20} />{t("err.generic")}</p>}
      {resume && (
        <section className="sn-card sn-resume" aria-label={t("home.continue")}>
          <span className="sn-eyebrow">{t("home.continue")}</span>
          <div className="sn-resume-row">
            <Cover sourceUrl={resume.sourceUrl} size="lg" />
            <span className="sn-grow">
              <bdi className="sn-t1">{resume.title}</bdi>
              {resume.artist && <bdi className="sn-t2">{resume.artist}</bdi>}
            </span>
            <a className="sn-play-fab sn-resume-play" href={itemHash(resume.token, resume.position)}
              aria-label={t("home.resume", { title: resume.title, time: fmtClock(resume.position) })}>
              <Icon name="play" size={26} />
            </a>
          </div>
          <div className="sn-meter">
            <span className="sn-bar"><i style={{ width: `${resume.duration ? Math.min(100, (resume.position / resume.duration) * 100) : 0}%` }} /></span>
            <span className="sn-cyan">{fmtClock(resume.position)}</span>
            {resume.duration ? <span className="sn-muted">/ {fmtClock(resume.duration)}</span> : null}
          </div>
        </section>
      )}

      {preparing.length > 0 && (
        <>
          <h2 className="sn-section">{t("home.preparing")}</h2>
          <ul className="sn-list">
            {preparing.map((job) => (
              <li key={job.id}>
                <a className="sn-row sn-job" href={itemHash(job.job_token)}>
                  <Cover sourceUrl={job.source_url} />
                  <span className="sn-grow">
                    <bdi className="sn-t1">{songTitle(job)}</bdi>
                    <span className="sn-t2 sn-cyan">{stageLabel(job.status, job.progress)}</span>
                    <span className="sn-bar"><i style={{ width: `${Math.max(2, Math.min(100, job.progress))}%` }} /></span>
                  </span>
                  <Icon name="right" />
                </a>
              </li>
            ))}
          </ul>
        </>
      )}

      {attention.length > 0 && (
        <>
          <h2 className="sn-section">{t("home.attention")}</h2>
          <ul className="sn-list">
            {attention.map((job) => (
              <li key={job.id} className="sn-attention">
                <a className="sn-row sn-job is-failed" href={itemHash(job.job_token)}>
                  <Cover sourceUrl={job.source_url} />
                  <span className="sn-grow">
                    <bdi className="sn-t1">{songTitle(job)}</bdi>
                    <span className="sn-t2 sn-err">{job.status === "cancelled" ? t("home.cancelled") : t("home.couldntPrepare")}</span>
                  </span>
                  <Icon name="right" />
                </a>
                {job.status === "failed" && (
                  <p className="sn-attention-why">{sourceProblem(job.error) ? t("proc.subSource") : t("proc.subFailed")}</p>
                )}
                <div className="sn-inline-actions">
                  {canRetryJob(job) && (
                    <button type="button" className="sn-btn sn-btn-small" onClick={() => onRetry(job)}>
                      <Icon name="retry" size={20} />{t("action.retry")}
                    </button>
                  )}
                  <button type="button" className="sn-btn sn-btn-small sn-btn-ghost" onClick={() => onRemove(job)}>{t("action.remove")}</button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {ready.length > 0 && (
        <>
          <h2 className="sn-section">{t("home.ready")} <span className="sn-count">{ready.length}</span></h2>
          {ready.length >= SEARCH_FROM && (
            <label className="sn-search sn-search-inline">
              <Icon name="search" />
              <span className="sn-visually-hidden">{t("home.search")}</span>
              <input type="search" value={query} dir="auto" onChange={(event) => setQuery(event.target.value)}
                placeholder={t("home.search")} />
            </label>
          )}
          <ul className="sn-list">
            {shown.map((job) => {
              const title = songTitle(job);
              const meta = [job.artist, job.duration ? fmtClock(job.duration) : null].filter(Boolean).join(" · ");
              return (
                <li key={job.id} className="sn-ready-item">
                  <a className="sn-row" href={itemHash(job.job_token)} aria-label={t("home.open", { title })}>
                    <Cover sourceUrl={job.source_url} />
                    <span className="sn-grow">
                      <bdi className="sn-t1">{title}</bdi>
                      {meta && <bdi className="sn-t2">{meta}</bdi>}
                    </span>
                    <Icon name="play" className="sn-row-play" />
                  </a>
                  <button type="button" className="sn-iconbtn sn-row-more" aria-label={t("home.removeAction", { title })}
                    onClick={() => onRemove(job)}>
                    <Icon name="more" />
                  </button>
                </li>
              );
            })}
          </ul>
          {needle && shown.length === 0 && <p className="sn-hint sn-left">{t("home.noMatch")}</p>}
        </>
      )}
    </div>
  );
}
