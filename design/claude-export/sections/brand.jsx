// KARAOKE redesign — Section 1: framing + brand identity directions.
// Exposes shared brand primitives (KMark, KWordmark, Wipe) on window for the
// other section files.

// ── The fork mark ──────────────────────────────────────────────────────────
// One line in, two lines out. Top branch = vocals (amber), bottom branch =
// instrumental (blue). `mono` renders single-color for tiny/favicon use.
function KMark({ size = 48, stem = "#ece9e2", vox = "#e8a93c", inst = "#7fa3c4", mono = null, weight = 3.4, wave = true }) {
  const s = mono ?? stem;
  const v = mono ?? vox;
  const i = mono ?? inst;
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" fill="none" aria-label="Karaoke mark">
      <path d="M5 24 H 19" stroke={s} strokeWidth={weight} strokeLinecap="round"></path>
      {wave ? (
        <path d="M19 24 C 26 24 25 13.5 31.5 13.5 C 37 13.5 36.5 17.5 43 16.5" stroke={v} strokeWidth={weight} strokeLinecap="round"></path>
      ) : (
        <path d="M19 24 L 43 14" stroke={v} strokeWidth={weight} strokeLinecap="round"></path>
      )}
      {wave ? (
        <path d="M19 24 C 26 24 25 34.5 31.5 34.5 C 37 34.5 36.5 30.5 43 31.5" stroke={i} strokeWidth={weight} strokeLinecap="round"></path>
      ) : (
        <path d="M19 24 L 43 34" stroke={i} strokeWidth={weight} strokeLinecap="round"></path>
      )}
    </svg>
  );
}

function KWordmark({ size = 26, color = "currentColor", markBg = null }) {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: size * 0.42, color }}>
      <span style={{
        display: "grid", placeItems: "center",
        width: size * 1.35, height: size * 1.35, borderRadius: size * 0.28,
        background: markBg ?? "transparent",
      }}>
        <KMark size={size * (markBg ? 0.95 : 1.25)} stem={markBg ? "#ece9e2" : "currentColor"} />
      </span>
      <span style={{
        fontFamily: "var(--font-display)", fontWeight: 640, fontSize: size,
        letterSpacing: "-0.015em", lineHeight: 1,
      }}>karaoke</span>
    </span>
  );
}

// ── The wipe — progress rendered as a lyric being sung ────────────────────
function Wipe({ text, pct, fill, dim, size = 12, family = "var(--font-mono)", weight = 500 }) {
  return (
    <span className="k-wipe" style={{ fontSize: size, fontFamily: family, fontWeight: weight }}>
      <span className="k-wipe-dim" style={dim ? { color: dim } : null}>{text}</span>
      <span className="k-wipe-fill" style={{ width: pct + "%", ...(fill ? { color: fill } : null) }} aria-hidden="true">{text}</span>
    </span>
  );
}

Object.assign(window, { KMark, KWordmark, Wipe });

// ── Artboard 1 · Assumptions & reasoning ──────────────────────────────────
function FramingBoard() {
  const h2 = { font: "600 11px/1 var(--font-mono, monospace)", letterSpacing: "0.09em", textTransform: "uppercase", color: "#8b8577", margin: "20px 0 8px" };
  const p = { margin: "0 0 8px", fontSize: 13, lineHeight: 1.55, color: "#3d3a33" };
  const li = { margin: "0 0 6px", fontSize: 13, lineHeight: 1.5, color: "#3d3a33" };
  return (
    <div style={{ boxSizing: "border-box", padding: "28px 30px", background: "#faf9f6", height: "100%", fontFamily: "ui-sans-serif, system-ui" }}>
      <div style={{ fontSize: 19, fontWeight: 650, letterSpacing: "-0.01em", color: "#211f1a" }}>Redesign brief — what I read, what I decided</div>
      <div style={h2}>Context absorbed</div>
      <p style={p}>Repo README + AGENTS.md + the full SPA and extension source. Current UI is literally Scribe's "field" variant (olive/sage, Geist, compact feed) — a costume borrowed from a sibling. The PRD lives in your Obsidian vault and wasn't attached; the README's verification gate and hard rules stand in for it.</p>
      <div style={h2}>The metaphor — the split</div>
      <p style={p}>Karaoke's one true fact: <b>one signal goes in, two come out.</b> The mark is a fork — a line that splits into an amber wave (vocals) and a blue wave (instrumental). The duet pair colors the whole product: stems, waveforms, blend slider, even the architecture diagram.</p>
      <p style={p}>The second signature is the <b>wipe</b> — the left-to-right lyric fill every karaoke screen uses. Here it becomes the progress language: a job's stage label literally gets "sung" as it completes.</p>
      <div style={h2}>Two rooms</div>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        <li style={li}><b>The Booth</b> — dashboard, queue, settings. Light, dense, mono numerals, operator-calm. Scribe's sibling, not its twin.</li>
        <li style={li}><b>The Stage</b> — share page, player, performance mode. Dark, warm, big display type. Karaoke happens at night.</li>
      </ul>
      <div style={h2}>Decisions taken on defaults</div>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        <li style={li}>Three identity directions below; <b>A · Split Signal</b> is carried through all screens.</li>
        <li style={li}>Extension gets a popup: a 3-job mini feed with live wipes — submit stays one click.</li>
        <li style={li}>Pipeline stays visible (download → GPU → split → lyrics) incl. vast.ai cost + teardown tick — operator pride, quietly.</li>
        <li style={li}>Type: UI keeps the no-webfont discipline; lyrics/display get one face (Bricolage Grotesque) with system fallback.</li>
      </ul>
      <div style={h2}>Open questions for you</div>
      <ul style={{ margin: 0, paddingLeft: 18 }}>
        <li style={li}>Attach the PRD + excalidraw and I'll fold in anything it contradicts.</li>
        <li style={li}>Marquee (B) or Ink (C) elements worth stealing into A?</li>
        <li style={li}>Performance mode: phone-in-hand or TV-across-the-room first?</li>
      </ul>
    </div>
  );
}

