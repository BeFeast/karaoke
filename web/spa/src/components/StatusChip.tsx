import type { JobStatus } from "../api";
import { statusMeta } from "../jobStatus";

const CHIP_CLASS: Record<string, string> = {
  ok: "chip ok",
  err: "chip err",
  run: "chip run",
  info: "chip info",
  neutral: "chip",
};

export function StatusChip({ status }: { status: JobStatus }) {
  const meta = statusMeta(status);
  return (
    <span className={CHIP_CLASS[meta.chip]}>
      {meta.spinner ? (
        <span className="spinner" aria-hidden />
      ) : (
        <span className="dot" aria-hidden />
      )}
      {meta.label}
    </span>
  );
}
