// Original vocals level memory (Sonereli UI/UX v1 step 9): the starting
// level comes from Settings (25% unless changed) and each song remembers its
// own. Levels are whole percents, 0–100. localStorage only.

export const DEFAULT_VOCAL_PCT = 25;
const DEFAULT_KEY = "sonereli-vocals-default";
const SONG_KEY = "sonereli-vocals-by-song";

/** Slider detents: a drag within this many percent of 0/50/100 snaps to it. */
export const DETENTS = [0, 50, 100];
const DETENT_PULL = 3;

export function clampPct(pct: number): number {
  return Math.max(0, Math.min(100, Math.round(pct)));
}

export function snapDetent(pct: number): number {
  const value = clampPct(pct);
  for (const detent of DETENTS) if (Math.abs(value - detent) <= DETENT_PULL) return detent;
  return value;
}

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}

export function defaultVocalPct(): number {
  const raw = read(DEFAULT_KEY);
  const value = raw === null ? NaN : Number(raw);
  return Number.isFinite(value) ? clampPct(value) : DEFAULT_VOCAL_PCT;
}

export function saveDefaultVocalPct(pct: number): void {
  write(DEFAULT_KEY, String(clampPct(pct)));
}

function songLevels(): Record<string, number> {
  try {
    const value = JSON.parse(read(SONG_KEY) ?? "{}");
    return value && typeof value === "object" ? value : {};
  } catch {
    return {};
  }
}

/** The level this song was last sung with, else the Settings default. */
export function songVocalPct(token: string): number {
  const value = songLevels()[token];
  return typeof value === "number" && Number.isFinite(value) ? clampPct(value) : defaultVocalPct();
}

export function saveSongVocalPct(token: string, pct: number): void {
  const levels = songLevels();
  delete levels[token]; // re-insert last: the map keeps the most recent songs
  levels[token] = clampPct(pct);
  // Keep the map small: the 200 most recent writes are plenty.
  const entries = Object.entries(levels).slice(-200);
  write(SONG_KEY, JSON.stringify(Object.fromEntries(entries)));
}
