// KARAOKE v2 — Marquee · the Booth (light-first dashboard, operator porn).

function StageRow({ name, detail, timing, state, pct }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "64px 1fr auto", gap: 12, alignItems: "baseline", padding: "7px 0", borderTop: "1px solid var(--border-soft)" }}>
      <span className="m-mono" style={{ fontSize: 11.5, fontWeight: 700, color: state === "todo" ? "var(--muted)" : "var(--fg)" }}>{name}</span>
      <span className="m-mono" style={{ fontSize: 11.5, color: "var(--muted)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {state === "run" ? <MWipe text={detail} pct={pct} size={11.5} /> : detail}
      </span>
      <span className="m-mono" style={{ fontSize: 11.5, color: state === "done" ? "var(--ok)" : state === "run" ? "var(--accent)" : "var(--muted)" }}>
        {state === "done" ? `✓ ${timing}` : state === "run" ? `${pct}% · ${timing}` : "—"}
      </span>
    </div>
  );
}

// The operator-porn card: a running job with the machinery exposed.
function RunningJobCard() {
  return (
    <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "15px 18px", boxShadow: "var(--shadow-sm)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className="m-chip run"><span className="m-dot"></span>splitting</span>
        <span style={{ fontSize: 14.5, fontWeight: 650, letterSpacing: "-0.005em", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          Bohemian Rhapsody — Queen
        </span>
        <span className="m-mono" style={{ fontSize: 12.5, color: "var(--accent)", fontWeight: 600 }}>$0.21</span>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>live</span>
        <button className="m-btn sm ghost" type="button">Cancel</button>
      </div>

      <div style={{ margin: "12px 0 0" }}>
        <StageRow name="fetch" detail="yt-dlp · devbox (residential ip) · 24.3 MB" timing="0:12" state="done"></StageRow>
        <StageRow name="gpu" detail="vast.ai #8814042 · RTX 4090 · $0.39/hr · cuda 12.4" timing="0:48" state="done"></StageRow>
        <StageRow name="split" detail="demucs htdemucs_ft · two stems" timing="1:21" state="run" pct={64}></StageRow>
        <StageRow name="lyrics" detail="faster-whisper large-v3 · waits for vocals stem" state="todo"></StageRow>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12, paddingTop: 11, borderTop: "1px solid var(--border-soft)" }}>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>job cap $0.80</span>
        <div className="m-wipebar" style={{ "--wipe": "26%", flex: 1, maxWidth: 220 }}><i></i></div>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>$0.21 / $0.80</span>
        <span style={{ flex: 1 }}></span>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>instance dies when the song's done — no exceptions</span>
      </div>
    </div>
  );
}

function CompactJob({ job }) {
  const { title, meta, state, receipt, seed, note } = job;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 16px", background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)" }}>
      {state === "ready" && <span className="m-chip ok"><span className="m-dot"></span>ready</span>}
      {state === "queued" && <span className="m-chip"><span className="m-dot"></span>queued</span>}
      {state === "failed" && <span className="m-chip err"><span className="m-dot"></span>failed</span>}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13.5, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</div>
        <div className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {meta}
          {receipt && <span title="teardown receipt"> · receipt: #{receipt} destroyed ✓</span>}
          {note && <span style={{ color: "var(--err)" }}> · {note}</span>}
        </div>
      </div>
      {state === "ready" && <MDuetWave seed={seed} w={110} h={24} />}
      {state === "ready" && <button className="m-btn sm primary" type="button">▸ Sing</button>}
      {state === "ready" && <button className="m-btn sm" type="button">⧉</button>}
      {state === "queued" && <span className="m-mono" style={{ fontSize: 11, color: "var(--muted)" }}>cap $0.80</span>}
      {state === "failed" && <button className="m-btn sm" type="button">↻ Retry</button>}
    </div>
  );
}

