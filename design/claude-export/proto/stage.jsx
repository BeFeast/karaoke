// KARAOKE prototype — the Stage: Console player (P1) with Setlist (P3) view toggle.

function TransportBar({ pb, onFullscreen }) {
  const { pos, setPos, playing, setPlaying, loop, cycleLoop } = pb;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
      <button className="m-btn primary" type="button" onClick={() => setPlaying(!playing)}
        style={{ width: 40, height: 40, borderRadius: "50%", justifyContent: "center", fontSize: 14 }}>
        {playing ? "❚❚" : "▶"}
      </button>
      <button className="m-btn sm" type="button" onClick={() => setPos(pos - 5)}>−5s</button>
      <button className="m-btn sm" type="button" onClick={() => setPos(pos + 5)}>+5s</button>
      <span className="m-mono" style={{ fontSize: 12, color: "var(--fg-soft)", minWidth: 76 }}>{fmtTime(pos)} / {fmtTime(SONG.duration)}</span>
      <span style={{ flex: 1 }}></span>
      <button className="m-btn sm" type="button" onClick={cycleLoop}
        style={loop ? { borderColor: "var(--accent)", color: "var(--accent)" } : null}>
        ⟲ {!loop ? "A–B" : loop.b == null ? "A set… (B?)" : `${fmtTime(loop.a)}–${fmtTime(loop.b)}`}
      </button>
      <button className="m-btn sm primary" type="button" onClick={onFullscreen}
        style={{ background: "transparent", color: "var(--accent)", borderColor: "var(--accent)" }}>⤢ Performance</button>
    </div>
  );
}

// P1 — console module: faders + DUCK
function ConsoleModule({ mix, setMix, ducked, setDucked }) {
  React.useEffect(() => {
    const dn = (e) => { if (e.key.toLowerCase() === "v" && !e.repeat) setDucked(true); };
    const up = (e) => { if (e.key.toLowerCase() === "v") setDucked(false); };
    window.addEventListener("keydown", dn);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", dn); window.removeEventListener("keyup", up); };
  }, [setDucked]);
  return (
    <div style={{ display: "flex", gap: 14, padding: "12px 16px", background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", alignItems: "center" }}>
      <ProtoFader label="VOX" color="var(--vox)" value={mix.vox} ducked={ducked} onChange={(v) => setMix((m) => ({ ...m, vox: v }))} />
      <ProtoFader label="INST" color="var(--inst)" value={mix.inst} onChange={(v) => setMix((m) => ({ ...m, inst: v }))} />
      <div style={{ display: "grid", gap: 8 }}>
        <button className="m-btn sm" type="button"
          onPointerDown={() => setDucked(true)} onPointerUp={() => setDucked(false)} onPointerLeave={() => setDucked(false)}
          style={{ borderColor: "var(--vox)", color: ducked ? "var(--accent-fg)" : "var(--vox)", background: ducked ? "var(--vox)" : "transparent", fontWeight: 700, justifyContent: "center" }}>DROP</button>
        <span className="m-mono" style={{ fontSize: 9, color: "var(--muted)", textAlign: "center", lineHeight: 1.4 }}>hold to drop vocals<br></br>while you sing · or "V"</span>
      </div>
    </div>
  );
}

// P3 — setlist module: marquee sign + spotlight dimmer
function SetlistModule({ pb, mix, setMix, glow }) {
  const ls = lyricState(SONG.lines, pb.pos);
  const gapBulbs = ls.inGap && ls.next ? Math.min(8, Math.ceil(ls.gap)) : 0;
  const dimmerDrag = (e) => {
    const rail = e.currentTarget;
    const move = (ev) => {
      const r = rail.getBoundingClientRect();
      const pct = 100 - ((ev.clientY - r.top) / r.height) * 100;
      setMix((m) => ({ ...m, vox: Math.round(Math.max(0, Math.min(100, pct))) }));
    };
    move(e);
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  return (
    <div style={{ display: "flex", gap: 16, alignItems: "stretch" }}>
      <div className="m-sign" style={{ flex: 1, padding: "16px 22px", display: "flex", flexDirection: "column", justifyContent: "center", gap: 9, textAlign: "center", boxShadow: glow ? undefined : "none" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 14, fontWeight: 500, color: "var(--lyric-prev)", minHeight: 18 }}>{ls.prev ? ls.prev.text : "—"}</div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 700, lineHeight: 1.25, minHeight: 28 }}>
          {ls.cur
            ? <MWipe text={ls.cur.text} pct={ls.sung} size={22} family="var(--font-display)" weight={700} fill="var(--accent)" dim="var(--lyric-dim)" />
            : <span style={{ color: "var(--lyric-dim)" }}>{ls.next ? "get ready…" : "intro"}</span>}
        </div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 15, fontWeight: 540, color: "var(--lyric-next)", minHeight: 19 }}>{ls.next ? ls.next.text : "— end —"}</div>
        <div style={{ display: "flex", justifyContent: "center", marginTop: 2, minHeight: 8 }}>
          {gapBulbs > 0 ? <MBulbs n={8} lit={gapBulbs} /> : <span></span>}
        </div>
      </div>
      <div style={{ display: "grid", justifyItems: "center", gridTemplateRows: "auto 1fr auto", padding: "4px 0", gap: 7 }}>
        <span className="m-mono" style={{ fontSize: 9, color: "var(--vox)" }}>VOX</span>
        <div onPointerDown={dimmerDrag} style={{ width: 16, display: "flex", justifyContent: "center", cursor: "ns-resize", touchAction: "none" }}>
          <div style={{ width: 4, borderRadius: 2, background: "linear-gradient(180deg, var(--vox), var(--inst))", position: "relative" }}>
            <span style={{ position: "absolute", top: (100 - mix.vox) + "%", left: "50%", transform: "translate(-50%,-50%)", width: 18, height: 18, borderRadius: "50%", background: "var(--fg)", border: "3px solid var(--bg)", transition: "top .1s" }}></span>
          </div>
        </div>
        <span className="m-mono" style={{ fontSize: 9, color: "var(--muted)" }}>{mix.vox}%</span>
      </div>
    </div>
  );
}

