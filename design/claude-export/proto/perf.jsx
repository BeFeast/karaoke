// KARAOKE prototype — fullscreen performance mode (laptop). Controls fade while singing.

function PerfScreen({ pb, mix, setMix, onExit, lyricScale, glow, vars = {}, theme = "night", onToggleTheme }) {
  const ls = lyricState(SONG.lines, pb.pos);
  const [idle, setIdle] = React.useState(false);
  const timer = React.useRef(null);

  const poke = React.useCallback(() => {
    setIdle(false);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setIdle(true), 3000);
  }, []);

  React.useEffect(() => {
    poke();
    const esc = (e) => { if (e.key === "Escape") onExit(); };
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("keydown", esc); clearTimeout(timer.current); };
  }, [poke, onExit]);

  const gapBulbs = ls.inGap && ls.next ? Math.min(8, Math.ceil(ls.gap)) : 0;
  const sz = lyricScale / 100;

  const dragBlend = (e) => {
    const rail = e.currentTarget;
    const move = (ev) => {
      const r = rail.getBoundingClientRect();
      const pct = ((ev.clientX - r.left) / r.width) * 100;
      setMix((m) => ({ ...m, vox: Math.round(Math.max(0, Math.min(100, pct))) }));
    };
    move(e);
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const day = theme === "day";
  return (
    <div className={day ? "m-booth" : "m-stage"} onPointerMove={poke}
      style={{ position: "fixed", inset: 0, zIndex: 50, display: "flex", flexDirection: "column", background: day ? "radial-gradient(120% 90% at 50% 112%, #ebe4d2 0%, #f5f2ea 56%)" : "radial-gradient(120% 90% at 50% 112%, #241a10 0%, #161210 56%)", ...vars }}>
      <div className="m-mono" style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 24px", fontSize: 11.5, color: "var(--muted)", opacity: idle ? 0 : 1, transition: "opacity .5s" }}>
        <MicMark size={22} />
        <span>{SONG.title} — {SONG.artist}</span>
        <span style={{ flex: 1 }}></span>
        <span>{fmtTime(pb.pos)} / {fmtTime(SONG.duration)}</span>
        {onToggleTheme && <button className="m-btn sm ghost" type="button" title="Day / night" onClick={onToggleTheme}>◐</button>}
        <button className="m-btn sm ghost" type="button" onClick={onExit}>esc ✕</button>
      </div>

      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: 18, padding: "0 64px", textAlign: "center", minHeight: 0 }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 20 * sz, fontWeight: 500, color: "var(--lyric-prev)", minHeight: 26 * sz }}>{ls.prev ? ls.prev.text : ""}</div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 41 * sz, fontWeight: 700, letterSpacing: "-0.015em", lineHeight: 1.15, minHeight: 48 * sz, textShadow: glow && !day ? "0 0 30px rgba(255,184,77,0.18)" : "none" }}>
          {ls.cur
            ? <MWipe text={ls.cur.text} pct={ls.sung} size={41 * sz} family="var(--font-display)" weight={700} fill="var(--accent)" dim="var(--lyric-dim)" />
            : <span style={{ color: "var(--lyric-dim)" }}>{ls.next ? "get ready…" : pb.pos < 2 ? SONG.title : "instrumental"}</span>}
        </div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 22 * sz, fontWeight: 540, color: "var(--lyric-next)", minHeight: 28 * sz }}>{ls.next ? ls.next.text : "— end —"}</div>
        <div style={{ display: "flex", justifyContent: "center", marginTop: 4, minHeight: 8 }}>
          {gapBulbs > 0 && <MBulbs n={8} lit={gapBulbs} size={7} gap={10} />}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 20, padding: "14px 24px 22px", opacity: idle ? 0 : 1, transition: "opacity .5s" }}>
        <button className="m-btn primary" type="button" onClick={() => pb.setPlaying(!pb.playing)}
          style={{ width: 46, height: 46, borderRadius: "50%", justifyContent: "center", fontSize: 16, boxShadow: glow && !day ? "var(--glow)" : "none" }}>
          {pb.playing ? "❚❚" : "▶"}
        </button>
        <div style={{ width: 320 }}>
          <div className="m-mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--muted)", marginBottom: 6 }}>
            <span className="m-stem inst">karaoke</span>
            <span>vox {mix.vox}%</span>
            <span className="m-stem vox">full voice</span>
          </div>
          <div onPointerDown={dragBlend} style={{ position: "relative", height: 14, display: "flex", alignItems: "center", cursor: "ew-resize", touchAction: "none" }}>
            <div style={{ position: "absolute", left: 0, right: 0, height: 5, borderRadius: 3, background: "linear-gradient(90deg, var(--inst), #4d5a66 45%, #6b5a33 55%, var(--vox))" }}></div>
            <span style={{ position: "absolute", left: mix.vox + "%", top: "50%", transform: "translate(-50%,-50%)", width: 15, height: 15, borderRadius: "50%", background: "var(--fg)", border: "3px solid var(--bg)", transition: "left .1s" }}></span>
          </div>
        </div>
        <span style={{ flex: 1 }}></span>
        <div className="m-wipebar" style={{ "--wipe": (pb.pos / SONG.duration) * 100 + "%", width: 200 }}><i></i></div>
      </div>
    </div>
  );
}

Object.assign(window, { PerfScreen });