// ── Artboard 2 · Direction A — Split Signal ───────────────────────────────
function DirectionA() {
  const label = { font: "600 10px/1 var(--font-mono, monospace)", letterSpacing: "0.09em", textTransform: "uppercase", color: "#847f74", marginBottom: 8 };
  return (
    <div className="k-stage" style={{ height: "100%", padding: 26, display: "flex", flexDirection: "column", gap: 18 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <KWordmark size={30} color="#ece9e2" />
        <span className="k-chip run">lead direction</span>
      </div>
      <div style={{ fontSize: 12.5, color: "#a8a397", lineHeight: 1.5 }}>
        One line in, two out. Quiet operator surfaces; amber/blue duet does all the talking.
        Booth is light paper, Stage is warm black.
      </div>

      <div>
        <div style={label}>the duet pair</div>
        <div style={{ display: "flex", gap: 10 }}>
          <div style={{ flex: 1, borderRadius: 8, padding: "10px 12px", background: "#e8a93c", color: "#16130c" }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>vocals</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, opacity: 0.7 }}>#E8A93C</div>
          </div>
          <div style={{ flex: 1, borderRadius: 8, padding: "10px 12px", background: "#7fa3c4", color: "#0e141a" }}>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>instrumental</div>
            <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, opacity: 0.7 }}>#7FA3C4</div>
          </div>
        </div>
      </div>

      <div>
        <div style={label}>two rooms</div>
        <div style={{ display: "flex", gap: 10 }}>
          {[
            ["booth", "#f3f1ec", "#211f1a", "#d4cfc3"],
            ["stage", "#131216", "#ece9e2", "#2c2a31"],
          ].map(([name, bg, fg, br]) => (
            <div key={name} style={{ flex: 1, borderRadius: 8, padding: "10px 12px", background: bg, color: fg, border: `1px solid ${br}` }}>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{name}</div>
              <div style={{ fontFamily: "var(--font-mono)", fontSize: 10, opacity: 0.6 }}>{bg}</div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div style={label}>type</div>
        <div style={{ display: "grid", gap: 4 }}>
          <div style={{ fontFamily: "var(--font-display)", fontSize: 24, fontWeight: 640, letterSpacing: "-0.015em" }}>Bricolage Grotesque — lyrics &amp; display</div>
          <div style={{ fontSize: 13, color: "#c4c0b6" }}>System sans for UI — Geist falls back to the OS, no webfont owed.</div>
          <div style={{ fontFamily: "var(--font-mono)", fontSize: 12, color: "#847f74" }}>mono for ids, timings, money — 04:12 · $0.31 · ktx_7f2…</div>
        </div>
      </div>

      <div>
        <div style={label}>progress = the wipe</div>
        <div style={{ display: "grid", gap: 6, fontSize: 13 }}>
          <Wipe text="separating stems · demucs htdemucs_ft" pct={62} size={13} />
          <div className="k-wipebar" style={{ "--wipe": "62%", width: 260 }}><i></i></div>
        </div>
      </div>
    </div>
  );
}

// ── Artboard 3 · Direction B — Marquee ────────────────────────────────────
function DirectionB() {
  const bulbs = Array.from({ length: 26 });
  return (
    <div style={{ boxSizing: "border-box", height: "100%", padding: 26, background: "#16110d", color: "#f3e9d8", display: "flex", flexDirection: "column", gap: 18, fontFamily: "ui-sans-serif, system-ui" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ font: "600 10px/1 monospace", letterSpacing: "0.09em", textTransform: "uppercase", color: "#8c7a63" }}>direction b</span>
        <span style={{ font: "500 10px/1 monospace", color: "#8c7a63" }}>playful · night out</span>
      </div>
      <div style={{ position: "relative", border: "2px solid #e8a93c", borderRadius: 10, padding: "22px 18px 18px", textAlign: "center", boxShadow: "0 0 24px rgba(232,169,60,0.18), inset 0 0 18px rgba(232,169,60,0.10)" }}>
        <div style={{ position: "absolute", inset: 6, borderRadius: 7, border: "1px dashed rgba(232,169,60,0.35)" }}></div>
        <div style={{ fontFamily: "var(--font-display, inherit)", fontWeight: 700, fontSize: 30, letterSpacing: "0.06em", color: "#ffd98a", textShadow: "0 0 14px rgba(232,169,60,0.55)" }}>KARAOKE</div>
        <div style={{ font: "500 11px/1 monospace", color: "#c98f9d", marginTop: 6, letterSpacing: "0.22em" }}>TONIGHT · EVERY NIGHT</div>
      </div>
      <div style={{ display: "flex", justifyContent: "center", gap: 7 }}>
        {bulbs.map((_, i) => (
          <span key={i} style={{ width: 5, height: 5, borderRadius: "50%", background: i % 2 ? "#e06c9f" : "#e8a93c", opacity: i % 3 === 2 ? 0.35 : 1 }}></span>
        ))}
      </div>
      <div style={{ fontSize: 12.5, lineHeight: 1.55, color: "#b3a48e" }}>
        The service as a neon bar sign: marquee borders, bulb dots as progress, status copy in
        showtime voice ("NOW SPLITTING"). Joyful, but the glow fights operator density —
        better as the Stage's party trick than the whole identity.
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <span style={{ font: "600 10px/1 monospace", letterSpacing: "0.1em", color: "#e8a93c" }}>NOW SPLITTING</span>
        <div style={{ flex: 1, display: "flex", gap: 5 }}>
          {Array.from({ length: 14 }).map((_, i) => (
            <span key={i} style={{ width: 5, height: 5, borderRadius: "50%", background: i < 9 ? "#e8a93c" : "#3a2f22" }}></span>
          ))}
        </div>
        <span style={{ font: "500 11px/1 monospace", color: "#8c7a63" }}>64%</span>
      </div>
    </div>
  );
}

// ── Artboard 4 · Direction C — Ink & Wipe ─────────────────────────────────
function DirectionC() {
  return (
    <div style={{ boxSizing: "border-box", height: "100%", padding: 26, background: "#f7f5f0", color: "#1d1b16", display: "flex", flexDirection: "column", gap: 16, fontFamily: "ui-sans-serif, system-ui" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ font: "600 10px/1 monospace", letterSpacing: "0.09em", textTransform: "uppercase", color: "#9a937f" }}>direction c</span>
        <span style={{ font: "500 10px/1 monospace", color: "#9a937f" }}>editorial · lyric sheet</span>
      </div>
      <div style={{ borderTop: "2px solid #1d1b16", borderBottom: "1px solid #d8d3c6", padding: "14px 0" }}>
        <div style={{ fontFamily: "var(--font-display, inherit)", fontWeight: 650, fontSize: 26, letterSpacing: "-0.01em" }}>karaoke<span style={{ color: "#d4502e" }}>.</span></div>
        <div style={{ font: "500 11px/1.4 monospace", color: "#9a937f", marginTop: 4 }}>vocals / instrumental / lyrics — typed like a setlist</div>
      </div>
      <div style={{ display: "grid", gap: 8, fontFamily: "var(--font-display, inherit)", fontSize: 17, fontWeight: 560, lineHeight: 1.35 }}>
        <Wipe text="Is this the real life?" pct={100} fill="#d4502e" dim="#c9c2b2" size={17} family="inherit" weight={560} />
        <Wipe text="Is this just fantasy?" pct={45} fill="#d4502e" dim="#c9c2b2" size={17} family="inherit" weight={560} />
        <Wipe text="Caught in a landslide…" pct={0} fill="#d4502e" dim="#c9c2b2" size={17} family="inherit" weight={560} />
      </div>
      <div style={{ fontSize: 12.5, lineHeight: 1.55, color: "#5c574b" }}>
        Monochrome ink on paper, one red-orange highlighter. The whole UI behaves like an
        annotated lyric sheet: rules instead of cards, the wipe is the only color event.
        Beautiful and calm — but mono-accent gives up the vocals/instrumental duality that
        the player genuinely needs.
      </div>
      <div style={{ marginTop: "auto", display: "flex", gap: 10, alignItems: "center", borderTop: "1px solid #d8d3c6", paddingTop: 12 }}>
        <span style={{ font: "600 11px/1 monospace", color: "#1d1b16" }}>job #1042</span>
        <span style={{ font: "500 11px/1 monospace", color: "#9a937f" }}>whisper · large-v3</span>
        <span style={{ marginLeft: "auto", font: "600 11px/1 monospace", color: "#d4502e" }}>82%</span>
      </div>
    </div>
  );
}

Object.assign(window, { FramingBoard, DirectionA, DirectionB, DirectionC });
