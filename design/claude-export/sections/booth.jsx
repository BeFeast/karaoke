// KARAOKE redesign — Section 2: the Booth (dashboard).
// Light operator room. Single calm column: submit, filters, feed.
// Uses window.KMark / KWordmark / Wipe from sections/brand.jsx.

// deterministic pseudo-random bars for mini duet waveforms
function bars(seed, n) {
  const out = [];
  let x = seed;
  for (let i = 0; i < n; i++) {
    x = (x * 9301 + 49297) % 233280;
    out.push(0.25 + 0.75 * (x / 233280));
  }
  return out;
}

function DuetWave({ seed = 7, w = 132, h = 26 }) {
  const n = 22;
  const vox = bars(seed, n);
  const inst = bars(seed + 13, n);
  const bw = w / n;
  return (
    <svg width={w} height={h} aria-hidden="true">
      {vox.map((v, i) => (
        <rect key={"v" + i} x={i * bw + 1} y={h / 2 - v * (h / 2 - 1)} width={bw - 2} height={v * (h / 2 - 1)} rx="1" fill="var(--vox-ui)" opacity="0.9"></rect>
      ))}
      {inst.map((v, i) => (
        <rect key={"i" + i} x={i * bw + 1} y={h / 2 + 1} width={bw - 2} height={v * (h / 2 - 1)} rx="1" fill="var(--inst-ui)" opacity="0.75"></rect>
      ))}
    </svg>
  );
}

// pipeline as four wipes: fetch → gpu → split → lyrics
function StageTrail({ stages }) {
  return (
    <span style={{ display: "inline-flex", gap: 14, alignItems: "baseline" }}>
      {stages.map(([name, pct], i) => (
        <span key={name} style={{ display: "inline-flex", gap: 14, alignItems: "baseline" }}>
          {i > 0 && <span style={{ color: "var(--border)", fontSize: 11 }}>·</span>}
          <Wipe text={name} pct={pct} size={11.5} />
        </span>
      ))}
    </span>
  );
}

