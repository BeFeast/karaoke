// Add song (#/add, Sonereli UI/UX v1 step 2). One field for a song link with
// Paste, a live check of the link, an audio upload as the alternative, and a
// pinned primary action. After a submit Processing replaces Add in the
// history (step 3). A link to a song the catalog already has ready (#277)
// turns the action into Start singing: the server links it to the owner
// without GPU work, and the Stage opens directly.

import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { type CatalogSong, getCatalog, type JobOut } from "../api";
import { t } from "../i18n";
import { parseSongLink, youtubeId } from "../lib/songLink";
import { closeModalRoute, itemHash, replaceHash } from "../router";
import { Icon } from "./Icon";

// Extensions POST /jobs/upload accepts (mirror of the server allowlist, #173).
const UPLOAD_ACCEPT = ".mp3,.m4a,.wav,.flac,.ogg";

function errorText(err: unknown, upload: boolean): string {
  const raw = err instanceof Error ? err.message : String(err);
  if (upload && raw.startsWith("413")) return t("err.tooLarge");
  if (upload && raw.startsWith("415")) return t("err.fileType");
  if (/^(400|404|409|422)\b/.test(raw)) return t("err.source");
  return t("err.generic");
}

function useOnline(): boolean {
  const [online, setOnline] = useState(() => typeof navigator === "undefined" || navigator.onLine !== false);
  useEffect(() => {
    const update = () => setOnline(navigator.onLine !== false);
    window.addEventListener("online", update);
    window.addEventListener("offline", update);
    return () => {
      window.removeEventListener("online", update);
      window.removeEventListener("offline", update);
    };
  }, []);
  return online;
}

