// Singing preferences from Settings (Sonereli UI/UX v1 step 10). Booleans in
// localStorage, both on by default. Lyrics size and the Original vocals start
// level live in their own modules (stageLyricsModel, vocals).

export type PrefKey = "keepAwake" | "countIn";

const KEYS: Record<PrefKey, string> = {
  keepAwake: "sonereli-keep-awake",
  countIn: "sonereli-count-in",
};

export function loadPref(key: PrefKey): boolean {
  try {
    return localStorage.getItem(KEYS[key]) !== "off";
  } catch {
    return true;
  }
}

export function savePref(key: PrefKey, on: boolean): void {
  try {
    localStorage.setItem(KEYS[key], on ? "on" : "off");
  } catch {
    /* storage unavailable */
  }
}
