// KARAOKE v2 — Marquee direction · brand primitives, naming, identity board.
// Exposes MWipe, MBulbs, MarqueeMark, MWordSign, MDuetWave on window.

function MWipe({ text, pct, size = 12, family = "var(--font-mono)", weight = 500, fill, dim }) {
  return (
    <span className="m-wipe" style={{ fontSize: size, fontFamily: family, fontWeight: weight }}>
      <span className="w-dim" style={dim ? { color: dim } : null}>{text}</span>
      <span className="w-fill" style={{ width: pct + "%", ...(fill ? { color: fill } : null) }} aria-hidden="true">{text}</span>
    </span>
  );
}

function MBulbs({ n = 10, lit = 0, size = 6, gap = 6 }) {
  return (
    <span className="m-bulbs" style={{ gap }}>
      {Array.from({ length: n }).map((_, i) => (
        <i key={i} className={i < lit ? "lit" : ""} style={{ width: size, height: size }}></i>
      ))}
    </span>
  );
}

// The mark: a marquee sign tile — rounded rect, bulb rim, bold K.
// `lit` = stage version (amber on black); unlit = booth version (ink on paper).
// The mic mark — the original glyph, painted in brand colors:
// capsule = marquee accent, stand = room ink. Theme-aware via tokens.
function MicMark({ size = 24, accent = "var(--vox-ui, #a8650f)", ink = "var(--fg, #24201a)" }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-label="Karaoke mark">
      <rect x="9" y="2" width="6" height="12" rx="3" fill={accent}></rect>
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8" stroke={ink} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"></path>
    </svg>
  );
}

// The marquee tile mark (kept for canvas boards)
function MarqueeMark({ size = 48, lit = true, label = "K" }) {
  // theme-aware: resolves from the surrounding room's tokens, falls back to brand amber/ink
  const bg = lit ? "#161210" : "var(--bg-card, #fbf9f4)";
  const frame = lit ? "var(--bulb, #ffb84d)" : "var(--fg, #24201a)";
  const letter = frame;
  const bulbOn = lit ? "var(--bulb, #ffb84d)" : "var(--vox-ui, #a8650f)";
  const bulbPos = [14, 24, 34];
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-label="Karaoke mark">
      <rect x="2" y="2" width="44" height="44" rx="11" fill={bg}></rect>
      <rect x="2" y="2" width="44" height="44" rx="11" fill="none" stroke={frame} strokeWidth="2.5"></rect>
      {bulbPos.map((x) => <circle key={"t" + x} cx={x} cy="8.5" r="1.8" fill={bulbOn}></circle>)}
      {bulbPos.map((x) => <circle key={"b" + x} cx={x} cy="39.5" r="1.8" fill={bulbOn}></circle>)}
      <text x="24" y="33" textAnchor="middle" fontFamily="Bungee, sans-serif" fontSize="20" fill={letter}>{label}</text>
    </svg>
  );
}

// A small marquee sign with a word in Bungee — used for naming candidates.
function MWordSign({ word, sub, lit = true, size = 21 }) {
  return (
    <div className="m-sign" style={{
      padding: "14px 18px 12px", textAlign: "center",
      background: lit ? "#161210" : "var(--bg-card)",
      borderColor: lit ? "#ffb84d" : "var(--fg)",
    }}>
      <div style={{
        fontFamily: "var(--font-sign)", fontSize: size, letterSpacing: "0.04em", lineHeight: 1,
        color: lit ? "#ffb84d" : "var(--fg)",
        textShadow: lit ? "0 0 16px rgba(255,184,77,0.55)" : "none",
      }}>{word}</div>
      {sub && <div className="m-mono" style={{ marginTop: 7, fontSize: 9.5, letterSpacing: "0.18em", color: lit ? "#897f6c" : "var(--muted)" }}>{sub}</div>}
      <div style={{ marginTop: 9, display: "flex", justifyContent: "center" }}>
        <MBulbs n={9} lit={lit ? 9 : 0} size={4} gap={7} />
      </div>
    </div>
  );
}