function MBoothBoard() {
  return (
    <div className="m-booth" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "0 24px", height: 56, borderBottom: "1px solid var(--border)" }}>
        <MarqueeMark size={26} lit={false} label="K" />
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 650, fontSize: 17, letterSpacing: "-0.01em" }}>Karaoke</span>
        <span style={{ flex: 1 }}></span>
        <span className="m-chip" style={{ textTransform: "uppercase", letterSpacing: "0.05em", fontSize: 10 }}>trusted lan</span>
        <span className="m-mono" style={{ fontSize: 12, color: "var(--muted)" }}>oleg@oklabs.uk</span>
        <button className="m-btn sm ghost" type="button" title="Theme">◐</button>
      </div>

      <div style={{ flex: 1, padding: "26px 24px 18px", maxWidth: 800, width: "100%", margin: "0 auto", display: "flex", flexDirection: "column", minHeight: 0 }}>
        {/* submit */}
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)", padding: 18, boxShadow: "var(--shadow-sm)" }}>
          <div style={{ display: "flex", gap: 10 }}>
            <input readOnly value="https://youtu.be/fJ9rUzIMcZQ" style={{
              flex: 1, padding: "10px 13px", border: "1px solid var(--border)", borderRadius: "var(--radius)",
              background: "var(--bg)", color: "var(--fg)", fontSize: 14, fontFamily: "var(--font-mono)",
            }}></input>
            <button className="m-btn primary" type="button" style={{ fontSize: 14, padding: "8px 18px" }}>Put it on stage</button>
          </div>
          <div className="m-mono" style={{ display: "flex", gap: 18, marginTop: 11, fontSize: 11, color: "var(--muted)", alignItems: "center" }}>
            <span>one link →</span>
            <span className="m-stem vox">vocals.mp3</span>
            <span className="m-stem inst">karaoke.mp3</span>
            <span style={{ color: "var(--fg-soft)" }}>≡ lyrics.lrc</span>
            <span style={{ marginLeft: "auto" }}>est $0.25–0.45 · ~4 min</span>
          </div>
        </div>

        {/* filters */}
        <div style={{ display: "flex", alignItems: "center", gap: 4, margin: "20px 0 11px" }}>
          {[["Tonight", 5, true], ["Active", 2, false], ["Ready", 2, false], ["Failed", 1, false]].map(([f, n, on]) => (
            <button key={f} type="button" className="m-btn sm" style={{
              border: "1px solid " + (on ? "var(--accent)" : "transparent"),
              background: on ? "var(--accent-soft)" : "transparent",
              color: on ? "var(--accent)" : "var(--fg-soft)", fontWeight: on ? 650 : 500,
            }}>{f} <span className="m-mono" style={{ fontSize: 10.5, opacity: 0.75 }}>{n}</span></button>
          ))}
          <span style={{ flex: 1 }}></span>
          <button className="m-btn sm ghost" type="button">Clear failed</button>
        </div>

        {/* feed */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <RunningJobCard />
          <CompactJob job={{ state: "queued", title: "Зимний сон — Алсу", meta: "queued · 2 min ago · waiting for GPU" }} />
          <CompactJob job={{ state: "ready", title: "Shallow — Lady Gaga, Bradley Cooper", meta: "3:37 · today 21:40 · $0.34", receipt: "8813990", seed: 11 }} />
          <CompactJob job={{ state: "ready", title: "Vampire — Olivia Rodrigo", meta: "3:39 · today 20:12 · $0.29", receipt: "8813871", seed: 4 }} />
          <CompactJob job={{ state: "failed", title: "My Heart Will Go On — Céline Dion", meta: "yesterday", note: "yt-dlp: sign-in required — resubmit from a signed-in browser" }} />
        </div>

        {/* infra strip */}
        <div className="m-mono" style={{
          marginTop: "auto", paddingTop: 16, display: "flex", gap: 18, fontSize: 11,
          color: "var(--muted)", borderTop: "1px dashed var(--border-soft)", alignItems: "center",
        }}>
          <span><span style={{ color: "var(--ok)" }}>●</span> vast.ai · 1 instance up</span>
          <span>today $1.12 / cap $6.00</span>
          <span>nfs odin ✓</span>
          <span>ws live ✓</span>
          <span style={{ marginLeft: "auto" }}>0 orphaned instances · ever</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { MBoothBoard });
