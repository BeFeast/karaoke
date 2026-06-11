// KARAOKE final — baked configuration, no tweaks panel.
// Day stage by default with a ◐ switch to night; green marquee accent;
// glow on (night); console player default; 150% perf lyrics; infra strip on.

const FINAL = {
  accentNight: "#9fd07a",
  accentDay: "#5f7a4a",
  glow: true,
  lyricScale: 150,
  infraStrip: true,
  defaultView: "console",
};

function FinalApp() {
  const [theme, setTheme] = React.useState(() => localStorage.getItem("kfinal-theme") || "day");
  const [screen, setScreen] = React.useState(() => localStorage.getItem("kfinal-screen") || "booth");
  const [view, setViewRaw] = React.useState(() => localStorage.getItem("kfinal-view") || FINAL.defaultView);
  const [mix, setMix] = React.useState({ vox: 25, inst: 90 });
  const [ducked, setDucked] = React.useState(false);
  const pb = usePlayback(SONG.duration);

  const go = (s) => { localStorage.setItem("kfinal-screen", s); setScreen(s); };
  const setView = (v) => { localStorage.setItem("kfinal-view", v); setViewRaw(v); };
  const toggleTheme = () => setTheme((th) => {
    const n = th === "day" ? "night" : "day";
    localStorage.setItem("kfinal-theme", n);
    return n;
  });

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

  const [signedIn, setSignedIn] = React.useState(() => localStorage.getItem("kfinal-auth") !== "out");
  const signIn = () => { localStorage.setItem("kfinal-auth", "in"); setSignedIn(true); };
  const signOut = () => { localStorage.setItem("kfinal-auth", "out"); go("booth"); setSignedIn(false); };

  const day = theme === "day";
  const ui = day ? FINAL.accentDay : FINAL.accentNight;
  const accentVars = {
    "--accent": ui,
    "--accent-soft": day ? "#e4ecd9" : FINAL.accentNight + "26",
    "--bulb": FINAL.accentNight,
    "--vox": FINAL.accentNight,
    "--vox-ui": ui,
    "--glow": FINAL.glow && !day ? `0 0 18px ${FINAL.accentNight}59` : "none",
  };

  // booth rooms are always light — inked green, soft green tint
  const boothVars = {
    "--accent": FINAL.accentDay,
    "--accent-soft": "#e4ecd9",
    "--bulb": FINAL.accentNight,
    "--vox": FINAL.accentNight,
    "--vox-ui": FINAL.accentDay,
    "--glow": "none",
  };

  return (
    <div style={{ height: "100%", overflow: "auto", ...boothVars }}>
      {!signedIn && <SignInScreen onSignIn={signIn} vars={boothVars} />}
      {signedIn && screen === "booth" && (
        <BoothScreen onSing={() => go("stage")} infraStrip={FINAL.infraStrip} onSignOut={signOut} onSettings={() => go("settings")} vars={boothVars} />
      )}
      {signedIn && screen === "settings" && (
        <SettingsScreen onBack={() => go("booth")} onSignOut={signOut} vars={boothVars} />
      )}
      {signedIn && screen !== "booth" && screen !== "settings" && (
        <div style={{ height: "100%" }}>
          <StageScreen pb={pb} mix={mix} setMix={setMix} ducked={ducked} setDucked={setDucked}
            view={view} setView={setView} glow={FINAL.glow} theme={theme} vars={accentVars}
            onToggleTheme={toggleTheme}
            onBack={() => go("booth")} onFullscreen={() => go("perf")} />
          {screen === "perf" && (
            <PerfScreen pb={pb} mix={mix} setMix={setMix} onExit={() => go("stage")}
              lyricScale={FINAL.lyricScale} glow={FINAL.glow} vars={accentVars} theme={theme}
              onToggleTheme={toggleTheme} />
          )}
        </div>
      )}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<FinalApp />);
