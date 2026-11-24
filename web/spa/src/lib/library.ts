// Per-device listening memory for My songs (Sonereli UI/UX v1 step 4): the
// last song sung and where it stopped (Continue singing), and when each song
// was last opened (list order). localStorage only; losing it just hides the
// Continue card and falls back to completion order.

import { youtubeId } from "./songLink";

export interface LastPlayed {
  token: string;
  title: string;
  artist: string | null;
  sourceUrl: string;
  position: number;
  duration: number | null;
  at: number;
}

const LAST_KEY = "sonereli-last-played";
const PLAYED_KEY = "sonereli-played-at";

function read<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

function write(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* storage unavailable: nothing to remember */
  }
}

export function loadLastPlayed(): LastPlayed | null {
  const value = read<LastPlayed>(LAST_KEY);
  return value && typeof value.token === "string" && Number.isFinite(value.position) ? value : null;
}

export function saveLastPlayed(value: LastPlayed): void {
  write(LAST_KEY, value);
  const played = read<Record<string, number>>(PLAYED_KEY) ?? {};
  played[value.token] = value.at;
  // Keep the map small: the 200 most recent songs are plenty for ordering.
  const recent = Object.entries(played).sort((a, b) => b[1] - a[1]).slice(0, 200);
  write(PLAYED_KEY, Object.fromEntries(recent));
}

export function forgetSong(token: string): void {
  if (loadLastPlayed()?.token === token) {
    try {
      localStorage.removeItem(LAST_KEY);
    } catch {
      /* ignore */
    }
  }
}

export function playedAt(): Record<string, number> {
  return read<Record<string, number>>(PLAYED_KEY) ?? {};
}

/** Cover art for a YouTube source (the catalog uses the same thumbnails). */
export function coverUrl(sourceUrl: string): string | null {
  const id = youtubeId(sourceUrl.split("#")[0]);
  return id ? `https://i.ytimg.com/vi/${id}/mqdefault.jpg` : null;
}

export function fmtClock(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "0:00";
  const whole = Math.floor(seconds);
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}
