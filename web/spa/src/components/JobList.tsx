import type { JobOut } from "../api";
import { sourceLabel, statusMeta } from "../jobStatus";
import { StatusChip } from "./StatusChip";

function shareHref(job: JobOut, publicBaseUrl: string): string {
  // Prefer the server-built share_url; fall back to public base + token.
  if (job.share_url) return job.share_url;
  return `${publicBaseUrl.replace(/\/$/, "")}/share/${job.job_token}`;
}

function JobCard({ job, publicBaseUrl }: { job: JobOut; publicBaseUrl: string }) {
  const meta = statusMeta(job.status);
  const label = job.title?.trim() || job.source_url;
  const pct = Math.max(0, Math.min(100, Math.round(job.progress)));
  const indeterminate = meta.active && pct <= 0;

  return (
    <div className="job">
      <div className="job-num">#{job.id}</div>
      <div className="job-body">
        <div className="job-meta-top">
          <StatusChip status={job.status} />
          <span className="job-source" title={job.source_url}>
            {sourceLabel(job.source_url)}
          </span>
        </div>

        <h3 className="job-title" title={label}>
          {label}
        </h3>

        {meta.active && (
          <>
            <div className={`progressbar active${indeterminate ? " indeterminate" : ""}`}>
              <div style={{ width: indeterminate ? undefined : `${pct}%` }} />
            </div>
            <div className="job-foot">
              <span className="stage-note">
                {meta.note}
                {!indeterminate && ` · ${pct}%`}
              </span>
            </div>
          </>
        )}

        {job.status === "completed" && (
          <div className="job-foot">
            <a
              className="share-link"
              href={shareHref(job, publicBaseUrl)}
              target="_blank"
              rel="noopener noreferrer"
            >
              → Open share page
            </a>
          </div>
        )}

        {job.status === "failed" && job.error && (
          <div className="job-error">
            <div className="err-label">error</div>
            {job.error}
          </div>
        )}
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
  publicBaseUrl,
  emptyTitle = "No jobs yet",
  emptySub = "Paste a URL above to split vocals and transcribe the lyrics.",
}: {
  jobs: JobOut[];
  loading: boolean;
  publicBaseUrl: string;
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
        <JobCard key={job.id} job={job} publicBaseUrl={publicBaseUrl} />
      ))}
    </div>
  );
}