// duet waveform rows (vox up / inst down) — kept from v1, marquee-toned
function mBars(seed, n) {
  const out = [];
  let x = seed;
  for (let i = 0; i < n; i++) {
    x = (x * 9301 + 49297) % 233280;
    out.push(0.25 + 0.75 * (x / 233280));
  }
  return out;
}
function MDuetWave({ seed = 7, w = 132, h = 26, played = 1 }) {
  const n = Math.round(w / 6);
  const vox = mBars(seed, n);
  const inst = mBars(seed + 13, n);
  const bw = w / n;
  return (
    <svg width={w} height={h} aria-hidden="true">
      {vox.map((v, i) => (
        <rect key={"v" + i} x={i * bw + 1} y={h / 2 - v * (h / 2 - 1)} width={bw - 2} height={v * (h / 2 - 1)} rx="1"
          fill="var(--vox)" opacity={i / n <= played ? 0.95 : 0.3}></rect>
      ))}
      {inst.map((v, i) => (
        <rect key={"i" + i} x={i * bw + 1} y={h / 2 + 1} width={bw - 2} height={v * (h / 2 - 1)} rx="1"
          fill="var(--inst)" opacity={i / n <= played ? 0.8 : 0.24}></rect>
      ))}
    </svg>
  );
}

Object.assign(window, { MWipe, MBulbs, MicMark, MarqueeMark, MWordSign, MDuetWave });

// ── Artboard · what changed after the interview ───────────────────────────
function PivotBoard() {
  const h2 = { font: "600 11px/1 var(--font-mono, monospace)", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)", margin: "18px 0 7px" };
  const li = { margin: "0 0 6px", fontSize: 13, lineHeight: 1.5, color: "var(--fg-soft)" };
  return (
    <div className="m-booth" style={{ height: "100%", padding: "26px 28px" }}>
      <div style={{ fontFamily: "var(--font-display)", fontSize: 19, fontWeight: 650, letterSpacing: "-0.01em" }}>Round 2 — your answers, my moves</div>
      <div style={h2}>locked by you</div>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        <li style={li}><b>Marquee</b> is the metaphor — refined: the booth is the sign by day (lights off, paper + ink + one amber), the stage is the sign at night.</li>
        <li style={li}><b>Light-first booth</b>, dark stage. Operator calm where you work, showtime where you sing.</li>
        <li style={li}><b>Operator porn</b>: the running job exposes the vast instance, live cost ticker, cap bar, and a teardown receipt when done.</li>
        <li style={li}><b>Variation budget → player UX.</b> Three genuinely different players below; mix and match.</li>
        <li style={li}><b>iPhone is the main player</b> → performance mode designed phone-first, laptop second.</li>
        <li style={li}><b>Popup-as-receipt</b>: the toolbar click still submits instantly; the popup that opens IS the confirmation, with tonight's mini feed.</li>
        <li style={li}><b>Webfonts allowed</b>: Bungee for signage moments, Bricolage Grotesque for lyrics & display, system for UI.</li>
      </ul>
      <div style={h2}>still yours to pick</div>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        <li style={li}>A name (candidates →). My vote: <b>Karaoke</b>.</li>
        <li style={li}>One of the three players (or a hybrid) to carry into the prototype.</li>
      </ul>
    </div>
  );
}

// ── Artboard · naming candidates ──────────────────────────────────────────
function NamingBoard() {
  const names = [
    ["ENCORE", "ONE MORE SONG", false,
      "What the crowd yells when it went well. Party-native, verbs well (“encore it”), zero explanation needed at a family gathering.", "karaoke.oklabs.uk", "my vote"],
    ["BACKTRACK", "SING OVER IT", false,
      "The backing track you sing over — with a wink at job tracking. Most descriptive of what the pipeline actually makes.", "backtrack.oklabs.uk", null],
    ["DUET", "YOU + THE MACHINE", false,
      "The blend slider is the whole story: the original voice on one side, yours on the other. Shortest, sweetest, slightly abstract.", "duet.oklabs.uk", null],
    ["KARAOKE", "SAYS WHAT IT IS", true,
      "The honest descriptor — and the one word that already belongs on a marquee. Keep if naming feels like overhead.", "karaoke.oklabs.uk", "chosen ✓"],
  ];
  return (
    <div className="m-booth" style={{ height: "100%", padding: 26, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, alignContent: "start" }}>
      {names.map(([word, sub, lit, why, domain, badge]) => (
        <div key={word} style={{ display: "grid", gap: 9, alignContent: "start" }}>
          <div style={{ position: "relative" }}>
            <MWordSign word={word} sub={sub} lit={lit} size={word.length > 7 ? 17 : 21} />
            {badge && <span className="m-chip run" style={{ position: "absolute", top: -9, right: -6 }}>{badge}</span>}
          </div>
          <div style={{ fontSize: 12, lineHeight: 1.5, color: "var(--fg-soft)" }}>{why}</div>
          <div className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>{domain}</div>
        </div>
      ))}
    </div>
  );
}

