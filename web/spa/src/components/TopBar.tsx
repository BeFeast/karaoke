import type { ReactNode } from "react";
import type { Theme } from "../theme";

/** Microphone glyph — the Karaoke brand mark (parity with Scribe's brand-mark). */
export function MicMark() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
      <rect x="9" y="2" width="6" height="12" rx="3" fill="currentColor" />
      <path
        d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SunMoon({ theme }: { theme: Theme }) {
  if (theme === "dark") {
    return (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path
          d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
        />
      </svg>
    );
  }
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="4" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

export function TopBar({
  theme,
  onToggleTheme,
  identity,
}: {
  theme: Theme;
  onToggleTheme: () => void;
  identity?: ReactNode;
}) {
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">
          <MicMark />
        </span>
        karaoke
      </div>
      <div className="grow" />
      <button
        type="button"
        className="iconbtn"
        onClick={onToggleTheme}
        title={theme === "dark" ? "Switch to light" : "Switch to dark"}
        aria-label="Toggle theme"
      >
        <SunMoon theme={theme} />
      </button>
      {identity}
    </header>
  );
}
