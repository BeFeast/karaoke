import type { JobOut } from "../api";
import { ARTIFACTS, artifactHref, canRetry, sourceLabel, statusMeta } from "../jobStatus";
import { formatRelativeTime } from "../lib/jobListUtils";
import { itemHash } from "../router";
import { StatusChip } from "./StatusChip";

export interface JobActions {
  onDelete: (job: JobOut) => void;
  onCancel: (job: JobOut) => void;
  onRetry: (job: JobOut) => void;
}

function JobCard({ job, actions }: { job: JobOut; actions: JobActions }) {
  const meta = statusMeta(job.status);
  const label = job.title?.trim() || job.source_url;
  const pct = Math.max(0, Math.min(100, Math.round(job.progress)));
  const indeterminate = meta.active && pct <= 0;
  // Null for a missing/unparseable timestamp — the span simply isn't rendered.
  const createdAgo = formatRelativeTime(job.created_at);

  return (
    <div className="job">
      <div className="job-num">#{job.id}</div>
      <div className="job-body">
        <div className="job-meta-top">
          <StatusChip status={job.status} />
          <a
            className="job-source"
            href={job.source_url}
            target="_blank"
            rel="noopener noreferrer"
            title={job.source_url}
          >
            {sourceLabel(job.source_url)} ↗
          </a>
          {createdAgo && (
            <span className="job-time" title={job.created_at}>
              {createdAgo}
            </span>
          )}
        </div>

        <h3 className="job-title" title={label}>
          {label}
        </h3>

        {meta.active && (
          <>
            <div className={`progressbar active${indeterminate ? " indeterminate" : ""}`}>
              <div style={{ width: indeterminate ? undefined : `${pct}%` }} />
            </div>
            <div className="stage-note">
              {job.stage_note || meta.note}
              {!indeterminate && ` · ${pct}%`}
            </div>
          </>
        )}

        {job.status === "failed" && job.error && (
          <div className="job-error">
            <div className="err-label">error</div>
            {job.error}
          </div>
        )}

        <div className="job-foot">
          {job.status === "completed" && (
            <>
              <a className="btn sm" href={itemHash(job.job_token)}>
                ▶ Open
              </a>
              <span className="artifact-links">
                {ARTIFACTS.map((a, i) => (
                  <span key={a.name}>
                    {i > 0 && <span className="sep">·</span>}
                    <a href={artifactHref(job.job_token, a.name)} target="_blank" rel="noopener noreferrer">
                      {a.label}
                    </a>
                  </span>
                ))}
              </span>
            </>
          )}

          {meta.active && (
            <button type="button" className="link-btn" onClick={() => actions.onCancel(job)}>
              Cancel
            </button>
          )}

          {canRetry(job.status) && (
            <button type="button" className="link-btn" onClick={() => actions.onRetry(job)}>
              ↻ Retry
            </button>
          )}

          {!meta.active && (
            <>
              <span className="spacer" />
              <button
                type="button"
                className="link-btn danger"
                onClick={() => actions.onDelete(job)}
                title="Remove job + artifacts"
              >
                ✕ Remove
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function JobsSkeleton() {
  return (
    <div className="jobs" aria-hidden>
      {[0, 1, 2].map((i) => (
        <div className="skel-job" key={i}>
          <span className="skel skel-line s" />
          <div>
            <span className="skel skel-line s" />
            <span className="skel skel-line l" />
            <span className="skel skel-line m" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function JobList({
  jobs,
  loading,
  actions,
  emptyTitle = "No jobs yet",
  emptySub = "Paste a URL above to split vocals and transcribe the lyrics.",
}: {
  jobs: JobOut[];
  loading: boolean;
  actions: JobActions;
  emptyTitle?: string;
  emptySub?: string;
}) {
  if (loading && jobs.length === 0) {
    return <JobsSkeleton />;
  }
  if (jobs.length === 0) {
    return (
      <div className="empty">
        <div className="empty-title">{emptyTitle}</div>
        <div>{emptySub}</div>
      </div>
    );
  }
  return (
    <div className="jobs">
      {jobs.map((job) => (
        <JobCard key={job.id} job={job} actions={actions} />
      ))}
    </div>
  );
}
