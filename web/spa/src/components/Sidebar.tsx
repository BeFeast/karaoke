import type { JobOut } from "../api";
import { statusMeta } from "../jobStatus";
import { settingsHash } from "../router";

export type JobFilter = "all" | "active" | "completed" | "failed";

const FILTERS: { key: JobFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "active", label: "Active" },
  { key: "completed", label: "Completed" },
  { key: "failed", label: "Failed" },
];

export function jobMatchesFilter(job: JobOut, filter: JobFilter): boolean {
  switch (filter) {
    case "active":
      return statusMeta(job.status).active;
    case "completed":
      return job.status === "completed";
    case "failed":
      return job.status === "failed";
    default:
      return true;
  }
}

function counts(jobs: JobOut[]): Record<JobFilter, number> {
  return {
    all: jobs.length,
    active: jobs.filter((j) => statusMeta(j.status).active).length,
    completed: jobs.filter((j) => j.status === "completed").length,
    failed: jobs.filter((j) => j.status === "failed").length,
  };
}

export function Sidebar({
  jobs,
  filter,
  onFilter,
}: {
  jobs: JobOut[];
  filter: JobFilter;
  onFilter: (f: JobFilter) => void;
}) {
  const c = counts(jobs);
  const finished = c.completed + c.failed;
  const successRate = finished > 0 ? Math.round((c.completed / finished) * 100) : null;

  return (
    <aside className="sidebar">
      <div className="nav-section">Jobs</div>
      {FILTERS.map((f) => (
        <button
          type="button"
          key={f.key}
          className={`nav-item${filter === f.key ? " active" : ""}`}
          onClick={() => onFilter(f.key)}
        >
          <span className="dot" aria-hidden />
          {f.label}
          <span className="count">{c[f.key]}</span>
        </button>
      ))}

      <div className="nav-section">Pipeline</div>
      <div className="stat-rows">
        <div className="stat-row">
          jobs<span className="stat-val">{c.all}</span>
        </div>
        <div className="stat-row">
          in flight<span className="stat-val">{c.active}</span>
        </div>
        <div className="stat-row">
          success<span className="stat-val">{successRate === null ? "—" : `${successRate}%`}</span>
        </div>
      </div>

      <div className="nav-section">Account</div>
      <a className="nav-item" href={settingsHash()}>
        <span className="dot" aria-hidden />
        Settings
      </a>
    </aside>
  );
}
