// KARAOKE final — Settings: stage passes (extension tokens), pipeline defaults, about.

const K_VERSION = "v0.4.0";
const K_REPO = "https://github.com/BeFeast/karaoke";

function SettingsSection({ title, children }) {
  return (
    <div style={{ marginTop: 26 }}>
      <div className="m-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)", marginBottom: 10 }}>{title}</div>
      {children}
    </div>
  );
}

function PassRow({ pass, onRevoke }) {
  return (
    <div style={{ display: "flex", gap: 14, alignItems: "baseline", padding: "12px 2px", borderTop: "1px solid var(--border-soft)" }}>
      <span className="m-mono" style={{ fontSize: 11.5, color: "var(--muted)", width: 22 }}>#{pass.n}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 13.5, fontWeight: 600 }}>{pass.name}</span>
          {pass.revoked && <span className="m-chip err" style={{ fontSize: 10 }}>revoked</span>}
        </div>
        <div className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 3, display: "flex", gap: 14, flexWrap: "wrap" }}>
          <span>created {pass.created}</span>
          <span>last used {pass.used}</span>
          <span>{pass.scope}</span>
        </div>
      </div>
      {!pass.revoked && (
        <button className="m-btn sm ghost" type="button" onClick={() => onRevoke(pass.n)} style={{ color: "var(--err)" }}>Revoke</button>
      )}
    </div>
  );
}

function Select({ value, options }) {
  return (
    <select defaultValue={value} style={{
      appearance: "none", padding: "7px 28px 7px 11px", border: "1px solid var(--border)", borderRadius: 7,
      background: "var(--bg-card)", color: "var(--fg)", fontSize: 12.5, fontFamily: "var(--font-mono)", cursor: "pointer",
      backgroundImage: "linear-gradient(45deg, transparent 50%, var(--muted) 50%), linear-gradient(135deg, var(--muted) 50%, transparent 50%)",
      backgroundPosition: "calc(100% - 14px) 55%, calc(100% - 9px) 55%", backgroundSize: "5px 5px", backgroundRepeat: "no-repeat",
    }}>
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  );
}

function SettingRow({ label, help, control }) {
  return (
    <div style={{ display: "flex", gap: 16, alignItems: "center", padding: "11px 2px", borderTop: "1px solid var(--border-soft)" }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{label}</div>
        {help && <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 2, lineHeight: 1.45 }}>{help}</div>}
      </div>
      {control}
    </div>
  );
}

