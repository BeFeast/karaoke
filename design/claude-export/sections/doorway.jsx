// KARAOKE redesign — Section 4: the Doorway (Chrome extension) + brand assets.
// Popup, options, icon sheet, notifications & copy voice.

// ── Popup ──────────────────────────────────────────────────────────────────
function PopupBoard() {
  return (
    <div className="k-stage" style={{ height: "100%", display: "flex", flexDirection: "column", fontSize: 13 }}>
      {/* header */}
      <div style={{ display: "flex", alignItems: "center", gap: 9, padding: "12px 14px", borderBottom: "1px solid var(--border)" }}>
        <KMark size={18} />
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 640, fontSize: 14.5 }}>karaoke</span>
        <span style={{ flex: 1 }}></span>
        <span className="k-chip ok" style={{ fontSize: 10 }}><span className="k-dot"></span>oklabs.uk</span>
        <button className="k-btn sm ghost" type="button" title="Settings" style={{ padding: "2px 6px" }}>⚙</button>
      </div>

      {/* current tab */}
      <div style={{ padding: "14px 14px 0" }}>
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: 10, padding: 12 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center", minWidth: 0 }}>
            <span style={{ width: 34, height: 34, borderRadius: 6, background: "#2a1215", display: "grid", placeItems: "center", color: "#e57373", fontSize: 15, flexShrink: 0 }}>▶</span>
            <div style={{ minWidth: 0 }}>
              <div style={{ fontWeight: 600, fontSize: 13, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>Bohemian Rhapsody (Official Video)</div>
              <div className="k-mono" style={{ fontSize: 10.5, color: "var(--muted)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>youtube.com · 5:54 · session ✓</div>
            </div>
          </div>
          <button className="k-btn primary" type="button" style={{ width: "100%", justifyContent: "center", marginTop: 11, padding: "9px 0", fontSize: 13.5 }}>
            <KMark size={16} mono="currentColor" wave={false} weight={4.5} /> Split this tab
          </button>
        </div>
        <div className="k-mono" style={{ fontSize: 10, color: "var(--muted)", margin: "8px 2px 0" }}>
          cookies ride along once, per submit — never stored
        </div>
      </div>

      {/* recent */}
      <div style={{ padding: "14px 14px 12px", display: "flex", flexDirection: "column", gap: 6, flex: 1 }}>
        <div className="k-mono" style={{ fontSize: 10, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)" }}>tonight</div>
        {[
          { t: "Shallow — Lady Gaga", state: "wipe", stage: "split · demucs", pct: 62 },
          { t: "Vampire — Olivia Rodrigo", state: "ready" },
          { t: "My Heart Will Go On", state: "failed" },
        ].map((j) => (
          <div key={j.t} style={{ display: "grid", gap: 4, padding: "8px 10px", borderRadius: 8, background: "var(--bg-card)", border: "1px solid var(--border-soft)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{ fontSize: 12, fontWeight: 550, flex: 1, whiteSpaceCollapse: "collapse", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{j.t}</span>
              {j.state === "ready" && <span className="k-mono" style={{ fontSize: 10.5, color: "var(--ok)" }}>ready ▸</span>}
              {j.state === "failed" && <span className="k-mono" style={{ fontSize: 10.5, color: "var(--err)" }}>failed ↻</span>}
              {j.state === "wipe" && <span className="k-mono" style={{ fontSize: 10.5, color: "var(--accent)" }}>{j.pct}%</span>}
            </div>
            {j.state === "wipe" && <Wipe text={j.stage} pct={j.pct} size={10.5} />}
          </div>
        ))}
        <div className="k-mono" style={{ marginTop: "auto", fontSize: 10.5, color: "var(--muted)", display: "flex", justifyContent: "space-between" }}>
          <span>open the booth →</span>
          <span>v0.4.0</span>
        </div>
      </div>
    </div>
  );
}

// ── Options page ───────────────────────────────────────────────────────────
function OptionsBoard() {
  const lbl = { fontSize: 12.5, fontWeight: 600, marginBottom: 5 };
  const help = { fontSize: 11.5, color: "var(--muted)", lineHeight: 1.5, marginTop: 5 };
  const field = { width: "100%", padding: "8px 11px", border: "1px solid var(--border)", borderRadius: 6, background: "var(--bg)", color: "var(--fg)", fontSize: 13, fontFamily: "var(--font-mono)" };
  return (
    <div className="k-booth" style={{ height: "100%", padding: "30px 36px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
        <KWordmark size={17} color="var(--fg)" />
        <span className="k-chip" style={{ fontSize: 10 }}>extension settings</span>
      </div>
      <div style={{ fontSize: 12.5, color: "var(--muted)", marginBottom: 22 }}>The doorway between this browser and your booth.</div>

      <div style={{ display: "grid", gap: 18, maxWidth: 460 }}>
        <div>
          <div style={lbl}>Booth address</div>
          <input readOnly style={field} value="https://karaoke.oklabs.uk"></input>
          <div style={help}>Where submits go. On the LAN you can point straight at the runtime — <span className="k-mono">http://10.10.0.13:13140</span>. Saving asks Chrome for permission to reach that origin.</div>
        </div>
        <div>
          <div style={lbl}>Extension token <span className="k-mono" style={{ fontWeight: 400, color: "var(--muted)" }}>ktx_…</span></div>
          <input readOnly type="password" style={field} value="ktx_7f2k49s1mz"></input>
          <div style={help}>Mint one in Booth → Settings → Tokens. Only needed outside the trusted LAN.</div>
        </div>
        <div style={{ border: "1px dashed var(--border)", borderRadius: 8, padding: "12px 14px", background: "var(--bg-card)" }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5, fontWeight: 600 }}>
            <span style={{ color: "var(--ok)" }}>●</span> YouTube session — nothing to configure
          </div>
          <div style={help}>Some videos demand a signed-in viewer. On each submit, this browser's YouTube cookies ride along with <b>that one job</b> and are never stored — not by the extension, not by the server. Just stay signed in.</div>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button className="k-btn primary" type="button">Save</button>
          <span className="k-mono" style={{ fontSize: 11.5, color: "var(--ok)" }}>saved ✓</span>
        </div>
      </div>
    </div>
  );
}

// ── Icon sheet ─────────────────────────────────────────────────────────────
function IconTile({ size, px, simplified }) {
  return (
    <div style={{ display: "grid", gap: 6, justifyItems: "center" }}>
      <span style={{ width: size, height: size, borderRadius: size * 0.22, background: "#131216", display: "grid", placeItems: "center", border: "1px solid #2c2a31" }}>
        <KMark size={size * 0.72} wave={!simplified} weight={simplified ? 6.5 : 4.2} />
      </span>
      <span className="k-mono" style={{ fontSize: 10, color: "var(--muted)" }}>{px}</span>
    </div>
  );
}

function IconSheetBoard() {
  return (
    <div className="k-booth" style={{ height: "100%", padding: 26, display: "flex", flexDirection: "column", gap: 20 }}>
      <div className="k-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)" }}>extension + favicon · the fork survives 16px</div>
      <div style={{ display: "flex", gap: 26, alignItems: "flex-end" }}>
        <IconTile size={96} px="karaoke-128.png" />
        <IconTile size={48} px="karaoke-48.png" />
        <IconTile size={22} px="karaoke-16.png" simplified />
      </div>

      {/* toolbar mocks with badge states */}
      <div style={{ display: "grid", gap: 10 }}>
        {[["light toolbar", "#f1f3f4", "#dadce0", "#202124"], ["dark toolbar", "#202124", "#3c4043", "#e8eaed"]].map(([name, bg, br, fg]) => (
          <div key={name} style={{ display: "flex", alignItems: "center", gap: 14, background: bg, border: `1px solid ${br}`, borderRadius: 10, padding: "9px 14px" }}>
            <span className="k-mono" style={{ fontSize: 10.5, color: fg, opacity: 0.6, width: 86 }}>{name}</span>
            {[
              ["idle", null, null],
              ["working", "62", "#e8a93c"],
              ["ready", "✓", "#5f7a4a"],
              ["error", "!", "#a14b38"],
            ].map(([state, badge, badgeBg]) => (
              <span key={state} style={{ position: "relative", display: "grid", justifyItems: "center", gap: 3 }}>
                <KMark size={17} mono={fg} wave={false} weight={5} />
                {badge && <span style={{ position: "absolute", top: -7, right: -10, background: badgeBg, color: "#fff", borderRadius: 7, font: "700 8px/1 monospace", padding: "2.5px 4px" }}>{badge}</span>}
                <span className="k-mono" style={{ fontSize: 9, color: fg, opacity: 0.5 }}>{state}</span>
              </span>
            ))}
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: 10, alignItems: "center", borderTop: "1px dashed var(--border-soft)", paddingTop: 14 }}>
        <span style={{ width: 0 }}></span>
        <div style={{ display: "flex", alignItems: "center", gap: 7, background: "#dee1e6", borderRadius: "10px 10px 0 0", padding: "6px 12px", fontSize: 11, color: "#202124" }}>
          <KMark size={13} mono="#131216" wave={false} weight={6} />
          Bohemian Rhapsody · karaoke
          <span style={{ color: "#5f6368" }}>✕</span>
        </div>
        <span className="k-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>favicon — mono fork, no rounded square</span>
      </div>
    </div>
  );
}

// ── Notifications + copy voice ─────────────────────────────────────────────
function NotifRow({ icon, title, body, action }) {
  return (
    <div style={{ display: "flex", gap: 11, background: "#fff", border: "1px solid #dadce0", borderRadius: 10, padding: "11px 13px", color: "#202124", boxShadow: "0 1px 3px rgba(0,0,0,0.12)" }}>
      <span style={{ width: 30, height: 30, borderRadius: 7, background: "#131216", display: "grid", placeItems: "center", flexShrink: 0 }}>
        <KMark size={18} weight={4.5} />
      </span>
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600 }}>{title}</div>
        <div style={{ fontSize: 11.5, color: "#5f6368", lineHeight: 1.45 }}>{body}</div>
        {action && <div style={{ fontSize: 11.5, color: "#0b57d0", fontWeight: 600, marginTop: 3 }}>{action}</div>}
      </div>
    </div>
  );
}

function VoiceBoard() {
  return (
    <div className="k-booth" style={{ height: "100%", padding: 26, display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="k-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)" }}>notifications · copy voice</div>
      <NotifRow title="On stage in ~4 min" body="Bohemian Rhapsody — fetching, then splitting on a GPU we'll throw away after." />
      <NotifRow title="Ready to sing" body="Bohemian Rhapsody — vocals, karaoke track and synced lyrics." action="Open the stage →" />
      <NotifRow title="Couldn't fetch this one" body="YouTube wants a signed-in viewer and this browser has no session. Sign in to YouTube, then resubmit." action="See details" />
      <div style={{ borderTop: "1px dashed var(--border-soft)", paddingTop: 12, display: "grid", gap: 6, fontSize: 12.5, lineHeight: 1.5 }}>
        <div><b>Voice rules.</b> Stagecraft nouns, operator honesty:</div>
        <div style={{ color: "var(--fg-soft)" }}>· "Ready to sing", never "Job completed successfully"</div>
        <div style={{ color: "var(--fg-soft)" }}>· Always say what happens next and what it costs — "~4 min · ~$0.30"</div>
        <div style={{ color: "var(--fg-soft)" }}>· Errors name the actor and the fix, not the stack trace</div>
        <div style={{ color: "var(--fg-soft)" }}>· The infra brag is allowed exactly once per surface ("a GPU we'll throw away after")</div>
      </div>
    </div>
  );
}

Object.assign(window, { PopupBoard, OptionsBoard, IconSheetBoard, VoiceBoard });
