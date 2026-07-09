import { useEffect, useRef, type RefObject } from "react";

export function useActiveTouchStart<T extends HTMLElement>(
  ref: RefObject<T>,
  onTouchStart: (event: TouchEvent) => void,
) {
  const onTouchStartRef = useRef(onTouchStart);
  onTouchStartRef.current = onTouchStart;

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const listener = (event: TouchEvent) => onTouchStartRef.current(event);
    el.addEventListener("touchstart", listener, { passive: false });
    return () => el.removeEventListener("touchstart", listener);
  }, [ref]);
}

