// KARAOKE v2 — Marquee · the Doorway: popup-as-receipt, options page, icons.

// ── Popup — the click already submitted; this is the receipt ───────────────
function MPopupBoard() {
  return (
    <div className="m-booth" style={{ height: "100%", display: "flex", flexDirection: "column", fontSize: 13 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "11px 14px", borderBottom: "1px solid var(--border)" }}>
        <MarqueeMark size={20} lit label="K" />
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 650, fontSize: 14 }}>Karaoke</span>
        <span style={{ flex: 1 }}></span>
        <span className="m-chip ok" style={{ fontSize: 10 }}><span className="m-dot"></span>oklabs.uk</span>
        <button className="m-btn sm ghost" type="button" title="Settings" style={{ padding: "2px 6px" }}>⚙</button>
      </div>

      {/* the receipt — submitted on click, already running */}
      <div style={{ padding: "12px 14px 0" }}>
        <div className="m-sign" style={{ borderColor: "var(--accent)", padding: "11px 13px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="m-chip run"><span className="m-dot"></span>on stage</span>
            <span style={{ fontWeight: 650, fontSize: 12.5, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>Bohemian Rhapsody (Official Video)</span>
          </div>
          <div style={{ marginTop: 8 }}>
            <MWipe text="fetch ✓ · gpu up · split · lyrics" pct={34} size={11} />
          </div>
          <div className="m-mono" style={{ display: "flex", justifyContent: "space-between", marginTop: 7, fontSize: 10, color: "var(--muted)" }}>
            <span>youtube session ✓ rode along — used once, never stored</span>
            <span>~4 min</span>
          </div>
        </div>
        <div className="m-mono" style={{ fontSize: 10, color: "var(--muted)", margin: "7px 2px 0" }}>
          the toolbar click already submitted this tab — this popup is just the receipt
        </div>
      </div>

      {/* tonight */}
      <div style={{ padding: "12px 14px 12px", display: "flex", flexDirection: "column", gap: 6, flex: 1 }}>
        <div className="m-mono" style={{ fontSize: 10, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)" }}>tonight</div>
        {[
          { t: "Shallow — Lady Gaga", state: "run", stage: "split · demucs", pct: 62 },
          { t: "Vampire — Olivia Rodrigo", state: "ready" },
          { t: "My Heart Will Go On", state: "failed" },
        ].map((j) => (
          <div key={j.t} style={{ display: "grid", gap: 4, padding: "8px 10px", borderRadius: 8, background: "var(--bg-card)", border: "1px solid var(--border-soft)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 12, fontWeight: 550, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{j.t}</span>
              {j.state === "ready" && <span className="m-mono" style={{ fontSize: 10.5, color: "var(--ok)" }}>ready ▸</span>}
              {j.state === "failed" && <span className="m-mono" style={{ fontSize: 10.5, color: "var(--err)" }}>failed ↻</span>}
              {j.state === "run" && <span className="m-mono" style={{ fontSize: 10.5, color: "var(--accent)" }}>{j.pct}%</span>}
            </div>
            {j.state === "run" && <MWipe text={j.stage} pct={j.pct} size={10.5} />}
          </div>
        ))}
        <div className="m-mono" style={{ marginTop: "auto", fontSize: 10.5, color: "var(--muted)", display: "flex", justifyContent: "space-between" }}>
          <span style={{ color: "var(--accent)" }}>open the booth →</span>
          <span>v0.4.0</span>
        </div>
      </div>
    </div>
  );
}

// ── Options page ───────────────────────────────────────────────────────────
function MOptionsBoard() {
  const lbl = { fontSize: 12.5, fontWeight: 600, marginBottom: 5 };
  const help = { fontSize: 11.5, color: "var(--muted)", lineHeight: 1.5, marginTop: 5 };
  const field = { width: "100%", padding: "8px 11px", border: "1px solid var(--border)", borderRadius: 7, background: "var(--bg)", color: "var(--fg)", fontSize: 13, fontFamily: "var(--font-mono)" };
  return (
    <div className="m-booth" style={{ height: "100%", padding: "28px 34px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <MarqueeMark size={26} lit={false} label="K" />
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 650, fontSize: 16 }}>Karaoke</span>
        <span className="m-chip" style={{ fontSize: 10 }}>extension settings</span>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--muted)", margin: "4px 0 20px" }}>The stage door — where this browser hands songs to your booth.</div>

      <div style={{ display: "grid", gap: 17, maxWidth: 460 }}>
        <div>
          <div style={lbl}>Booth address</div>
          <input readOnly style={field} value="https://karaoke.oklabs.uk"></input>
          <div style={help}>Where submits go. On the LAN, point straight at the runtime — <span className="m-mono">http://10.10.0.13:13140</span>. Saving asks Chrome for permission to reach that origin.</div>
        </div>
        <div>
          <div style={lbl}>Stage pass <span className="m-mono" style={{ fontWeight: 400, color: "var(--muted)" }}>ktx_…</span></div>
          <input readOnly type="password" style={field} value="ktx_7f2k49s1mz"></input>
          <div style={help}>Mint one in Booth → Settings → Stage passes. Only needed outside the trusted LAN; jobs stay scoped to you.</div>
        </div>
        <div style={{ border: "1px dashed var(--border)", borderRadius: 9, padding: "11px 13px", background: "var(--bg-card)" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5, fontWeight: 600 }}>
            <span style={{ color: "var(--ok)" }}>●</span> YouTube session — nothing to configure
          </div>
          <div style={help}>Some videos demand a signed-in viewer. On each submit, this browser's YouTube cookies ride along with <b>that one job</b> and are never stored — not by the extension, not by the server. Just stay signed in.</div>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button className="m-btn primary" type="button">Save</button>
          <span className="m-mono" style={{ fontSize: 11.5, color: "var(--ok)" }}>saved ✓</span>
        </div>
      </div>
    </div>
  );
}

// ── Icon sheet ─────────────────────────────────────────────────────────────
function MIconTile({ size, file }) {
  return (
    <div style={{ display: "grid", gap: 6, justifyItems: "center" }}>
      <MarqueeMark size={size} lit label="K" />
      <span className="m-mono" style={{ fontSize: 10, color: "var(--muted)" }}>{file}</span>
    </div>
  );
}

function MIconBoard() {
  return (
    <div className="m-booth" style={{ height: "100%", padding: 24, display: "flex", flexDirection: "column", gap: 18 }}>
      <div className="m-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)" }}>the sign survives 16px — frame + bulbs + letter</div>
      <div style={{ display: "flex", gap: 26, alignItems: "flex-end" }}>
        <MIconTile size={96} file="karaoke-128.png" />
        <MIconTile size={48} file="karaoke-48.png" />
        <MIconTile size={22} file="karaoke-16.png" />
      </div>

      <div style={{ display: "grid", gap: 10 }}>
        {[["light toolbar", "#f1f3f4", "#dadce0", "#202124"], ["dark toolbar", "#202124", "#3c4043", "#e8eaed"]].map(([name, bg, br, fg]) => (
          <div key={name} style={{ display: "flex", alignItems: "center", gap: 16, background: bg, border: `1px solid ${br}`, borderRadius: 10, padding: "9px 14px" }}>
            <span className="m-mono" style={{ fontSize: 10.5, color: fg, opacity: 0.6, width: 84 }}>{name}</span>
            {[["idle", null, null], ["working", "62", "#e8a93c"], ["ready", "✓", "#5f7a4a"], ["error", "!", "#a8442f"]].map(([state, badge, badgeBg]) => (
              <span key={state} style={{ position: "relative", display: "grid", justifyItems: "center", gap: 3 }}>
                <MarqueeMark size={18} lit label="K" />
                {badge && <span style={{ position: "absolute", top: -7, right: -10, background: badgeBg, color: "#fff", borderRadius: 7, font: "700 8px/1 monospace", padding: "2.5px 4px" }}>{badge}</span>}
                <span className="m-mono" style={{ fontSize: 9, color: fg, opacity: 0.55 }}>{state}</span>
              </span>
            ))}
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", borderTop: "1px dashed var(--border-soft)", paddingTop: 13 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 7, background: "#dee1e6", borderRadius: "10px 10px 0 0", padding: "6px 12px", fontSize: 11, color: "#202124" }}>
          <MarqueeMark size={13} lit label="K" />
          Bohemian Rhapsody · Karaoke
          <span style={{ color: "#5f6368" }}>✕</span>
        </div>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>favicon — same sign, no special case</span>
      </div>
    </div>
  );
}

Object.assign(window, { MPopupBoard, MOptionsBoard, MIconBoard });