function StageScreen({ pb, mix, setMix, ducked, setDucked, view, setView, onBack, onFullscreen, glow, theme = "night", vars = {}, onToggleTheme }) {
  const ls = lyricState(SONG.lines, pb.pos);
  return (
    <div className={theme === "day" ? "m-booth" : "m-stage"} style={{ minHeight: "100%", display: "flex", flexDirection: "column", ...vars }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "0 22px", height: 54, borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
        <button className="m-btn sm ghost" type="button" onClick={onBack}>← booth</button>
        <MicMark size={20} />
        <span style={{ flex: 1 }}></span>
        <span className="m-chip info">unlisted share</span>
        <button className="m-btn sm" type="button">⧉ Copy link</button>
        {onToggleTheme && <button className="m-btn sm ghost" type="button" title="Day / night" onClick={onToggleTheme}>◐</button>}
      </div>

      <div style={{ flex: 1, maxWidth: 760, width: "100%", margin: "0 auto", padding: "22px 24px 20px", display: "flex", flexDirection: "column", gap: 16, minHeight: 0 }}>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 14 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="m-mono" style={{ fontSize: 11, color: "var(--muted)", display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
              <span>1:36 · split today 21:03 · {SONG.cost} · receipt #{SONG.receipt} ✓</span>
              <a href={SONG.url} target="_blank" rel="noopener" className="m-mono" style={{ color: "var(--info)", textDecoration: "none" }}>source: {SONG.url.replace("https://", "")} ↗</a>
            </div>
            <h1 style={{ margin: "6px 0 0", fontFamily: "var(--font-display)", fontWeight: 650, fontSize: 28, letterSpacing: "-0.02em", lineHeight: 1.1 }}>{SONG.title}</h1>
            <div style={{ marginTop: 2, fontSize: 13, color: "var(--muted)" }}>{SONG.artist}</div>
          </div>
          {/* view toggle */}
          <div style={{ display: "flex", border: "1px solid var(--border)", borderRadius: 8, overflow: "hidden" }}>
            {["console", "setlist"].map((v) => (
              <button key={v} type="button" onClick={() => setView(v)} className="m-mono" style={{
                appearance: "none", border: "none", cursor: "pointer", padding: "6px 13px", fontSize: 11,
                background: view === v ? "var(--accent)" : "var(--bg-card)",
                color: view === v ? "var(--accent-fg)" : "var(--fg-soft)", fontWeight: 650,
              }}>{v}</button>
            ))}
          </div>
        </div>

        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "14px 16px" }}>
          <ProtoWave pos={pb.pos} duration={SONG.duration} onSeek={pb.setPos}
            voxLevel={ducked ? 8 : mix.vox} instLevel={mix.inst} />
        </div>

        {view === "console" ? (
          <div style={{ display: "flex", gap: 16, alignItems: "stretch" }}>
            <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 11, justifyContent: "center", minWidth: 0 }}>
              <TransportBar pb={pb} onFullscreen={onFullscreen} />
              <div className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>space play · ←/→ seek · V drops vocals · click wave to seek</div>
              <div style={{ fontFamily: "var(--font-display)", fontSize: 17, fontWeight: 650, minHeight: 22 }}>
                {ls.cur && <MWipe text={ls.cur.text} pct={ls.sung} size={17} family="var(--font-display)" weight={650} fill="var(--accent)" dim="var(--lyric-dim)" />}
              </div>
            </div>
            <ConsoleModule mix={mix} setMix={setMix} ducked={ducked} setDucked={setDucked} />
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <SetlistModule pb={pb} mix={mix} setMix={setMix} glow={glow} />
            <TransportBar pb={pb} onFullscreen={onFullscreen} />
          </div>
        )}

        {/* take home */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginTop: "auto", borderTop: "1px dashed var(--border)", paddingTop: 13 }}>
          <span className="m-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)", marginRight: 6 }}>take home</span>
          <button className="m-btn sm" type="button"><span className="m-stem vox"></span>vocals.mp3</button>
          <button className="m-btn sm" type="button"><span className="m-stem inst"></span>karaoke.mp3</button>
          <button className="m-btn sm" type="button">≡ lyrics.lrc</button>
          <span style={{ flex: 1 }}></span>
          <a className="m-btn sm ghost" href={SONG.url} target="_blank" rel="noopener">▶ original ↗</a>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { StageScreen, TransportBar });
