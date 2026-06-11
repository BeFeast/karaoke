// KARAOKE prototype — the Booth: live dashboard with simulated jobs.

const STAGES = ["fetch", "gpu", "split", "lyrics"];
const STAGE_DETAIL = {
  fetch: "yt-dlp · devbox (residential ip)",
  gpu: "vast.ai #8814042 · RTX 4090 · $0.39/hr",
  split: "demucs htdemucs_ft · two stems",
  lyrics: "faster-whisper large-v3",
};

function useFakeJobs() {
  const [jobs, setJobs] = React.useState(() => ([
    { id: 1, title: "Nearly Right — The Late Shift", state: "ready", meta: "1:36 · today 21:03 · $0.31", receipt: "8814042", seed: 11, url: "https://youtu.be/dQw4w9WgXcQ" },
    { id: 2, title: "Зимний сон — Алсу", state: "running", stageIdx: 1, stagePct: 40, cost: 0.06 },
    { id: 3, title: "Golden Hour Static — Cassette Royale", state: "ready", meta: "3:39 · today 20:12 · $0.29", receipt: "8813871", seed: 4, url: "https://youtu.be/2X3wLkzCNGk" },
    { id: 4, title: "Harbour Lights — Petrov & June", state: "failed", note: "yt-dlp: sign-in required — resubmit from a signed-in browser" },
  ]));

  React.useEffect(() => {
    const id = setInterval(() => {
      setJobs((js) => js.map((j) => {
        if (j.state !== "running") return j;
        let { stageIdx, stagePct, cost } = j;
        stagePct += stageIdx === 0 ? 9 : stageIdx === 1 ? 5 : 3;
        cost = +(cost + 0.004).toFixed(3);
        if (stagePct >= 100) { stageIdx += 1; stagePct = 0; }
        if (stageIdx >= STAGES.length) {
          return { ...j, state: "ready", meta: "3:12 · just now · $" + cost.toFixed(2), receipt: "8814119", seed: 7 };
        }
        return { ...j, stageIdx, stagePct, cost };
      }));
    }, 700);
    return () => clearInterval(id);
  }, []);

  const submit = (title) => setJobs((js) => [
    { id: Date.now(), title, state: "running", stageIdx: 0, stagePct: 5, cost: 0 },
    ...js,
  ]);
  return [jobs, submit];
}

