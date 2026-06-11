// KARAOKE prototype — app shell: routing, global keys, tweaks.

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "accent": "#ffb84d",
  "stageTheme": "night",
  "defaultView": "console",
  "lyricScale": 100,
  "glow": true,
  "infraStrip": true
}/*EDITMODE-END*/;

function ProtoApp() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const [screen, setScreen] = React.useState(() => localStorage.getItem("kproto-screen") || "booth");
  const [view, setViewRaw] = React.useState(() => localStorage.getItem("kproto-view") || TWEAK_DEFAULTS.defaultView);
  const [mix, setMix] = React.useState({ vox: 25, inst: 90 });
  const [ducked, setDucked] = React.useState(false);
  const pb = usePlayback(SONG.duration);

  const go = (s) => { localStorage.setItem("kproto-screen", s); setScreen(s); };
  const setView = (v) => { localStorage.setItem("kproto-view", v); setViewRaw(v); };

  // global transport keys on stage/perf
  React.useEffect(() => {
    const dn = (e) => {
      if (screen === "booth" || e.target.tagName === "INPUT") return;
      if (e.code === "Space") { e.preventDefault(); pb.setPlaying(!pb.playing); }
      if (e.key === "ArrowLeft") pb.setPos(pb.pos - 5);
      if (e.key === "ArrowRight") pb.setPos(pb.pos + 5);
    };
    window.addEventListener("keydown", dn);
    return () => window.removeEventListener("keydown", dn);
  });

  // bright neon for night; inked equivalents so day keeps contrast
  const DAY_INK = { "#ffb84d": "#a8650f", "#e8a93c": "#a8650f", "#e06a5a": "#b3503f", "#9fd07a": "#5f7a4a" };
  const day = t.stageTheme === "day";
  const ui = day ? (DAY_INK[t.accent] || t.accent) : t.accent;
  const accentVars = {
    "--accent": ui,
    "--bulb": t.accent,
    "--vox": t.accent,
    "--vox-ui": ui,
    "--glow": t.glow && !day ? `0 0 18px ${t.accent}59` : "none",
  };

  return (
    <div style={{ height: "100%" }}>
      <div style={{ height: "100%", overflow: "auto" }}>
        {screen === "booth" && (
          <BoothScreen onSing={() => go("stage")} infraStrip={t.infraStrip} />
        )}
        {screen !== "booth" && (
          <div style={{ height: "100%" }}>
            <StageScreen pb={pb} mix={mix} setMix={setMix} ducked={ducked} setDucked={setDucked}
              view={view} setView={setView} glow={t.glow} theme={t.stageTheme} vars={accentVars}
              onBack={() => go("booth")} onFullscreen={() => go("perf")} />
            {screen === "perf" && (
              <PerfScreen pb={pb} mix={mix} setMix={setMix} onExit={() => go("stage")} lyricScale={t.lyricScale} glow={t.glow} vars={accentVars} theme={t.stageTheme} />
            )}
          </div>
        )}
      </div>

      <TweaksPanel>
        <TweakSection label="Stage" />
        <TweakRadio label="Stage theme" value={t.stageTheme} options={["night", "day"]}
          onChange={(v) => setTweak("stageTheme", v)} />
        <TweakColor label="Marquee amber" value={t.accent}
          options={["#ffb84d", "#e8a93c", "#e06a5a", "#9fd07a"]}
          onChange={(v) => setTweak("accent", v)} />
        <TweakToggle label="Neon glow" value={t.glow} onChange={(v) => setTweak("glow", v)} />
        <TweakRadio label="Default player" value={t.defaultView} options={["console", "setlist"]}
          onChange={(v) => { setTweak("defaultView", v); setView(v); }} />
        <TweakSection label="Performance mode" />
        <TweakSlider label="Lyric size" value={t.lyricScale} min={80} max={150} step={5} unit="%"
          onChange={(v) => setTweak("lyricScale", v)} />
        <TweakSection label="Booth" />
        <TweakToggle label="Infra strip" value={t.infraStrip} onChange={(v) => setTweak("infraStrip", v)} />
      </TweaksPanel>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<ProtoApp />);
