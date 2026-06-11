// KARAOKE v2 — Marquee · performance mode, phone-first (iPhone is the player).
// Uses IOSDevice from ios-frame.jsx and brand primitives from m-brand.jsx.

function PhonePerf() {
  return (
    <IOSDevice dark width={402} height={874}>
      <div className="m-stage" style={{
        height: "100%", display: "flex", flexDirection: "column",
        background: "radial-gradient(130% 80% at 50% 115%, #241a10 0%, #161210 58%)",
        paddingTop: 64,
      }}>
        {/* top strip */}
        <div className="m-mono" style={{ display: "flex", alignItems: "center", gap: 9, padding: "4px 18px", fontSize: 11, color: "var(--muted)" }}>
          <MarqueeMark size={20} lit label="K" />
          <span style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>Bohemian Rhapsody — Queen</span>
          <span style={{ marginLeft: "auto" }}>2:14 / 5:54</span>
          <span style={{ opacity: 0.65 }}>✕</span>
        </div>

        {/* lyrics — the room reads from here */}
        <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: 16, padding: "0 26px", textAlign: "center" }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 17, fontWeight: 500, color: "#6e6354", lineHeight: 1.3 }}>Put a gun against his head</div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 29, fontWeight: 700, letterSpacing: "-0.01em", lineHeight: 1.22 }}>
            <MWipe text="Pulled my trigger," pct={100} size={29} family="var(--font-display)" weight={700} fill="var(--accent)" dim="#54493a" />
            <br></br>
            <MWipe text="now he's dead" pct={42} size={29} family="var(--font-display)" weight={700} fill="var(--accent)" dim="#54493a" />
          </div>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 18, fontWeight: 540, color: "#8d8170", lineHeight: 1.3 }}>Mama, life had just begun</div>
          <div style={{ display: "flex", justifyContent: "center", marginTop: 4 }}>
            <MBulbs n={8} lit={3} size={7} gap={9} />
          </div>
        </div>

        {/* thumb zone */}
        <div style={{ padding: "0 22px 56px", display: "grid", gap: 18 }}>
          <div>
            <div className="m-mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 10.5, color: "var(--muted)", marginBottom: 8 }}>
              <span className="m-stem inst">karaoke</span>
              <span style={{ color: "var(--fg-soft)" }}>guide vocals 25%</span>
              <span className="m-stem vox">full voice</span>
            </div>
            <div style={{ position: "relative", height: 6, borderRadius: 3, background: "linear-gradient(90deg, var(--inst) 0%, #4d5a66 45%, #6b5a33 55%, var(--vox) 100%)" }}>
              <span style={{
                position: "absolute", left: "25%", top: "50%", transform: "translate(-50%,-50%)",
                width: 26, height: 26, borderRadius: "50%", background: "var(--fg)",
                border: "4px solid var(--bg)", boxShadow: "0 2px 8px rgba(0,0,0,0.6)",
              }}></span>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 26 }}>
            <button className="m-btn" type="button" style={{ width: 52, height: 52, borderRadius: "50%", justifyContent: "center", fontSize: 13 }}>−5s</button>
            <button className="m-btn primary" type="button" style={{ width: 72, height: 72, borderRadius: "50%", justifyContent: "center", fontSize: 24, boxShadow: "var(--glow)" }}>❚❚</button>
            <button className="m-btn" type="button" style={{ width: 52, height: 52, borderRadius: "50%", justifyContent: "center", fontSize: 13 }}>+5s</button>
          </div>
          <div className="m-wipebar" style={{ "--wipe": "38%" }}><i></i></div>
        </div>
      </div>
    </IOSDevice>
  );
}

function PhonePerfBoard() {
  return (
    <div style={{ boxSizing: "border-box", height: "100%", display: "grid", placeItems: "center", background: "#221d18", padding: 18 }}>
      <PhonePerf />
    </div>
  );
}

// laptop / TV fullscreen
function LaptopPerfBoard() {
  return (
    <div className="m-stage" style={{ height: "100%", display: "flex", flexDirection: "column", background: "radial-gradient(120% 90% at 50% 112%, #241a10 0%, #161210 56%)" }}>
      <div className="m-mono" style={{ display: "flex", alignItems: "center", gap: 12, padding: "16px 24px", fontSize: 11.5, color: "var(--muted)" }}>
        <MarqueeMark size={24} lit label="K" />
        <span>Bohemian Rhapsody — Queen</span>
        <span style={{ flex: 1 }}></span>
        <span>2:14 / 5:54</span>
        <span style={{ opacity: 0.6 }}>esc exits · controls fade while you sing</span>
      </div>

      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: 18, padding: "0 64px", textAlign: "center" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 20, fontWeight: 500, color: "#6e6354" }}>Put a gun against his head</div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 41, fontWeight: 700, letterSpacing: "-0.015em", lineHeight: 1.15 }}>
          <MWipe text="Pulled my trigger, now he's dead" pct={64} size={41} family="var(--font-display)" weight={700} fill="var(--accent)" dim="#54493a" />
        </div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 22, fontWeight: 540, color: "#8d8170" }}>Mama, life had just begun</div>
        <div style={{ display: "flex", justifyContent: "center", marginTop: 4 }}>
          <MBulbs n={8} lit={3} size={7} gap={10} />
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 20, padding: "14px 24px 20px" }}>
        <button className="m-btn primary" type="button" style={{ width: 46, height: 46, borderRadius: "50%", justifyContent: "center", fontSize: 16, boxShadow: "var(--glow)" }}>❚❚</button>
        <div style={{ width: 320 }}>
          <div className="m-mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--muted)", marginBottom: 6 }}>
            <span className="m-stem inst">karaoke</span>
            <span className="m-stem vox">full voice</span>
          </div>
          <div style={{ position: "relative", height: 5, borderRadius: 3, background: "linear-gradient(90deg, var(--inst), #4d5a66 45%, #6b5a33 55%, var(--vox))" }}>
            <span style={{ position: "absolute", left: "25%", top: "50%", transform: "translate(-50%,-50%)", width: 15, height: 15, borderRadius: "50%", background: "var(--fg)", border: "3px solid var(--bg)" }}></span>
          </div>
        </div>
        <span style={{ flex: 1 }}></span>
        <div className="m-wipebar" style={{ "--wipe": "38%", width: 200 }}><i></i></div>
      </div>
    </div>
  );
}

Object.assign(window, { PhonePerfBoard, LaptopPerfBoard });