export function AddSong({ initialUrl, catalogEnabled, onCreate, onUpload }: {
  initialUrl: string;
  /** Catalog lookups need a Clerk session; the LAN booth skips reuse. */
  catalogEnabled: boolean;
  onCreate: (url: string) => Promise<JobOut>;
  onUpload: (file: File) => Promise<JobOut>;
}) {
  const [text, setText] = useState(initialUrl);
  const [file, setFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<CatalogSong[]>([]);
  const online = useOnline();
  const input = useRef<HTMLInputElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const link = useMemo(() => parseSongLink(text), [text]);

  useEffect(() => {
    if (!file) input.current?.focus({ preventScroll: true });
  }, [file]);

  useEffect(() => {
    if (!catalogEnabled) return;
    const controller = new AbortController();
    getCatalog("", undefined, undefined, controller.signal).then((page) => setCatalog(page.songs), () => undefined);
    return () => controller.abort();
  }, [catalogEnabled]);

  const ready = link.kind === "youtube" && !file
    ? catalog.find((song) => song.stage_token && youtubeId(song.source_url) === link.id) ?? null
    : null;
  const canSubmit = !sending && online && (file !== null || link.kind === "youtube" || link.kind === "url");

  const paste = async () => {
    try {
      const value = await navigator.clipboard.readText();
      if (value.trim()) {
        setText(value.trim());
        setError(null);
      }
    } catch {
      // Clipboard reads need permission (and HTTPS); typing still works.
    }
    input.current?.focus({ preventScroll: true });
  };

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!canSubmit) return;
    setSending(true);
    setError(null);
    try {
      if (file) {
        const job = await onUpload(file);
        replaceHash(itemHash(job.job_token));
        return;
      }
      if (link.kind !== "youtube" && link.kind !== "url") return;
      const job = await onCreate(link.url);
      // Processing replaces Add in the history; a catalog-ready song comes
      // back completed and opens straight on the Stage.
      replaceHash(itemHash(job.job_token));
    } catch (err) {
      setError(errorText(err, file !== null));
      setSending(false);
    }
  };

  const message = file ? null
    : link.kind === "youtube" ? { tone: "ok", text: t("add.valid"), icon: "check" as const }
    : link.kind === "url" ? { tone: "ok", text: t("add.validOther"), icon: "check" as const }
    : link.kind === "invalid" ? { tone: "err", text: t("add.invalid"), icon: "alert" as const }
    : null;

  return (
    <form className="sn-app sn-modal sn-add" onSubmit={(event) => void submit(event)} noValidate>
      <div className="sn-navbar">
        <button type="button" className="sn-iconbtn sn-add-close" onClick={closeModalRoute} aria-label={t("action.close")}>
          <Icon name="x" />
        </button>
      </div>
      <main className="sn-screen">
        <h1 className="sn-display sn-title">{t("add.title")}</h1>
        <p className="sn-lead sn-add-lead">{t("add.lead")}</p>

        {file ? (
          <div className="sn-filerow">
            <Icon name="note" />
            <span className="sn-grow"><span className="sn-t1" dir="auto">{file.name}</span></span>
            <button type="button" className="sn-iconbtn" aria-label={t("add.remove")} onClick={() => {
              setFile(null);
              if (picker.current) picker.current.value = "";
            }}><Icon name="x" /></button>
          </div>
        ) : (
          <div className="sn-field">
            <label className="sn-label" htmlFor="song-link">{t("add.field")}</label>
            <div className={`sn-input${link.kind === "invalid" ? " is-invalid" : ""}`}>
              <Icon name="link" />
              <input ref={input} id="song-link" name="song-link" type="url" inputMode="url" value={text}
                onChange={(event) => { setText(event.target.value); setError(null); }}
                placeholder="youtube.com/watch?v=…" autoComplete="off" autoCapitalize="none" autoCorrect="off"
                spellCheck={false} enterKeyHint="go" aria-describedby="song-link-msg"
                aria-invalid={link.kind === "invalid" || undefined} disabled={sending} />
              <button type="button" className="sn-btn sn-btn-small" onClick={() => void paste()} disabled={sending}>
                {t("add.paste")}
              </button>
            </div>
            <p id="song-link-msg" className={`sn-msg${message ? ` is-${message.tone}` : ""}`} aria-live="polite">
              {message && <><Icon name={message.icon} size={18} />{message.text}</>}
            </p>
          </div>
        )}

        {ready && (
          <div className="sn-card sn-ready" data-reuse-song={ready.id}>
            <span className="sn-cover">
              {ready.thumbnail_url ? <img src={ready.thumbnail_url} alt="" referrerPolicy="no-referrer" /> : <Icon name="note" />}
            </span>
            <span className="sn-grow">
              <span className="sn-eyebrow sn-ok">{t("add.ready")}</span>
              <bdi className="sn-t1">{ready.title}</bdi>
              <bdi className="sn-t2">{ready.artist}</bdi>
            </span>
          </div>
        )}

        {!ready && (
          <>
            <div className="sn-or">{t("add.or")}</div>
            {file ? (
              <button type="button" className="sn-btn sn-block" onClick={() => setFile(null)}>
                <Icon name="link" size={20} />{t("add.linkInstead")}
              </button>
            ) : (
              <button type="button" className="sn-btn sn-block" onClick={() => picker.current?.click()} disabled={sending}>
                <Icon name="upload" size={20} />{t("add.upload")}
              </button>
            )}
            <p className="sn-hint">{t("add.uploadHint")}</p>
            <input ref={picker} type="file" accept={UPLOAD_ACCEPT} hidden onChange={(event) => {
              const picked = event.currentTarget.files?.[0];
              if (picked) {
                setFile(picked);
                setError(null);
              }
            }} />
            <div className="sn-help">
              <Icon name="share" />
              <span className="sn-grow">
                <span className="sn-t1">{t("add.youtube")}</span>
                <span className="sn-t2 sn-wrap">{t("add.youtubeHint")}</span>
              </span>
            </div>
          </>
        )}

        {!online && <p className="sn-note is-warn" role="status"><Icon name="alert" size={20} />{t("add.offline")}</p>}
        {error && <p className="sn-note is-err" role="alert"><Icon name="alert" size={20} />{error}</p>}
      </main>
      <footer className="sn-footer">
        <button type="submit" className="sn-btn sn-btn-primary sn-block sn-add-submit" disabled={!canSubmit}
          data-reuse={ready ? "catalog" : undefined}>
          {sending ? t("add.sending") : ready ? <><Icon name="play" size={22} />{t("action.sing")}</> : t("add.submit")}
        </button>
      </footer>
    </form>
  );
}
