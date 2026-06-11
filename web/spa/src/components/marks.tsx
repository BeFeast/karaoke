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
