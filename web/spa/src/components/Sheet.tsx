// Bottom sheet (Sonereli UI/UX v1): a modal <dialog>, so focus stays inside
// and Escape closes it. Tapping the scrim closes it too.

import { type ReactNode, useEffect, useRef } from "react";
import { t } from "../i18n";
import { Icon } from "./Icon";

export function Sheet({ open, title, onClose, children, className }: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement | null>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className={`sn-sheet${className ? ` ${className}` : ""}`}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {open && (
        <div className="sn-sheet-body">
          <span className="sn-sheet-grip" aria-hidden="true" />
          <div className="sn-sheet-head">
            <h2 className="sn-sheet-title"><bdi>{title}</bdi></h2>
            <button type="button" className="sn-iconbtn sn-sheet-close" onClick={onClose} aria-label={t("action.close")}>
              <Icon name="x" size={22} />
            </button>
          </div>
          {children}
        </div>
      )}
    </dialog>
  );
}
