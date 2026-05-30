import type { JobStatus } from "../api";

const COLORS: Record<JobStatus, string> = {
  queued: "#6b7280",
  downloading: "#0ea5e9",
  separating: "#6366f1",
  transcribing: "#8b5cf6",
  completed: "#16a34a",
  failed: "#dc2626",
  cancelled: "#9ca3af",
};

export function StatusChip({ status }: { status: JobStatus }) {
  return (
    <span className="status-chip" style={{ backgroundColor: COLORS[status] ?? "#6b7280" }}>
      {status}
    </span>
  );
}
