import { useCallback, useState } from "react";

// Room-scoped theming (#154): booth rooms are ALWAYS light — only the stage
// room (#/job/:token) flips day/night via the ◐ toggle. The flip swaps the
// room container's token class (.m-booth ↔ .m-stage, final-app.jsx:45-54 with
// the FINAL green bake already in styles.css), so nothing touches <html> and
// the rest of the app never re-themes.

export type StageTheme = "day" | "night";

const KEY = "karaoke-stage-theme";

function initialTheme(): StageTheme {
  try {
    if (localStorage.getItem(KEY) === "night") return "night";
  } catch {
    /* localStorage unavailable — fall through to the default */
  }
  // FINAL bake: day-default stage (final-app.jsx "kfinal-theme" || "day").
  return "day";
}

/** Stage-room day/night state, persisted; day default. */
export function useStageTheme(): [StageTheme, () => void] {
  const [theme, setTheme] = useState<StageTheme>(initialTheme);

  const toggle = useCallback(() => {
    setTheme((t) => {
      const next: StageTheme = t === "day" ? "night" : "day";
      try {
        localStorage.setItem(KEY, next);
      } catch {
        /* ignore persistence failure */
      }
      return next;
    });
  }, []);

  return [theme, toggle];
}
