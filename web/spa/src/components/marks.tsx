// KARAOKE v2 — Marquee direction · brand primitives.
// Literal port of design/claude-export/sections/m-brand.jsx:4-103 (#153).
// Geometry (viewBox/coords/strokes) is verbatim; the adaptations are TypeScript
// props, ES exports instead of window globals, and colors routed through
// tokens — hex fallbacks inside var() stripped, lit-mode literals replaced
// with var(--bg)/var(--bulb)/var(--glow)-derived values (design guard 2).

export function MWipe({
  text,
  pct,
  size = 12,
  family = "var(--font-mono)",
  weight = 500,
  fill,
  dim,
  style,
}: {
  text: string;
  pct: number;
  size?: number;
  family?: string;
  weight?: number;
  fill?: string;
  dim?: string;
  /**
   * Extra styles on the .m-wipe root. The #176 overflow cap (maxWidth +
   * ellipsis) must live on the element that owns the nowrap text or the
   * clipped line gets no ellipsis (#185 phone setlist); render-identical
   * when omitted.
   */
  style?: React.CSSProperties;
}) {
  return (
    <span className="m-wipe" style={{ fontSize: size, fontFamily: family, fontWeight: weight, ...style }}>
      <span className="w-dim" style={dim ? { color: dim } : undefined}>{text}</span>
      <span className="w-fill" style={{ width: pct + "%", ...(fill ? { color: fill } : null) }} aria-hidden="true">{text}</span>
    </span>
  );
}

export function MBulbs({
  n = 10,
  lit = 0,
  size = 6,
  gap = 6,
}: {
  n?: number;
  lit?: number;
  size?: number;
  gap?: number;
}) {
  return (
    <span className="m-bulbs" style={{ gap }}>
      {Array.from({ length: n }).map((_, i) => (
        <i key={i} className={i < lit ? "lit" : ""} style={{ width: size, height: size }}></i>
      ))}
    </span>
  );
}

// The mic mark — the original glyph, painted in brand colors:
// capsule = marquee accent, stand = room ink. Theme-aware via tokens.
export function MicMark({
  size = 24,
  accent = "var(--vox-ui)",
  ink = "var(--fg)",
}: {
  size?: number;
  accent?: string;
  ink?: string;
}) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-label="Karaoke mark">
      <rect x="9" y="2" width="6" height="12" rx="3" fill={accent}></rect>
      <path d="M5 11a7 7 0 0 0 14 0M12 18v3M8 21h8" stroke={ink} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"></path>
    </svg>
  );
}

// The marquee tile mark (kept for canvas boards / extension icons).
// `lit` = stage version (bulbs on night ink); unlit = booth version (ink on paper).
export function MarqueeMark({
  size = 48,
  lit = true,
  label = "K",
}: {
  size?: number;
  lit?: boolean;
  label?: string;
}) {
  // theme-aware: resolves from the surrounding room's tokens
  const bg = lit ? "var(--bg)" : "var(--bg-card)";
  const frame = lit ? "var(--bulb)" : "var(--fg)";
  const letter = frame;
  const bulbOn = lit ? "var(--bulb)" : "var(--vox-ui)";
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
export function MWordSign({
  word,
  sub,
  lit = true,
  size = 21,
}: {
  word: string;
  sub?: string;
  lit?: boolean;
  size?: number;
}) {
  return (
    <div className="m-sign" style={{
      padding: "14px 18px 12px", textAlign: "center",
      background: lit ? "var(--bg)" : "var(--bg-card)",
      borderColor: lit ? "var(--bulb)" : "var(--fg)",
    }}>
      <div style={{
        fontFamily: "var(--font-sign)", fontSize: size, letterSpacing: "0.04em", lineHeight: 1,
        color: lit ? "var(--bulb)" : "var(--fg)",
        textShadow: lit ? "var(--glow)" : "none",
      }}>{word}</div>
      {sub && <div className="m-mono" style={{ marginTop: 7, fontSize: 9.5, letterSpacing: "0.18em", color: "var(--muted)" }}>{sub}</div>}
      <div style={{ marginTop: 9, display: "flex", justifyContent: "center" }}>
        <MBulbs n={9} lit={lit ? 9 : 0} size={4} gap={7} />
      </div>
    </div>
  );
}

// duet waveform rows (vox up / inst down) — kept from v1, marquee-toned
function mBars(seed: number, n: number): number[] {
  const out: number[] = [];
  let x = seed;
  for (let i = 0; i < n; i++) {
    x = (x * 9301 + 49297) % 233280;
    out.push(0.25 + 0.75 * (x / 233280));
  }
  return out;
}

export function MDuetWave({
  seed = 7,
  w = 132,
  h = 26,
  played = 1,
}: {
  seed?: number;
  w?: number;
  h?: number;
  played?: number;
}) {
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