function JobRow({ job }) {
  const { title, by, status, pct, stage, stages, dur, cost, time, note } = job;
  const chip =
    status === "splitting" || status === "transcribing" ? <span className="k-chip run"><span className="k-dot"></span>{status}</span> :
    status === "queued" ? <span className="k-chip"><span className="k-dot"></span>queued</span> :
    status === "failed" ? <span className="k-chip err"><span className="k-dot"></span>failed</span> :
    <span className="k-chip ok"><span className="k-dot"></span>ready</span>;
  const active = status === "splitting" || status === "transcribing";
  const done = status === "ready";

  return (
    <div style={{
      display: "grid", gridTemplateColumns: "1fr auto", gap: "4px 16px",
      padding: "13px 16px", background: "var(--bg-card)",
      border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)",
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
          {chip}
          <span style={{ fontSize: 14, fontWeight: 600, letterSpacing: "-0.005em", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{title}</span>
        </div>
        <div className="k-mono" style={{ marginTop: 5, fontSize: 11.5, color: "var(--muted)", display: "flex", gap: 14, alignItems: "baseline", flexWrap: "wrap" }}>
          {active && stages && <StageTrail stages={stages} />}
          {!active && <span>{time} · {by}</span>}
          {done && <span>{dur}</span>}
          {cost && <span title="vast.ai spend, instance destroyed">gpu {cost}</span>}
          {note && <span style={{ color: status === "failed" ? "var(--err)" : "var(--muted)" }}>{note}</span>}
        </div>
        {active && (
          <div className="k-wipebar" style={{ "--wipe": pct + "%", marginTop: 9, maxWidth: 420 }}><i></i></div>
        )}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, alignSelf: "center" }}>
        {done && <DuetWave seed={job.seed}></DuetWave>}
        {done && <button className="k-btn sm primary" type="button">▸ Open</button>}
        {done && <button className="k-btn sm" type="button">⧉</button>}
        {active && <span className="k-mono" style={{ fontSize: 12, color: "var(--accent)" }}>{pct}%</span>}
        {active && <button className="k-btn sm ghost" type="button">Cancel</button>}
        {status === "failed" && <button className="k-btn sm" type="button">↻ Retry</button>}
        {status === "queued" && <button className="k-btn sm ghost" type="button">Cancel</button>}
      </div>
    </div>
  );
}

const BOOTH_JOBS = [
  { title: "Bohemian Rhapsody — Queen", status: "splitting", pct: 62, by: "oleg", time: "now",
    stages: [["fetch", 100], ["gpu up", 100], ["split", 62], ["lyrics", 0]], cost: "$0.21·live" },
  { title: "Зимний сон — Алсу", status: "queued", by: "oleg", time: "2 min ago",
    note: "waiting for GPU · cap $0.80/job" },
  { title: "Shallow — Lady Gaga, Bradley Cooper", status: "ready", seed: 11, by: "extension", time: "today 21:40", dur: "3:37", cost: "$0.34" },
  { title: "Vampire — Olivia Rodrigo", status: "ready", seed: 4, by: "masha · share", time: "today 20:12", dur: "3:39", cost: "$0.29" },
  { title: "My Heart Will Go On — Céline Dion", status: "failed", by: "oleg", time: "yesterday",
    note: "yt-dlp: sign-in required — resubmit from a browser with YouTube session" },
];

function DashboardBoard() {
  return (
    <div className="k-booth" style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* topbar */}
      <div style={{ display: "flex", alignItems: "center", gap: 14, padding: "0 24px", height: 56, borderBottom: "1px solid var(--border)" }}>
        <KWordmark size={19} color="var(--fg)" />
        <span style={{ flex: 1 }}></span>
        <span className="k-chip" style={{ textTransform: "uppercase", letterSpacing: "0.05em", fontSize: 10 }}>trusted lan</span>
        <span className="k-mono" style={{ fontSize: 12, color: "var(--muted)" }}>oleg@oklabs.uk</span>
        <button className="k-btn sm ghost" type="button" title="Theme">◐</button>
      </div>

      <div style={{ flex: 1, padding: "28px 24px 20px", maxWidth: 780, width: "100%", margin: "0 auto", display: "flex", flexDirection: "column" }}>
        {/* submit — the fork made literal */}
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)", padding: 18, boxShadow: "var(--shadow-sm)" }}>
          <div style={{ display: "flex", gap: 10 }}>
            <input
              readOnly
              value="https://youtu.be/fJ9rUzIMcZQ"
              style={{
                flex: 1, padding: "10px 13px", border: "1px solid var(--border)", borderRadius: "var(--radius)",
                background: "var(--bg)", color: "var(--fg)", fontSize: 14, fontFamily: "var(--font-mono)",
              }}
            ></input>
            <button className="k-btn primary" type="button" style={{ fontSize: 14, padding: "8px 18px" }}>
              <KMark size={17} mono="currentColor" wave={false} weight={4.5} /> Split
            </button>
          </div>
          <div className="k-mono" style={{ display: "flex", gap: 18, marginTop: 11, fontSize: 11, color: "var(--muted)", alignItems: "center" }}>
            <span>one link in →</span>
            <span className="k-stem vox">vocals.mp3</span>
            <span className="k-stem inst">karaoke.mp3</span>
            <span style={{ color: "var(--fg-soft)" }}>≡ lyrics.lrc</span>
            <span style={{ marginLeft: "auto" }}>est $0.25–0.45 · ~4 min</span>
          </div>
        </div>

        {/* filters */}
        <div style={{ display: "flex", alignItems: "center", gap: 4, margin: "22px 0 12px" }}>
          {[["Tonight", 5, true], ["Active", 2, false], ["Ready", 2, false], ["Failed", 1, false]].map(([f, n, on]) => (
            <button key={f} type="button" className="k-btn sm" style={{
              border: "1px solid " + (on ? "var(--accent)" : "transparent"),
              background: on ? "var(--accent-soft)" : "transparent",
              color: on ? "var(--accent)" : "var(--fg-soft)", fontWeight: on ? 600 : 500,
            }}>{f} <span className="k-mono" style={{ fontSize: 10.5, opacity: 0.75 }}>{n}</span></button>
          ))}
          <span style={{ flex: 1 }}></span>
          <button className="k-btn sm ghost" type="button">Clear failed</button>
        </div>

        {/* feed */}
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {BOOTH_JOBS.map((j) => <JobRow key={j.title} job={j}></JobRow>)}
        </div>

        {/* infra strip — the operator's conscience */}
        <div className="k-mono" style={{
          marginTop: "auto", paddingTop: 18, display: "flex", gap: 18, fontSize: 11,
          color: "var(--muted)", borderTop: "1px dashed var(--border-soft)", alignItems: "center",
        }}>
          <span><span style={{ color: "var(--ok)" }}>●</span> vast.ai · 1 instance up (RTX 4090)</span>
          <span>today $1.12 / cap $6.00</span>
          <span>nfs odin ✓</span>
          <span style={{ marginLeft: "auto" }}>all instances destroyed after use — always</span>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { DashboardBoard, DuetWave, StageTrail });
