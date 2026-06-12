// KARAOKE — shared responsive-layout hooks (#184). The Marquee export does
// mobile by composing separate phone screens (karaoke-marquee.css has zero
// @media rules by design), so these production media-query hooks are a
// sanctioned invention — precedent: PHONE_QUERY in Perf.tsx (#156), moved
// here verbatim for app-wide reuse.

import { useEffect, useState } from "react";

// Phone (thumb-zone) vs desktop/TV layout — m-perf.jsx PhonePerf vs
// LaptopPerfBoard. Live matchMedia so rotation/resize re-lays-out.
export const PHONE_QUERY = "(max-width: 640px)";

export function usePhoneLayout(): boolean {
  const [phone, setPhone] = useState(
    () => typeof window !== "undefined" && !!window.matchMedia?.(PHONE_QUERY).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia?.(PHONE_QUERY);
    if (!mq) return;
    const onChange = () => setPhone(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return phone;
}

// Coarse (touch-first) pointer — same live matchMedia pattern. Gates the
// touch-target sizing and keyboard-hint copy: a phone-width desktop window
// keeps its keyboard hints, a tablet-width touchscreen loses them.
export const COARSE_QUERY = "(pointer: coarse)";

export function useCoarsePointer(): boolean {
  const [coarse, setCoarse] = useState(
    () => typeof window !== "undefined" && !!window.matchMedia?.(COARSE_QUERY).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia?.(COARSE_QUERY);
    if (!mq) return;
    const onChange = () => setCoarse(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return coarse;
}
