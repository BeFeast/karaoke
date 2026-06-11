// KARAOKE prototype — core: song data, playback engine, shared widgets.
// Reuses MWipe/MBulbs/MarqueeMark/MDuetWave from sections/m-brand.jsx (window).

// Original demo song — written for this prototype (no real-song lyrics).
const SONG = {
  id: "late-shift",
  title: "Nearly Right",
  artist: "The Late Shift",
  url: "https://youtu.be/dQw4w9WgXcQ",
  duration: 96,
  cost: "$0.31",
  receipt: "8814042",
  lines: [
    { t: 6,  d: 4.5, text: "We took the long way down to Friday night" },
    { t: 11, d: 4.5, text: "Loaded up a song we barely know" },
    { t: 16, d: 4.5, text: "The kitchen is a stadium tonight" },
    { t: 21, d: 4.5, text: "And every neighbor is the front row" },
    { t: 28, d: 4.5, text: "Turn the singer down, I'll take it from here" },
    { t: 33, d: 4.5, text: "Two thousand watts of borrowed light" },
    { t: 38, d: 4.5, text: "If I forget the words, nobody will care" },
    { t: 43, d: 4.0, text: "We'll get it nearly right" },
    { t: 56, d: 5.0, text: "Give me one more chorus before the morning comes" },
    { t: 62, d: 4.5, text: "Keep the amber burning low" },
    { t: 67, d: 4.5, text: "We are out of tune and out of time" },
    { t: 72, d: 4.0, text: "But never out of show" },
    { t: 80, d: 6.0, text: "La la la, la la la — everybody now" },
    { t: 88, d: 5.0, text: "We'll get it nearly right" },
  ],
};

function fmtTime(s) {
  s = Math.max(0, Math.floor(s));
  return Math.floor(s / 60) + ":" + String(s % 60).padStart(2, "0");
}

// playback clock — simulated; position survives refresh
function usePlayback(duration) {
  const [pos, setPosRaw] = React.useState(() => {
    const v = parseFloat(localStorage.getItem("kproto-pos"));
    return isFinite(v) && v >= 0 && v < duration ? v : 0;
  });
  const [playing, setPlaying] = React.useState(false);
  const [loop, setLoop] = React.useState(null); // null | {a} | {a,b}

  const setPos = React.useCallback((p) => {
    const v = Math.max(0, Math.min(duration, typeof p === "number" ? p : 0));
    localStorage.setItem("kproto-pos", String(v));
    setPosRaw(v);
  }, [duration]);

  React.useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setPosRaw((p) => {
        let n = p + 0.1;
        if (loop && loop.b != null && n > loop.b) n = loop.a;
        if (n >= duration) { n = 0; setPlaying(false); }
        localStorage.setItem("kproto-pos", String(n));
        return n;
      });
    }, 100);
    return () => clearInterval(id);
  }, [playing, loop, duration]);

  const cycleLoop = React.useCallback(() => {
    setLoop((l) => {
      if (!l) return { a: posRef.current };
      if (l.b == null) return posRef.current > l.a + 1 ? { a: l.a, b: posRef.current } : null;
      return null;
    });
  }, []);
  const posRef = React.useRef(pos);
  posRef.current = pos;

  return { pos, setPos, playing, setPlaying, loop, cycleLoop };
}

// which lyric line is current, and its wipe %
function lyricState(lines, pos) {
  let idx = -1;
  for (let i = 0; i < lines.length; i++) if (pos >= lines[i].t) idx = i;
  const cur = idx >= 0 ? lines[idx] : null;
  const sung = cur ? Math.min(1, (pos - cur.t) / cur.d) : 0;
  const next = lines[idx + 1] || null;
  const gap = next ? next.t - pos : 0; // seconds until next line starts
  const inGap = cur ? pos > cur.t + cur.d : true;
  return { idx, cur, sung: sung * 100, next, prev: lines[idx - 1] || null, gap, inGap };
}

// seekable duet waveform
function pBars(seed, n) {
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

function ProtoWave({ pos, duration, onSeek, h = 88, voxLevel = 100, instLevel = 100 }) {
  const ref = React.useRef(null);
  const w = 600, n = 100;
  const vox = React.useMemo(() => pBars(5, n), []);
  const inst = React.useMemo(() => pBars(19, n), []);
  const played = pos / duration;
  const bw = w / n, half = h / 2;
  const seek = (e) => {
    const r = ref.current.getBoundingClientRect();
    onSeek(((e.clientX - r.left) / r.width) * duration);
  };
  return (
    <svg ref={ref} width="100%" viewBox={`0 0 ${w} ${h}`} style={{ display: "block", cursor: "pointer" }}
      onClick={seek} aria-label="Waveform — click to seek">
      {vox.map((v, i) => (
        <rect key={"v" + i} x={i * bw + 0.8} y={half - 2 - v * (half - 6) * (0.25 + 0.75 * voxLevel / 100)}
          width={bw - 1.6} height={Math.max(1, v * (half - 6) * (0.25 + 0.75 * voxLevel / 100))} rx="1"
          fill="var(--vox)" opacity={i / n <= played ? 0.95 : 0.26}></rect>
      ))}
      {inst.map((v, i) => (
        <rect key={"i" + i} x={i * bw + 0.8} y={half + 2}
          width={bw - 1.6} height={Math.max(1, v * (half - 6) * (0.25 + 0.75 * instLevel / 100))} rx="1"
          fill="var(--inst)" opacity={i / n <= played ? 0.85 : 0.2}></rect>
      ))}
      <line x1={w * played} x2={w * played} y1="0" y2={h} stroke="var(--fg)" strokeWidth="1.5"></line>
    </svg>
  );
}

// draggable vertical fader
function ProtoFader({ label, color, value, onChange, ducked }) {
  const trackRef = React.useRef(null);
  const drag = (e) => {
    e.preventDefault();
    const move = (ev) => {
      const r = trackRef.current.getBoundingClientRect();
      const pct = 100 - ((ev.clientY - r.top) / r.height) * 100;
      onChange(Math.round(Math.max(0, Math.min(100, pct))));
    };
    move(e);
    const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const shown = ducked ? Math.min(value, 8) : value;
  return (
    <div style={{ display: "grid", justifyItems: "center", gap: 6, userSelect: "none" }}>
      <span className="m-mono" style={{ fontSize: 10, letterSpacing: "0.1em", color }}>{label}</span>
      <div ref={trackRef} onPointerDown={drag} style={{ position: "relative", width: 34, height: 132, display: "flex", justifyContent: "center", cursor: "ns-resize", touchAction: "none" }}>
        <div style={{ width: 4, borderRadius: 2, background: "var(--border)", position: "relative" }}>
          <div style={{ position: "absolute", left: 0, right: 0, bottom: 0, height: shown + "%", background: color, borderRadius: 2, opacity: ducked ? 0.45 : 0.85, transition: "height .12s" }}></div>
          <div style={{
            position: "absolute", left: "50%", bottom: shown + "%", transform: "translate(-50%, 50%)",
            width: 28, height: 14, borderRadius: 4, background: "var(--fg)",
            border: "1px solid var(--bg)", boxShadow: "0 2px 5px rgba(0,0,0,0.5)", transition: "bottom .12s",
          }}></div>
        </div>
      </div>
      <span className="m-mono" style={{ fontSize: 10, color: ducked ? color : "var(--muted)" }}>{ducked ? "duck" : shown + "%"}</span>
    </div>
  );
}

Object.assign(window, { SONG, fmtTime, usePlayback, lyricState, ProtoWave, ProtoFader });
