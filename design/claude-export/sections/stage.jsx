// KARAOKE redesign — Section 3: the Stage (share page + performance mode).
// Dark room. Uses window.KMark / KWordmark / Wipe / DuetWave from earlier files.

function bigBars(seed, n) {
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

// The duet waveform: vocals up, instrumental down, playhead splits past/future.
function StageWave({ w = 820, h = 120, played = 0.38 }) {
  const n = 110;
  const vox = bigBars(5, n);
  const inst = bigBars(19, n);
  const bw = w / n;
  const half = h / 2;
  return (
    <svg width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block" }} aria-label="Duet waveform">
      {vox.map((v, i) => {
        const past = i / n <= played;
        return <rect key={"v" + i} x={i * bw + 0.8} y={half - 2 - v * (half - 6)} width={bw - 1.6} height={v * (half - 6)} rx="1"
          fill="#e8a93c" opacity={past ? 0.95 : 0.28}></rect>;
      })}
      {inst.map((v, i) => {
        const past = i / n <= played;
        return <rect key={"i" + i} x={i * bw + 0.8} y={half + 2} width={bw - 1.6} height={v * (half - 6)} rx="1"
          fill="#7fa3c4" opacity={past ? 0.85 : 0.22}></rect>;
      })}
      <line x1={w * played} x2={w * played} y1="0" y2={h} stroke="#ece9e2" strokeWidth="1.5"></line>
    </svg>
  );
}

// Blend slider — instrumental ←→ vocals, colored as the duet gradient.
function BlendSlider({ value = 25 }) {
  return (
    <div style={{ display: "grid", gap: 7 }}>
      <div className="k-mono" style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)" }}>
        <span className="k-stem inst">karaoke</span>
        <span style={{ color: "var(--fg-soft)" }}>guide vocals · {value}%</span>
        <span className="k-stem vox">full vocals</span>
      </div>
      <div style={{ position: "relative", height: 6, borderRadius: 3, background: "linear-gradient(90deg, #7fa3c4 0%, #4d5a66 45%, #6b5a33 55%, #e8a93c 100%)", opacity: 0.9 }}>
        <span style={{
          position: "absolute", left: value + "%", top: "50%", transform: "translate(-50%,-50%)",
          width: 16, height: 16, borderRadius: "50%", background: "var(--fg)",
          border: "3px solid var(--bg)", boxShadow: "0 1px 4px rgba(0,0,0,0.5)",
        }}></span>
      </div>
    </div>
  );
}

const LYRICS = [
  ["Mama, just killed a man", 100],
  ["Put a gun against his head", 100],
  ["Pulled my trigger, now he's dead", 64],
  ["Mama, life had just begun", 0],
  ["But now I've gone and thrown it all away", 0],
];

