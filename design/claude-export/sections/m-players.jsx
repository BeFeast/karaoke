// KARAOKE v2 — Marquee · three player UX explorations (the variation budget).
// All live on the dark stage; pick one or hybridize.

function bigBars2(seed, n) {
  const out = [];
  let x = seed;
  for (let i = 0; i < n; i++) {
    x = (x * 9301 + 49297) % 233280;
    const t = i / n;
    const env = 0.45 + 0.55 * Math.sin(Math.PI * Math.min(1, t * 1.15));
    out.push((0.2 + 0.8 * (x / 233280)) * env);
  }
  return out;
}

function MStageWave({ w = 560, h = 96, played = 0.38 }) {
  const n = 96;
  const vox = bigBars2(5, n);
  const inst = bigBars2(19, n);
  const bw = w / n;
  const half = h / 2;
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }} aria-label="Duet waveform">
      {vox.map((v, i) => (
        <rect key={"v" + i} x={i * bw + 0.8} y={half - 2 - v * (half - 6)} width={bw - 1.6} height={v * (half - 6)} rx="1"
          fill="var(--vox)" opacity={i / n <= played ? 0.95 : 0.26}></rect>
      ))}
      {inst.map((v, i) => (
        <rect key={"i" + i} x={i * bw + 0.8} y={half + 2} width={bw - 1.6} height={v * (half - 6)} rx="1"
          fill="var(--inst)" opacity={i / n <= played ? 0.85 : 0.2}></rect>
      ))}
      <line x1={w * played} x2={w * played} y1="0" y2={h} stroke="var(--fg)" strokeWidth="1.5"></line>
    </svg>
  );
}

function Transport({ compact }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <button className="m-btn primary" type="button" style={{ width: 38, height: 38, borderRadius: "50%", justifyContent: "center", fontSize: 14 }}>▶</button>
      <button className="m-btn sm" type="button">−5s</button>
      <button className="m-btn sm" type="button">+5s</button>
      <span className="m-mono" style={{ fontSize: 12, color: "var(--fg-soft)" }}>2:14 / 5:54</span>
      {!compact && <span style={{ flex: 1 }}></span>}
      {!compact && <button className="m-btn sm" type="button">⟲ A–B</button>}
      {!compact && <button className="m-btn sm" type="button">1.0×</button>}
    </div>
  );
}

function Caption({ children }) {
  return (
    <div className="m-mono" style={{ marginTop: "auto", borderTop: "1px dashed var(--border)", paddingTop: 10, fontSize: 10.5, lineHeight: 1.55, color: "var(--muted)" }}>
      {children}
    </div>
  );
}

// ── P1 · The Console — a mixing desk you already know ─────────────────────
function Fader({ label, color, level, scale = ["0", "−6", "−12", "−24", "∞"] }) {
  return (
    <div style={{ display: "grid", justifyItems: "center", gap: 7 }}>
      <span className="m-mono" style={{ fontSize: 10, letterSpacing: "0.1em", color }}>{label}</span>
      <div style={{ position: "relative", width: 34, height: 150, display: "flex", justifyContent: "center" }}>
        <div style={{ position: "absolute", left: 4, top: 4, bottom: 4, display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
          {scale.map((s) => <span key={s} className="m-mono" style={{ fontSize: 7, color: "var(--muted)" }}>{s}</span>)}
        </div>
        <div style={{ width: 4, borderRadius: 2, background: "var(--border)", position: "relative" }}>
          <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: level + "%", background: color, borderRadius: 2, opacity: 0.85 }}></div>
          <div style={{
            position: "absolute", left: "50%", bottom: level + "%", transform: "translate(-50%, 50%)",
            width: 26, height: 13, borderRadius: 3.5, background: "var(--fg)",
            border: "1px solid var(--bg)", boxShadow: "0 2px 5px rgba(0,0,0,0.5)",
          }}></div>
        </div>
      </div>
      <span className="m-mono" style={{ fontSize: 10, color: "var(--muted)" }}>{level}%</span>
    </div>
  );
}

function PlayerConsole() {
  return (
    <div className="m-stage" style={{ height: "100%", padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "14px 16px" }}>
        <MStageWave />
      </div>
      <div style={{ display: "flex", gap: 18, alignItems: "stretch" }}>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 12, justifyContent: "center" }}>
          <Transport />
          <div className="m-mono" style={{ fontSize: 11, color: "var(--muted)" }}>
            keyboard: space play · ←/→ seek · <b style={{ color: "var(--fg-soft)" }}>V</b> hold to duck vocals
          </div>
        </div>
        <div style={{ display: "flex", gap: 14, padding: "10px 16px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)" }}>
          <Fader label="VOX" color="var(--vox)" level={25} />
          <Fader label="INST" color="var(--inst)" level={90} />
          <div style={{ display: "grid", alignContent: "center", gap: 8 }}>
            <button className="m-btn sm" type="button" title="Hold to drop vocals while you sing" style={{ borderColor: "var(--vox)", color: "var(--vox)", fontWeight: 700 }}>DUCK</button>
            <button className="m-btn sm" type="button">A–B</button>
          </div>
        </div>
      </div>
      <Caption>
        Pro metaphor, zero learning curve for anyone who's seen a desk. Two independent faders
        instead of one blend — you can have quiet guide vocals over a full band. DUCK is the
        party trick: hold it (or "V") when it's your line.
      </Caption>
    </div>
  );
}

