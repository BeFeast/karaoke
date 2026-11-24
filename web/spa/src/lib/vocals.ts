// Original vocals start level (Sonereli UI/UX v1, decision 2 as revised by
// Oleg 2026-11-24): the first song on a device starts at 50%; after that every
// song starts at the level the user last chose on this device, on the Stage
// slider or in Settings. The mic mute toggle never changes the remembered
// level. Levels are whole percents, 0–100. localStorage only, so a browser
// without storage simply starts every song at 50%.

export const DEFAULT_VOCAL_PCT = 50;
const START_KEY = "sonereli-vocals-default";
/** v0.52–v0.53 kept a per-song map; it is superseded by the device level. */
const LEGACY_SONG_KEY = "sonereli-vocals-by-song";

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

function remove(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* storage unavailable */
  }
}

/** The level a song starts at: the last one chosen on this device, else 50%. */
export function startVocalPct(): number {
  const raw = read(START_KEY);
  const value = raw === null || raw.trim() === "" ? NaN : Number(raw);
  return Number.isFinite(value) ? clampPct(value) : DEFAULT_VOCAL_PCT;
}

/** Remember a level the user chose. Never call it for the mute toggle. */
export function rememberVocalPct(pct: number): void {
  write(START_KEY, String(clampPct(pct)));
  remove(LEGACY_SONG_KEY);
}
