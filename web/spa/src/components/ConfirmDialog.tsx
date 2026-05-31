import { useEffect, useRef } from "react";

export interface ConfirmState {
  title?: string;
  message: string;
  confirmLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
}

/** In-app confirmation modal — replaces the native window.confirm() so
 *  destructive actions match the design system. Esc / backdrop cancels,
 *  Enter confirms; the confirm button is focused on open. */
export function ConfirmDialog({
  state,
  onClose,
}: {
  state: ConfirmState | null;
  onClose: () => void;
}) {
  const confirmRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!state) return;
    confirmRef.current?.focus();
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "Enter") {
        e.preventDefault();
        state?.onConfirm();
        onClose();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [state, onClose]);

  if (!state) return null;

  const confirm = () => {
    state.onConfirm();
    onClose();
  };

  return (
    <div
      className="modal-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-label={state.title ?? "Confirm"}>
        {state.title && <div className="modal-title">{state.title}</div>}
        <div className="modal-msg">{state.message}</div>
        <div className="modal-actions">
          <button type="button" className="btn ghost" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            ref={confirmRef}
            className={`btn ${state.danger ? "danger" : "primary"}`}
            onClick={confirm}
          >
            {state.confirmLabel ?? "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}