// ── P2 · The Dial — one decision: how much help do you need? ──────────────
function ConfidenceDial({ value = 25 }) {
  const r = 92, cx = 120, cy = 112;
  const a0 = Math.PI, a1 = 0;
  const ang = a0 + (a1 - a0) * (value / 100);
  const nx = cx + Math.cos(ang) * (r - 18);
  const ny = cy - Math.sin(ang) * (r - 18);
  const arc = (from, to, color, width, opacity) => {
    const x0 = cx + Math.cos(from) * r, y0 = cy - Math.sin(from) * r;
    const x1 = cx + Math.cos(to) * r, y1 = cy - Math.sin(to) * r;
    return <path d={`M ${x0} ${y0} A ${r} ${r} 0 0 1 ${x1} ${y1}`} fill="none" stroke={color} strokeWidth={width} strokeLinecap="round" opacity={opacity}></path>;
  };
  return (
    <svg width="240" height="132" viewBox="0 0 240 132">
      {arc(Math.PI, ang, "var(--inst)", 7, 0.9)}
      {arc(ang, 0, "var(--vox)", 7, 0.28)}
      {Array.from({ length: 11 }).map((_, i) => {
        const a = Math.PI - (Math.PI * i) / 10;
        const x0 = cx + Math.cos(a) * (r + 8), y0 = cy - Math.sin(a) * (r + 8);
        const x1 = cx + Math.cos(a) * (r + 13), y1 = cy - Math.sin(a) * (r + 13);
        return <line key={i} x1={x0} y1={y0} x2={x1} y2={y1} stroke="var(--muted)" strokeWidth="1.5" opacity="0.5"></line>;
      })}
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="var(--fg)" strokeWidth="3" strokeLinecap="round"></line>
      <circle cx={cx} cy={cy} r="7" fill="var(--fg)"></circle>
      <text x="18" y="128" fontFamily="var(--font-mono)" fontSize="9.5" fill="var(--inst)">KARAOKE</text>
      <text x="178" y="128" fontFamily="var(--font-mono)" fontSize="9.5" fill="var(--vox)">FULL VOICE</text>
    </svg>
  );
}

function PlayerDial() {
  return (
    <div className="m-stage" style={{ height: "100%", padding: 20, display: "flex", flexDirection: "column", gap: 12, alignItems: "center" }}>
      <div style={{ alignSelf: "stretch", textAlign: "center", marginTop: 4 }}>
        <MWipe text="Pulled my trigger, now he's dead" pct={64} size={20} family="var(--font-display)" weight={650} fill="var(--accent)" dim="#54493a" />
        <div className="m-mono" style={{ marginTop: 5, fontSize: 10.5, color: "var(--muted)" }}>next: Mama, life had just begun</div>
      </div>
      <ConfidenceDial value={25} />
      <div className="m-mono" style={{ fontSize: 11.5, color: "var(--fg-soft)", marginTop: -6 }}>guide vocals · 25%</div>
      <Transport compact />
      <Caption>
        The whole mixer collapses into one "confidence dial" — turn left when you know the song,
        right when you don't. Lyrics stay on top; transport shrinks to essentials. Most novel,
        most opinionated; scrub ring doubles as the seek bar in the prototype.
      </Caption>
    </div>
  );
}

// ── P3 · The Setlist — lyrics ARE the player ──────────────────────────────
function PlayerSetlist() {
  return (
    <div className="m-stage" style={{ height: "100%", padding: 20, display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", gap: 16, flex: 1, minHeight: 0 }}>
        <div className="m-sign" style={{ flex: 1, padding: "18px 22px", display: "flex", flexDirection: "column", justifyContent: "center", gap: 10, textAlign: "center" }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 14, fontWeight: 500, color: "#6e6354" }}>Put a gun against his head</div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 23, fontWeight: 700, lineHeight: 1.2 }}>
            <MWipe text="Pulled my trigger, now he's dead" pct={64} size={23} family="var(--font-display)" weight={700} fill="var(--accent)" dim="#54493a" />
          </div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 540, color: "#8d8170" }}>Mama, life had just begun</div>
          <div style={{ display: "flex", justifyContent: "center", marginTop: 2 }}>
            <MBulbs n={8} lit={3} />
          </div>
        </div>
        {/* spotlight dimmer = vocal blend */}
        <div style={{ display: "grid", justifyItems: "center", alignContent: "space-between", padding: "6px 0" }}>
          <span style={{ fontSize: 13 }} title="Full vocals">🔆</span>
          <div style={{ width: 4, flex: 1, margin: "8px 0", borderRadius: 2, background: "linear-gradient(180deg, var(--vox), var(--inst))", position: "relative" }}>
            <span style={{
              position: "absolute", top: "70%", left: "50%", transform: "translate(-50%,-50%)",
              width: 18, height: 18, borderRadius: "50%", background: "var(--fg)", border: "3px solid var(--bg)",
            }}></span>
          </div>
          <span style={{ fontSize: 13, opacity: 0.6 }} title="Karaoke only">◌</span>
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Transport />
        <span style={{ flex: 1 }}></span>
        <button className="m-btn sm primary" type="button" style={{ background: "transparent", color: "var(--accent)", borderColor: "var(--accent)" }}>⤢ Fullscreen</button>
      </div>
      <Caption>
        Lyrics are the instrument panel: the marquee sign holds prev/now/next, bulbs count down
        instrumental gaps, and the blend is a "spotlight dimmer" on the rail. Waveform demoted
        to a strip in fullscreen. Closest to what the room actually looks at while singing.
      </Caption>
    </div>
  );
}

Object.assign(window, { PlayerConsole, PlayerDial, PlayerSetlist, MStageWave, Transport });