function PStageRow({ name, idx, stageIdx, stagePct }) {
  const state = idx < stageIdx ? "done" : idx === stageIdx ? "run" : "todo";
  return (
    <div style={{ display: "grid", gridTemplateColumns: "58px 1fr auto", gap: 12, alignItems: "baseline", padding: "6px 0", borderTop: "1px solid var(--border-soft)" }}>
      <span className="m-mono" style={{ fontSize: 11.5, fontWeight: 700, color: state === "todo" ? "var(--muted)" : "var(--fg)" }}>{name}</span>
      <span className="m-mono" style={{ fontSize: 11.5, color: "var(--muted)", minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
        {state === "run" ? <MWipe text={STAGE_DETAIL[name]} pct={stagePct} size={11.5} /> : STAGE_DETAIL[name]}
      </span>
      <span className="m-mono" style={{ fontSize: 11.5, color: state === "done" ? "var(--ok)" : state === "run" ? "var(--accent)" : "var(--muted)" }}>
        {state === "done" ? "✓" : state === "run" ? Math.round(stagePct) + "%" : "—"}
      </span>
    </div>
  );
}

function PRunningCard({ job }) {
  const overall = ((job.stageIdx + job.stagePct / 100) / STAGES.length) * 100;
  return (
    <div style={{ background: "var(--bg-card)", border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "13px 16px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className="m-chip run"><span className="m-dot"></span>{STAGES[job.stageIdx]}</span>
        <span style={{ fontSize: 14, fontWeight: 650, flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{job.title}</span>
        <span className="m-mono" style={{ fontSize: 12.5, color: "var(--accent)", fontWeight: 600 }}>${job.cost.toFixed(2)}</span>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>live</span>
      </div>
      <div style={{ margin: "10px 0 0" }}>
        {STAGES.map((s, i) => <PStageRow key={s} name={s} idx={i} stageIdx={job.stageIdx} stagePct={job.stagePct}></PStageRow>)}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--border-soft)" }}>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>cap $0.80</span>
        <div className="m-wipebar" style={{ "--wipe": overall + "%", flex: 1, maxWidth: 220 }}><i></i></div>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>{Math.round(overall)}%</span>
        <span style={{ flex: 1 }}></span>
        <span className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)" }}>instance dies when the song's done</span>
      </div>
    </div>
  );
}

function BoothScreen({ onSing, infraStrip = true, onSignOut, onSettings, vars = {} }) {
  const [jobs, submit] = useFakeJobs();
  const [url, setUrl] = React.useState("");
  const doSubmit = () => {
    submit(url.trim() ? decodeURIComponent(url.trim().split("/").pop()).slice(0, 48) : "Pasted link — resolving title…");
    setUrl("");
  };
  return (
    <div className="m-booth" style={{ minHeight: "100%", display: "flex", flexDirection: "column", ...vars }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "0 24px", height: 56, borderBottom: "1px solid var(--border)", flexShrink: 0 }}>
        <MicMark size={24} />
        <span style={{ fontFamily: "var(--font-display)", fontWeight: 650, fontSize: 17, letterSpacing: "-0.01em" }}>Karaoke</span>
        <span style={{ flex: 1 }}></span>
        <span className="m-chip" style={{ textTransform: "uppercase", letterSpacing: "0.05em", fontSize: 10 }}>trusted lan</span>
        <UserAvatar onSignOut={onSignOut} onSettings={onSettings} />
      </div>

      <div style={{ flex: 1, padding: "24px 24px 16px", maxWidth: 780, width: "100%", margin: "0 auto", display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)", padding: 16 }}>
          <div style={{ display: "flex", gap: 10 }}>
            <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="Paste a YouTube link…"
              onKeyDown={(e) => e.key === "Enter" && doSubmit()}
              style={{ flex: 1, padding: "10px 13px", border: "1px solid var(--border)", borderRadius: "var(--radius)", background: "var(--bg)", color: "var(--fg)", fontSize: 14, fontFamily: "var(--font-mono)", outline: "none" }}></input>
            <button className="m-btn primary" type="button" onClick={doSubmit} style={{ fontSize: 14, padding: "8px 18px" }}>Put it on stage</button>
          </div>
          <div className="m-mono" style={{ display: "flex", gap: 16, marginTop: 10, fontSize: 11, color: "var(--muted)", alignItems: "center" }}>
            <span>one link →</span>
            <span className="m-stem vox">vocals.mp3</span>
            <span className="m-stem inst">karaoke.mp3</span>
            <span style={{ color: "var(--fg-soft)" }}>≡ lyrics.lrc</span>
            <span style={{ marginLeft: "auto" }}>est $0.25–0.45 · ~4 min</span>
          </div>
        </div>

        <div className="m-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)", margin: "20px 0 9px" }}>tonight</div>

        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {jobs.map((j) => j.state === "running" ? (
            <PRunningCard key={j.id} job={j}></PRunningCard>
          ) : (
            <div key={j.id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "11px 16px", background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)" }}>
              {j.state === "ready" && <span className="m-chip ok"><span className="m-dot"></span>ready</span>}
              {j.state === "failed" && <span className="m-chip err"><span className="m-dot"></span>failed</span>}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 13.5, fontWeight: 600, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{j.title}</div>
                <div className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 2, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {j.meta}
                  {j.receipt && <span> · receipt: #{j.receipt} destroyed ✓</span>}
                  {j.note && <span style={{ color: "var(--err)" }}>{j.note}</span>}
                </div>
              </div>
              {j.url && <a href={j.url} target="_blank" rel="noopener" title="Original video" className="m-btn sm ghost" style={{ textDecoration: "none" }}>↗</a>}
              {j.state === "ready" && <MDuetWave seed={j.seed} w={100} h={22} />}
              {j.state === "ready" && <button className="m-btn sm primary" type="button" onClick={() => onSing(j)}>▸ Sing</button>}
              {j.state === "failed" && <button className="m-btn sm" type="button">↻ Retry</button>}
            </div>
          ))}
        </div>

        {infraStrip && <div className="m-mono" style={{ marginTop: "auto", paddingTop: 16, display: "flex", gap: 16, fontSize: 11, color: "var(--muted)", borderTop: "1px dashed var(--border-soft)", alignItems: "center" }}>
          <span><span style={{ color: "var(--ok)" }}>●</span> vast.ai · {jobs.some((j) => j.state === "running") ? "1 instance up" : "0 instances up"}</span>
          <span>today $1.12 / cap $6.00</span>
          <span>nfs odin ✓ · ws live ✓</span>
          <span style={{ marginLeft: "auto" }}>0 orphaned instances · ever</span>
        </div>}
      </div>
    </div>
  );
}

Object.assign(window, { BoothScreen });
