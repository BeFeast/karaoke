// Horizontal rail drag for the Stage sliders (scrub, Original vocals).
// Mouse/pen use pointer events; touch uses an active (non-passive) touchstart
// so iOS never turns a hold into text selection, a callout or a page scroll
// (#209). The callback gets the position along the rail as 0..1, always
// left-to-right: the rails are LTR in every interface language.

import { type RefObject, useRef } from "react";
import { useActiveTouchStart } from "./touchGuards";

export interface RailDragHandlers {
  onStart?: (fraction: number) => void;
  onMove: (fraction: number) => void;
  onEnd?: (fraction: number) => void;
}

export function railFraction(clientX: number, rect: { left: number; width: number }): number {
  if (rect.width <= 0) return 0;
  return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
}

export function useRailDrag<T extends HTMLElement>(ref: RefObject<T>, handlers: RailDragHandlers) {
  const h = useRef(handlers);
  h.current = handlers;
  const at = (clientX: number) => railFraction(clientX, ref.current?.getBoundingClientRect() ?? { left: 0, width: 0 });

  useActiveTouchStart(ref, (event) => {
    const touch = event.touches[0];
    if (!touch) return;
    event.preventDefault();
    let last = at(touch.clientX);
    h.current.onStart?.(last);
    h.current.onMove(last);
    const move = (ev: TouchEvent) => {
      ev.preventDefault();
      const next = ev.touches[0] ?? ev.changedTouches[0];
      if (!next) return;
      last = at(next.clientX);
      h.current.onMove(last);
    };
    const up = () => {
      window.removeEventListener("touchmove", move);
      window.removeEventListener("touchend", up);
      window.removeEventListener("touchcancel", up);
      h.current.onEnd?.(last);
    };
    window.addEventListener("touchmove", move, { passive: false });
    window.addEventListener("touchend", up);
    window.addEventListener("touchcancel", up);
  });

  const onPointerDown = (event: React.PointerEvent<T>) => {
    if (event.pointerType === "touch" || event.button !== 0) return;
    event.preventDefault();
    let last = at(event.clientX);
    h.current.onStart?.(last);
    h.current.onMove(last);
    const move = (ev: PointerEvent) => {
      last = at(ev.clientX);
      h.current.onMove(last);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      h.current.onEnd?.(last);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  };
  return { onPointerDown };
}