// ── Artboard · identity system ────────────────────────────────────────────
function IdentityBoard() {
  const label = { font: "600 10px/1 var(--font-mono, monospace)", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)", marginBottom: 8 };
  return (
    <div className="m-booth" style={{ height: "100%", padding: 26, display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <MarqueeMark size={44} lit={true} label="K" />
        <MarqueeMark size={44} lit={false} label="K" />
        <div style={{ fontSize: 12, color: "var(--fg-soft)", lineHeight: 1.45 }}>
          One sign, two states: <b>lit</b> on stage &amp; icons, <b>unlit</b> in the booth.
          Swap the letter when the name lands.
        </div>
      </div>

      <div>
        <div style={label}>palette · day / night of the same sign</div>
        <div style={{ display: "flex", gap: 8 }}>
          {[["paper", "#f5f2ea", "#24201a"], ["ink", "#24201a", "#f5f2ea"], ["amber", "#e8a93c", "#1c1207"], ["rose", "#b3503f", "#fbf9f4"], ["night", "#161210", "#ffb84d"]].map(([n, bg, fg]) => (
            <div key={n} style={{ flex: 1, borderRadius: 8, padding: "9px 10px", background: bg, color: fg, border: "1px solid var(--border-soft)" }}>
              <div className="m-mono" style={{ fontSize: 10.5 }}>{n}</div>
              <div className="m-mono" style={{ fontSize: 9, opacity: 0.65 }}>{bg}</div>
            </div>
          ))}
        </div>
        <div className="m-mono" style={{ marginTop: 7, fontSize: 10.5, color: "var(--muted)", display: "flex", gap: 16 }}>
          <span className="m-stem vox">vocals · #E8A93C</span>
          <span className="m-stem inst">instrumental · #7FA3C4</span>
          <span>— stems keep the functional duet pair</span>
        </div>
      </div>

      <div>
        <div style={label}>type — three voices</div>
        <div style={{ display: "grid", gap: 6 }}>
          <div style={{ fontFamily: "var(--font-sign)", fontSize: 19, color: "var(--accent)" }}>BUNGEE — SIGNAGE ONLY</div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 21, fontWeight: 640, letterSpacing: "-0.015em" }}>Bricolage Grotesque — lyrics &amp; display</div>
          <div style={{ fontSize: 13, color: "var(--fg-soft)" }}>System sans for UI · <span className="m-mono" style={{ fontSize: 12 }}>mono for ids, timings, money</span></div>
        </div>
      </div>

      <div>
        <div style={label}>progress — bulbs & wipes</div>
        <div style={{ display: "grid", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <MBulbs n={14} lit={9} />
            <span className="m-mono" style={{ fontSize: 11, color: "var(--muted)" }}>bulbs: stages, countdowns</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <MWipe text="splitting stems · demucs" pct={64} size={12} />
            <span className="m-mono" style={{ fontSize: 11, color: "var(--muted)" }}>wipes: anything with a %</span>
          </div>
        </div>
      </div>

      <div style={{ marginTop: "auto", borderTop: "1px dashed var(--border-soft)", paddingTop: 12, fontSize: 12, color: "var(--fg-soft)", lineHeight: 1.5 }}>
        <b>Restraint rule:</b> by day the marquee exists only as the mark, bulbs and one amber accent.
        Glow, Bungee headlines and lit signs are reserved for the stage and the icons.
      </div>
    </div>
  );
}

Object.assign(window, { PivotBoard, NamingBoard, IdentityBoard });
