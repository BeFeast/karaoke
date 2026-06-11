// KARAOKE final — the stage door (sign-in, Clerk) + user avatar menu.

function SignInScreen({ onSignIn, vars = {} }) {
  return (
    <div className="m-booth" style={{ minHeight: "100%", display: "grid", placeItems: "center", padding: 24, ...vars }}>
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
          <span>karaoke v0.4.0</span>
          <span style={{ color: "var(--border)" }}>·</span>
          <a href="https://github.com/BeFeast/karaoke" target="_blank" rel="noopener" style={{ color: "var(--info)", textDecoration: "none" }}>github.com/BeFeast/karaoke ↗</a>
          <span style={{ color: "var(--border)" }}>·</span>
          <span>open source</span>
        </div>
      </div>
    </div>
  );
}

// Clerk-backed avatar + account menu for the booth header.
function UserAvatar({ name = "Oleg", email = "oleg@oklabs.uk", onSignOut, onSettings }) {
  const [open, setOpen] = React.useState(false);
  React.useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    window.addEventListener("pointerdown", close);
    return () => window.removeEventListener("pointerdown", close);
  }, [open]);
  return (
    <div style={{ position: "relative" }} onPointerDown={(e) => e.stopPropagation()}>
      <button type="button" onClick={() => setOpen(!open)} aria-label="Account" style={{
        appearance: "none", cursor: "pointer", width: 30, height: 30, borderRadius: "50%",
        border: "2px solid " + (open ? "var(--accent)" : "var(--border)"),
        background: "var(--accent-soft)", color: "var(--accent)",
        font: "700 12px/1 var(--font-ui)", display: "grid", placeItems: "center", padding: 0,
      }}>{name[0]}</button>
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
          {[["Settings", onSettings], ["Manage account", null], ["Sign out", onSignOut]].map(([label, fn]) => (
            <button key={label} type="button" onClick={() => { setOpen(false); fn && fn(); }} style={{
              appearance: "none", border: "none", cursor: "pointer", width: "100%", textAlign: "left",
              padding: "7px 10px", borderRadius: 6, background: "transparent",
              color: label === "Sign out" ? "var(--err)" : "var(--fg)", fontSize: 12.5, fontFamily: "var(--font-ui)",
            }}
            onMouseEnter={(e) => e.currentTarget.style.background = "var(--bg-soft)"}
            onMouseLeave={(e) => e.currentTarget.style.background = "transparent"}>{label}</button>
          ))}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { SignInScreen, UserAvatar });