function SettingsScreen({ onBack, onSignOut, vars = {} }) {
  const [passes, setPasses] = React.useState([
    { n: 3, name: "desk-session-10", created: "2 h ago", used: "1 h ago", scope: "karaoke:operator-extension" },
    { n: 2, name: "operator desktop Chrome extension", created: "Jun 1", used: "23 h ago", scope: "karaoke:operator-extension" },
    { n: 1, name: "prisma launchd cookie-sync (#10)", created: "Jun 1", used: "5 h ago", scope: "karaoke:cookie-rotation-cron", revoked: true },
  ]);
  const [mintName, setMintName] = React.useState("");
  const [fresh, setFresh] = React.useState(null);
  const mint = () => {
    if (!mintName.trim()) return;
    const n = Math.max(...passes.map((p) => p.n)) + 1;
    setPasses([{ n, name: mintName.trim(), created: "just now", used: "never", scope: "karaoke:operator-extension" }, ...passes]);
    setFresh("ktx_" + Math.random().toString(36).slice(2, 12));
    setMintName("");
  };
  const revoke = (n) => setPasses((ps) => ps.map((p) => p.n === n ? { ...p, revoked: true } : p));
  const active = passes.filter((p) => !p.revoked).length;

  return (
    <div className="m-booth" style={{ minHeight: "100%", display: "flex", flexDirection: "column", ...vars }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "0 24px", height: 56, borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
        <MicMark size={24} />
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 650, fontSize: 17, letterSpacing: "-0.01em" }}>Karaoke</span>
        <span style={{ flex: 1 }}></span>
        <span className="m-chip" style={{ textTransform: "uppercase", letterSpacing: "0.05em", fontSize: 10 }}>trusted lan</span>
        <UserAvatar onSignOut={onSignOut} />
      </div>

      <div style={{ flex: 1, padding: "24px 24px 20px", maxWidth: 720, width: "100%", margin: "0 auto", display: "flex", flexDirection: "column" }}>
        <button className="m-btn sm ghost" type="button" onClick={onBack} style={{ alignSelf: "flex-start", marginLeft: -9 }}>← back to the booth</button>
        <h1 style={{ margin: "10px 0 2px", fontFamily: "var(--font-display)", fontWeight: 650, fontSize: 26, letterSpacing: "-0.015em" }}>Settings</h1>
        <div className="m-mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>{active} active stage passes</div>

        <SettingsSection title="stage passes — extension tokens">
          <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)", padding: 14 }}>
            <div style={{ display: "flex", gap: 10 }}>
              <input value={mintName} onChange={(e) => setMintName(e.target.value)} onKeyDown={(e) => e.key === "Enter" && mint()}
                placeholder="Name this pass — e.g. Chrome on the desk machine"
                style={{ flex: 1, padding: "9px 12px", border: "1px solid var(--border)", borderRadius: 7, background: "var(--bg)", color: "var(--fg)", fontSize: 13, fontFamily: "var(--font-ui)", outline: "none" }}></input>
              <button className="m-btn primary" type="button" onClick={mint}>Mint pass</button>
            </div>
            {fresh && (
              <div className="m-mono" style={{ marginTop: 10, padding: "8px 11px", borderRadius: 7, background: "var(--accent-soft)", color: "var(--accent)", fontSize: 12, display: "flex", gap: 10, alignItems: "center" }}>
                <span style={{ fontWeight: 700 }}>{fresh}</span>
                <span style={{ color: "var(--fg-soft)" }}>— copy it now; it's shown exactly once</span>
                <button className="m-btn sm" type="button" style={{ marginLeft: "auto" }} onClick={() => setFresh(null)}>⧉ Copied</button>
              </div>
            )}
            <div className="m-mono" style={{ marginTop: 9, fontSize: 10.5, color: "var(--muted)", lineHeight: 1.5 }}>
              ktx_ passes let the Chrome extension submit jobs as you. Raw value appears once, right after minting.
            </div>
          </div>
          <div style={{ marginTop: 6 }}>
            {passes.map((p) => <PassRow key={p.n} pass={p} onRevoke={revoke}></PassRow>)}
          </div>
        </SettingsSection>

        <SettingsSection title="pipeline defaults">
          <SettingRow label="Cost cap per job" help="A job that would exceed this is cancelled and the instance destroyed."
            control={<Select value="$0.80" options={["$0.40", "$0.80", "$1.50", "no cap"]} />} />
          <SettingRow label="Daily spend cap" help="Hard ceiling across all jobs; submits queue until tomorrow once hit."
            control={<Select value="$6.00" options={["$3.00", "$6.00", "$12.00"]} />} />
          <SettingRow label="Separation model" help="htdemucs_ft is slower but noticeably cleaner on vocals."
            control={<Select value="htdemucs_ft" options={["htdemucs", "htdemucs_ft"]} />} />
          <SettingRow label="Lyrics model" help="large-v3 for accuracy; medium roughly halves GPU time."
            control={<Select value="large-v3" options={["medium", "large-v3"]} />} />
        </SettingsSection>

        <SettingsSection title="sharing">
          <SettingRow label="Share links" help="Anyone with the link can play and download — no account needed."
            control={<Select value="unlisted" options={["unlisted", "signed-in only"]} />} />
          <SettingRow label="Link lifetime" help="Old party links quietly expire."
            control={<Select value="30 days" options={["7 days", "30 days", "forever"]} />} />
        </SettingsSection>

        <div className="m-mono" style={{ marginTop: "auto", paddingTop: 26, display: "flex", gap: 16, fontSize: 11, color: "var(--muted)", borderTop: "1px dashed var(--border-soft)", alignItems: "center", flexWrap: "wrap" }}>
          <span>karaoke {K_VERSION}</span>
          <a href={K_REPO} target="_blank" rel="noopener" style={{ color: "var(--info)", textDecoration: "none" }}>github.com/BeFeast/karaoke ↗</a>
          <span>open source · MIT</span>
          <span style={{ marginLeft: "auto" }}>self-hosted · your music never leaves your boxes</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { SettingsScreen, K_VERSION, K_REPO });
