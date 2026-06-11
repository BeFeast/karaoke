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