function SharePageBoard() {
  return (
    <div className="k-stage" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "0 24px", height: 56, borderBottom: "1px solid var(--border)" }}>
        <KWordmark size={18} color="var(--fg)" />
        <span style={{ flex: 1 }}></span>
        <span className="k-chip info">unlisted share</span>
        <button className="k-btn sm" type="button">⧉ Copy link</button>
      </div>

      <div style={{ flex: 1, maxWidth: 880, width: "100%", margin: "0 auto", padding: "30px 28px", display: "flex", flexDirection: "column", gap: 22 }}>
        {/* header */}
        <div>
          <div className="k-mono" style={{ fontSize: 11, color: "var(--muted)", display: "flex", gap: 12 }}>
            <span className="k-chip ok"><span className="k-dot"></span>ready</span>
            <span style={{ alignSelf: "center" }}>5:54 · split today 21:03 · shared by oleg</span>
          </div>
          <h1 style={{ margin: "10px 0 0", fontFamily: "var(--font-display)", fontWeight: 650, fontSize: 34, letterSpacing: "-0.02em", lineHeight: 1.1 }}>
            Bohemian Rhapsody
          </h1>
          <div style={{ marginTop: 4, fontSize: 14, color: "var(--muted)" }}>Queen — A Night at the Opera</div>
        </div>

        {/* player */}
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "18px 20px", boxShadow: "var(--shadow)" }}>
          <StageWave />
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 14 }}>
            <button className="k-btn primary" type="button" style={{ width: 40, height: 40, borderRadius: "50%", justifyContent: "center", fontSize: 15 }}>▶</button>
            <button className="k-btn sm" type="button">−5s</button>
            <button className="k-btn sm" type="button">+5s</button>
            <span className="k-mono" style={{ fontSize: 12.5, color: "var(--fg-soft)" }}>2:14 / 5:54</span>
            <span style={{ flex: 1 }}></span>
            <button className="k-btn sm" type="button">⟲ A–B</button>
            <button className="k-btn sm" type="button">1.0×</button>
            <button className="k-btn sm primary" type="button" style={{ background: "transparent", color: "var(--accent)", borderColor: "var(--accent)" }}>
              ⤢ Performance mode
            </button>
          </div>
          <div style={{ marginTop: 18 }}>
            <BlendSlider value={25} />
          </div>
        </div>

        {/* lyrics */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 24 }}>
          <div style={{ display: "grid", gap: 10, padding: "4px 2px" }}>
            <div className="k-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)" }}>lyrics · synced · click to seek</div>
            {LYRICS.map(([line, pct], i) => (
              <div key={i} style={{ fontFamily: "var(--font-display)", fontSize: pct > 0 && pct < 100 ? 21 : 16, fontWeight: pct > 0 && pct < 100 ? 640 : 500, lineHeight: 1.3, transition: "all .2s" }}>
                <Wipe text={line} pct={pct} fill="#e8a93c" dim={pct === 100 ? "#6b675e" : "#4a473f"} size={pct > 0 && pct < 100 ? 21 : 16} family="var(--font-display)" weight={pct > 0 && pct < 100 ? 640 : 500} />
              </div>
            ))}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "stretch", minWidth: 168 }}>
            <div className="k-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)" }}>take home</div>
            <button className="k-btn sm" type="button"><span className="k-stem vox"></span>vocals.mp3</button>
            <button className="k-btn sm" type="button"><span className="k-stem inst"></span>karaoke.mp3</button>
            <button className="k-btn sm" type="button">≡ lyrics.lrc</button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Performance mode — TV across the room ─────────────────────────────────
function PerformanceBoard() {
  return (
    <div className="k-stage" style={{ height: "100%", display: "flex", flexDirection: "column", background: "radial-gradient(120% 90% at 50% 110%, #1d1820 0%, #131216 55%)" }}>
      {/* minimal top strip */}
      <div className="k-mono" style={{ display: "flex", alignItems: "center", gap: 12, padding: "14px 22px", fontSize: 11.5, color: "var(--muted)" }}>
        <KMark size={16} />
        <span>Bohemian Rhapsody — Queen</span>
        <span style={{ flex: 1 }}></span>
        <span>2:14 / 5:54</span>
        <span style={{ opacity: 0.6 }}>esc to exit</span>
      </div>

      {/* lyrics center stage */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", gap: 18, padding: "0 56px", textAlign: "center" }}>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 21, fontWeight: 500, color: "#5b574e" }}>Put a gun against his head</div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 40, fontWeight: 700, letterSpacing: "-0.015em", lineHeight: 1.15 }}>
          <Wipe text="Pulled my trigger, now he's dead" pct={64} fill="#e8a93c" dim="#4a473f" size={40} family="var(--font-display)" weight={700} />
        </div>
        <div style={{ fontFamily: "var(--font-display)", fontSize: 23, fontWeight: 540, color: "#7d786d" }}>Mama, life had just begun</div>
        {/* gap countdown: bulbs burn down during instrumental breaks */}
        <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 6 }}>
          {Array.from({ length: 8 }).map((_, i) => (
            <span key={i} style={{ width: 7, height: 7, borderRadius: "50%", background: i < 3 ? "#e8a93c" : "#33302a" }}></span>
          ))}
        </div>
      </div>

      {/* bottom transport — thumb-sized, fades out while singing */}
      <div style={{ display: "flex", alignItems: "center", gap: 18, padding: "16px 22px 20px" }}>
        <button className="k-btn primary" type="button" style={{ width: 44, height: 44, borderRadius: "50%", justifyContent: "center", fontSize: 16 }}>❚❚</button>
        <div style={{ flex: 1, maxWidth: 420 }}>
          <BlendSlider value={25} />
        </div>
        <span style={{ flex: 1 }}></span>
        <div className="k-wipebar" style={{ "--wipe": "38%", width: 180 }}><i></i></div>
      </div>
    </div>
  );
}

Object.assign(window, { SharePageBoard, PerformanceBoard, StageWave, BlendSlider });
