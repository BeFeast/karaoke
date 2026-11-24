// Feedback screen on #/feedback (Sonereli UI/UX v1 step 10): a topic in one
// tap, a message, and what gets attached, shown before sending. Song and
// player state are attached by default; a screenshot is off by default.
// Same pomoi feedback contract (#344) as the Stage's Report a problem sheet.

import { type FormEvent, useState } from "react";
import { feedbackInstallId, feedbackLog, feedbackMetadata, redactFeedbackText } from "../feedback/diagnostics";
import { postFeedback } from "../feedback/Feedback";
import { type StringKey, t } from "../i18n";
import { fmtClock } from "../lib/library";
import { useOptionalPlayerSession } from "../player/PlayerProvider";
import { settingsHash } from "../router";
import { Icon } from "./Icon";
import { Shell } from "./Shell";

const TOPICS: { key: StringKey; kind: "bug" | "fr" }[] = [
  { key: "fb.timing", kind: "bug" },
  { key: "fb.words", kind: "bug" },
  { key: "fb.vocals", kind: "bug" },
  { key: "fb.app", kind: "bug" },
  { key: "fb.idea", kind: "fr" },
];

export function FeedbackPage({ catalogEnabled, baseUrl }: { catalogEnabled: boolean; baseUrl?: string }) {
  const ctx = useOptionalPlayerSession();
  const [topic, setTopic] = useState<StringKey>("fb.app");
  const [message, setMessage] = useState("");
  const [attachState, setAttachState] = useState(true);
  const [attachShot, setAttachShot] = useState(false);
  const [phase, setPhase] = useState<"normal" | "sending" | "sent" | "error">("normal");
  const [receipt, setReceipt] = useState<number | null>(null);

  const song = ctx?.session && !ctx.session.shared ? ctx.session : null;
  const state = song
    ? `${song.title} · ${fmtClock(ctx?.player.currentTime ?? 0)} · ${t("stage.vocals").toLowerCase()} ${ctx?.vocalPct ?? 0}%`
    : t("fb.noSong");

  async function send(event: FormEvent) {
    event.preventDefault();
    if (phase === "sending") return;
    setPhase("sending");
    try {
      const form = new FormData();
      form.set("text", redactFeedbackText(`[${t(topic, undefined, "en")}] ${message}`.trim()));
      form.set("kind", TOPICS.find((item) => item.key === topic)?.kind ?? "bug");
      form.set("install_id", feedbackInstallId());
      const meta: Record<string, unknown> = { ...(await feedbackMetadata()), topic: t(topic, undefined, "en") };
      if (attachState && song && ctx) {
        meta.player = {
          title: song.title, position_s: Math.round(ctx.player.currentTime * 10) / 10,
          vocals_pct: ctx.vocalPct, playing: ctx.player.playing,
        };
      }
      form.set("meta", JSON.stringify(meta));
      form.set("log", feedbackLog());
      if (attachShot) {
        const { captureViewport, feedbackPng } = await import("../feedback/screenshot");
        const png = await feedbackPng(await captureViewport(), null);
        if (png) form.set("screenshot", png, "feedback.png");
      }
      const result = await postFeedback(form, baseUrl);
      setReceipt(result.number);
      setPhase("sent");
      setMessage("");
    } catch {
      setPhase("error");
    }
  }

  return (
    <Shell tab="settings" title={t("fb.title")} catalogEnabled={catalogEnabled}>
      <a className="sn-btn sn-btn-small sn-btn-ghost" href={settingsHash()} style={{ paddingInline: 0 }}>
        <Icon name="left" size={20} />{t("tab.settings")}
      </a>
      {phase === "sent" ? (
        <div className="sn-empty" role="status">
          <Icon name="check" size={40} className="sn-ok" />
          <h2 className="sn-empty-title">{t("fb.sent")}</h2>
          {receipt && <p className="sn-lead">{t("fb.sentLead", { n: receipt })}</p>}
          <button type="button" className="sn-btn sn-btn-small" onClick={() => setPhase("normal")}>{t("fb.another")}</button>
        </div>
      ) : (
        <form className="sn-set-section" onSubmit={(event) => void send(event)}>
          <span className="sn-label">{t("fb.topic")}</span>
          <div className="sn-chips">
            {TOPICS.map((item) => (
              <button key={item.key} type="button" className="sn-chip" aria-pressed={topic === item.key}
                onClick={() => setTopic(item.key)}>{t(item.key)}</button>
            ))}
          </div>
          <label className="sn-field">
            <span className="sn-label">{t("fb.message")}</span>
            <textarea className="sn-textarea" value={message} maxLength={4000} dir="auto"
              placeholder={t("fb.placeholder")} onChange={(event) => setMessage(event.target.value)} />
          </label>
          <label className="sn-attach">
            <span className="sn-grow">{t("fb.attachState")}<span className="sn-attach-sub"><bdi>{state}</bdi></span></span>
            <input type="checkbox" className="sn-switch" checked={attachState} onChange={(e) => setAttachState(e.target.checked)} />
          </label>
          <label className="sn-attach">
            <span className="sn-grow">{t("fb.attachShot")}</span>
            <input type="checkbox" className="sn-switch" checked={attachShot} onChange={(e) => setAttachShot(e.target.checked)} />
          </label>
          {phase === "error" && <p className="sn-msg is-err" role="alert">{t("fb.error")}</p>}
          <button type="submit" className="sn-btn sn-btn-primary sn-block" disabled={phase === "sending"}>
            {phase === "sending" ? t("fb.sending") : t("fb.send")}
          </button>
        </form>
      )}
    </Shell>
  );
}
