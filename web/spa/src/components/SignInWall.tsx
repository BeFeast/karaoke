// KARAOKE — the stage door (sign-in wall, S1) + the booth avatar menu.
// Literal port of design/claude-export/proto/signin.jsx (#153): SignInScreen
// :3-44, UserAvatar :47-86. Adaptations wire real data: the footer version
// comes from GET /health, the CTA triggers the real Clerk sign-in flow via
// the onSignIn prop (only ClerkShell renders this — LanShell never does, so
// the trusted-LAN path carries zero Clerk code), and UserAvatar takes real
// Clerk user data via props (the export's fake name/email defaults dropped,
// "Manage account" wired).

import { useEffect, useState } from "react";
import { getHealth } from "../api";
import { useCoarsePointer } from "../lib/layout";
import { MBulbs, MicMark } from "./marks";

export function SignInScreen({ onSignIn }: { onSignIn: () => void }) {
  const [version, setVersion] = useState<string | null>(null);
  useEffect(() => {
    let on = true;
    getHealth()
      .then((h) => {
        if (on) setVersion(h.version);
      })
      .catch(() => {
        // /health unreachable — the footer simply omits the version.
      });
    return () => {
      on = false;
    };
  }, []);
  return (
    <div className="m-booth" style={{ minHeight: "100%", display: "grid", placeItems: "center", padding: 24 }}>
      <div style={{ display: "grid", justifyItems: "center", gap: 0, textAlign: "center", maxWidth: 360 }}>
        {/* the sign above the door */}
        <div style={{
          width: 76, height: 76, borderRadius: 20, display: "grid", placeItems: "center",
          background: "var(--bg-card)", border: "2px solid var(--accent)",
          boxShadow: "var(--shadow)",
        }}>
          <MicMark size={38} accent="var(--accent)" ink="var(--fg)" />
        </div>
        <div style={{ marginTop: 8 }}>
          <MBulbs n={7} lit={7} size={4} gap={7} />
        </div>

        <h1 style={{ margin: "18px 0 0", fontFamily: "var(--font-display)", fontWeight: 680, fontSize: 32, letterSpacing: "-0.02em", lineHeight: 1.05 }}>Karaoke</h1>
        <div style={{ marginTop: 8, fontSize: 13.5, color: "var(--fg-soft)", lineHeight: 1.5 }}>
          One link in — vocals, karaoke track<br></br>and synced lyrics out.
        </div>

        <button className="m-btn primary" type="button" onClick={onSignIn}
          style={{ marginTop: 22, fontSize: 14, padding: "10px 26px", justifyContent: "center" }}>
          Sign in to the booth
        </button>

        <div className="m-mono" style={{ marginTop: 26, display: "flex", gap: 14, fontSize: 10.5, color: "var(--muted)", alignItems: "center" }}>
          <span>secured by clerk</span>
          <span style={{ color: "var(--border)" }}>·</span>
          <span>trusted lan skips this door</span>
        </div>
        <div className="m-mono" style={{ marginTop: 10, display: "flex", gap: 14, fontSize: 10.5, color: "var(--muted)", alignItems: "center" }}>
          <span>karaoke{version ? ` v${version}` : ""}</span>
          <span style={{ color: "var(--border)" }}>·</span>
          <a href="https://github.com/BeFeast/karaoke" target="_blank" rel="noopener" style={{ color: "var(--info)", textDecoration: "none" }}>github.com/BeFeast/karaoke ↗</a>
          <span style={{ color: "var(--border)" }}>·</span>
          <span>open source</span>
        </div>
      </div>
    </div>
  );
}

// Clerk-backed avatar + account menu for the booth header. Pure presentation:
// the caller (ClerkShell in main.tsx) supplies the real user identity and the
// real Clerk actions — this file imports nothing from Clerk.
export function UserAvatar({
  name,
  email,
  onSignOut,
  onSettings,
  onManage,
}: {
  name: string;
  email: string;
  onSignOut: () => void;
  onSettings: () => void;
  onManage: () => void;
}) {
  const [open, setOpen] = useState(false);
  // Outside-tap dismiss: document-level pointerdown (fires for touch too) +
  // the stopPropagation wrapper below — taps inside the menu never reach it.
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);
  const coarse = useCoarsePointer();
  const entries: [string, () => void][] = [
    ["Settings", onSettings],
    ["Manage account", onManage],
    ["Sign out", onSignOut],
  ];
  return (
    <div style={{ position: "relative" }} onPointerDown={(e) => e.stopPropagation()}>
      {/* ≥44px coarse-pointer hit target (#187): the button is a transparent
          hit area (30 + 2×7 = 44); the negative margin keeps the 30px layout
          footprint so the topbar and the visual circle (inner span) are
          unchanged. Fine pointers keep the exact pre-#187 30×30 geometry. */}
      <button type="button" onClick={() => setOpen(!open)} aria-label="Account" style={{
        appearance: "none", cursor: "pointer", border: "none", background: "transparent",
        padding: coarse ? 7 : 0, margin: coarse ? -7 : 0, display: "grid", placeItems: "center",
      }}>
        <span style={{
          width: 30, height: 30, borderRadius: "50%",
          border: "2px solid " + (open ? "var(--accent)" : "var(--border)"),
          background: "var(--accent-soft)", color: "var(--accent)",
          font: "700 12px/1 var(--font-ui)", display: "grid", placeItems: "center",
        }}>{(name || email || "?")[0]}</span>
      </button>
      {open && (
        <div style={{
          position: "absolute", right: 0, top: 38, zIndex: 20, minWidth: 198,
          background: "var(--bg-card)", border: "1px solid var(--border)",
          borderRadius: 10, boxShadow: "var(--shadow)", padding: 6,
        }}>
          <div style={{ padding: "8px 10px", borderBottom: "1px solid var(--border-soft)", marginBottom: 4 }}>
            <div style={{ fontSize: 12.5, fontWeight: 650 }}>{name}</div>
            <div className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 1 }}>{email}</div>
          </div>
          {/* Hover lives on .signin-item (styles.css) behind @media (hover:
              hover) — the old inline onMouseEnter/onMouseLeave mutation left
              the highlight stuck after a tap on iOS (#187). */}
          {entries.map(([label, fn]) => (
            <button key={label} type="button" className="signin-item" onClick={() => { setOpen(false); fn(); }} style={{
              appearance: "none", border: "none", cursor: "pointer", width: "100%", textAlign: "left",
              padding: "7px 10px", borderRadius: 6,
              color: label === "Sign out" ? "var(--err)" : "var(--fg)", fontSize: 12.5, fontFamily: "var(--font-ui)",
            }}>{label}</button>
          ))}
        </div>
      )}
    </div>
  );
}
