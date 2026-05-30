import type { JobOut } from "../api";
import { StatusChip } from "./StatusChip";

function shareHref(job: JobOut, publicBaseUrl: string): string {
  // Prefer the server-built share_url; fall back to public base + token.
  if (job.share_url) return job.share_url;
  return `${publicBaseUrl.replace(/\/$/, "")}/share/${job.job_token}`;
}

function JobRow({ job, publicBaseUrl }: { job: JobOut; publicBaseUrl: string }) {
  const label = job.title || job.source_url;
  return (
    <div className="job">
      <div className="job-top">
        <span className="job-title" title={label}>
          {label}
        </span>
        <span className="job-meta">
          <StatusChip status={job.status} />
        </span>
      </div>
      <div className="progress">
        <span style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} />
      </div>
      <div className="share-link">
        <a href={shareHref(job, publicBaseUrl)} target="_blank" rel="noopener noreferrer">
          → share
        </a>
      </div>
      {job.status === "failed" && job.error && (
        <div className="job-error">{job.error}</div>
      )}
    </div>
  );
}

export function JobList({
  jobs,
  publicBaseUrl,
}: {
  jobs: JobOut[];
  publicBaseUrl: string;
}) {
  if (jobs.length === 0) {
    return <div className="empty">No jobs yet — submit a URL above.</div>;
  }
  return (
    <div className="jobs">
      {jobs.map((job) => (
        <JobRow key={job.id} job={job} publicBaseUrl={publicBaseUrl} />
      ))}
    </div>
  );
}
