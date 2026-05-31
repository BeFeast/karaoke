import { useEffect } from "react";

/** Lightweight transient toast (e.g. "Link copied"). Auto-dismisses. */
export function Toast({
  message,
  onDone,
  duration = 1800,
}: {
  message: string | null;
  onDone: () => void;
  duration?: number;
}) {
  useEffect(() => {
    if (!message) return;
    const id = window.setTimeout(onDone, duration);
    return () => window.clearTimeout(id);
  }, [message, onDone, duration]);

  if (!message) return null;
  return (
    <div className="toast" role="status" aria-live="polite">
      {message}
    </div>
  );
}
