// KARAOKE — Settings: stage passes + footer on #/settings (Marquee port, #157).
// Literal port of design/claude-export/proto/settings.jsx (SettingsScreen
// :78-146, SettingsSection :6-13, PassRow :15-35, footer :137-142); the topbar
// is the booth shell's MarqueeTopBar (same composition as settings.jsx:82-88).
// The DOM tree and inline styles are the design's; the adaptations wire real
// data only: pass rows come from GET /tokens, "Mint pass" hits POST /tokens
// and the reveal banner shows the real one-time mint response (the export's
// fake ktx_ random generator, settings.jsx:70-77, is gone), Revoke is
// DELETE /tokens/{id} behind ConfirmDialog, and the footer version comes from
// GET /health. The export's design-fiction sections — "pipeline defaults"
// (:119-128) and "sharing" (:130-135), plus their Select/SettingRow helpers —
// have no backing product and are deleted, not stubbed.

import { type ReactNode, useCallback, useEffect, useState } from "react";
import {
  type ExtensionTokenMinted,
  type ExtensionTokenOut,
  getHealth,
  listTokens,
  mintToken,
  revokeToken,
} from "../api";
import { formatRelativeTime } from "../lib/jobListUtils";
import { usePhoneLayout } from "../lib/layout";
import { goDashboard } from "../router";
import { MarqueeTopBar } from "./Booth";
import { ConfirmDialog, type ConfirmState } from "./ConfirmDialog";
import { Toast } from "./Toast";

const K_REPO = "https://github.com/BeFeast/karaoke";

// The server 403s a mint from an actor that may not mint (trusted-LAN,
// extension-token callers) — translate that into a hint instead of dumping
// the raw "403 Forbidden: {…}" string on the user.
const MINT_FORBIDDEN_HINT =
  "Minting requires Clerk sign-in or the machine bearer. " +
  "Trusted-LAN callers can list and revoke passes but not mint them.";

function SettingsSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ marginTop: 26 }}>
      <div className="m-mono" style={{ fontSize: 10.5, letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--muted)", marginBottom: 10 }}>{title}</div>
      {children}
    </div>
  );
}

function PassRow({ token, onRevoke }: { token: ExtensionTokenOut; onRevoke: (token: ExtensionTokenOut) => void }) {
  const created = formatRelativeTime(token.created_at);
  const used = formatRelativeTime(token.last_used_at);
  return (
    <div style={{ display: "flex", gap: 14, alignItems: "baseline", padding: "12px 2px", borderTop: "1px solid var(--border-soft)" }}>
      <span className="m-mono" style={{ fontSize: 11.5, color: "var(--muted)", width: 22 }}>#{token.id}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: 13.5, fontWeight: 600 }}>{token.label?.trim() || "(no label)"}</span>
          {token.disabled && <span className="m-chip err" style={{ fontSize: 10 }}>revoked</span>}
        </div>
        <div className="m-mono" style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 3, display: "flex", gap: 14, flexWrap: "wrap" }}>
          <span title={token.created_at}>created {created ?? "—"}</span>
          <span title={token.last_used_at ?? undefined}>last used {used ?? "never"}</span>
          <span title={token.owner_subject} style={{ maxWidth: "100%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{token.owner_subject}</span>
        </div>
      </div>
      {!token.disabled && (
        <button className="m-btn sm ghost" type="button" onClick={() => onRevoke(token)} style={{ color: "var(--err)" }}>Revoke</button>
      )}
    </div>
  );
}

// Loading placeholder shaped like the pass rows (booth skeleton idiom).
function PassesSkeleton() {
  return (
    <div style={{ marginTop: 6 }} aria-hidden>
      {[0, 1].map((i) => (
        <div key={i} style={{ display: "flex", gap: 14, alignItems: "baseline", padding: "12px 2px", borderTop: "1px solid var(--border-soft)" }}>
          <div style={{ width: 22, height: 9, borderRadius: "var(--radius-sm)", background: "var(--bg-soft)" }}></div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ height: 10, width: "40%", borderRadius: "var(--radius-sm)", background: "var(--bg-soft)" }}></div>
            <div style={{ height: 8, width: "65%", marginTop: 7, borderRadius: "var(--radius-sm)", background: "var(--bg-soft)" }}></div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function SettingsPage({ authControl }: { authControl?: ReactNode }) {
  // Phone (#187): responsive adaptation of the shipped structure — the mint
  // row stacks (input full width, button below) and the container's side
  // padding tightens to 16. Desktop branch renders identical DOM (undefined
  // style values are omitted by React).
  const phone = usePhoneLayout();
  const [tokens, setTokens] = useState<ExtensionTokenOut[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [mintName, setMintName] = useState("");
  const [minting, setMinting] = useState(false);
  const [mintError, setMintError] = useState<string | null>(null);
  const [fresh, setFresh] = useState<ExtensionTokenMinted | null>(null);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setTokens(await listTokens());
      setListError(null);
    } catch (err) {
      setListError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Deploy-truth version for the footer (GET /health) — never hardcoded.
  useEffect(() => {
    getHealth()
      .then((h) => setVersion(h.version))
      .catch(() => setVersion(null));
  }, []);

  const mint = useCallback(async () => {
    const name = mintName.trim();
    if (!name || minting) return;
    setMinting(true);
    setMintError(null);
    try {
      setFresh(await mintToken(name));
      setMintName("");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setMintError(/^403\b/.test(msg) ? MINT_FORBIDDEN_HINT : msg);
    } finally {
      setMinting(false);
      await refresh();
    }
  }, [mintName, minting, refresh]);

  // One-time reveal: the banner's single button copies, then dismisses — after
  // this render the raw value is gone for good (only a hash is stored).
  const copyFresh = useCallback(async () => {
    if (!fresh) return;
    try {
      await navigator.clipboard.writeText(fresh.token);
      setToast("Pass copied");
    } catch {
      // Clipboard API blocked (http LAN origin / permissions) — fall back.
      window.prompt("Copy this pass:", fresh.token);
    }
    setFresh(null);
  }, [fresh]);

  const onRevoke = useCallback(
    (token: ExtensionTokenOut) => {
      setConfirmState({
        title: "Revoke pass",
        message: `Revoke pass #${token.id} (${token.label?.trim() || "no label"})? Anything still using it will stop working.`,
        confirmLabel: "Revoke",
        danger: true,
        onConfirm: () => {
          setActionError(null);
          void (async () => {
            try {
              await revokeToken(token.id);
            } catch (err) {
              setActionError(err instanceof Error ? err.message : String(err));
            } finally {
              await refresh();
            }
          })();
        },
      });
    },
    [refresh],
  );

  const active = tokens.filter((t) => !t.disabled).length;

  return (
    <div style={{ height: "100%", overflow: "auto" }}>
      <div className="m-booth" style={{ minHeight: "100%", display: "flex", flexDirection: "column" }}>
        <MarqueeTopBar authControl={authControl} />

        <div style={{ flex: 1, padding: phone ? "24px 16px 20px" : "24px 24px 20px", maxWidth: 720, width: "100%", margin: "0 auto", display: "flex", flexDirection: "column" }}>
          <button className="m-btn sm ghost" type="button" onClick={goDashboard} style={{ alignSelf: "flex-start", marginLeft: -9 }}>← back to the booth</button>
          <h1 style={{ margin: "10px 0 2px", fontFamily: "var(--font-display)", fontWeight: 650, fontSize: 26, letterSpacing: "-0.015em" }}>Settings</h1>
          <div className="m-mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>{active} active stage pass{active === 1 ? "" : "es"}</div>

          <SettingsSection title="stage passes — extension tokens">
            <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-soft)", borderRadius: "var(--radius-lg)", padding: 14 }}>
              <div style={{ display: "flex", gap: 10, flexDirection: phone ? "column" : undefined }}>
                <input value={mintName} onChange={(e) => setMintName(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") void mint(); }}
                  placeholder="Name this pass — e.g. Chrome on the desk machine"
                  maxLength={255} disabled={minting} aria-label="Pass name"
                  style={{ flex: phone ? undefined : 1, minWidth: 0, padding: "9px 12px", border: "1px solid var(--border)", borderRadius: 7, background: "var(--bg)", color: "var(--fg)", fontSize: 13, fontFamily: "var(--font-ui)", outline: "none" }}></input>
                <button className="m-btn primary" type="button" onClick={() => void mint()} disabled={minting || !mintName.trim()} style={{ justifyContent: phone ? "center" : undefined }}>{minting ? "Minting…" : "Mint pass"}</button>
              </div>
              {fresh && (
                <div className="m-mono" role="status" style={{ marginTop: 10, padding: "8px 11px", borderRadius: 7, background: "var(--accent-soft)", color: "var(--accent)", fontSize: 12, display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <span style={{ fontWeight: 700, minWidth: 0, overflowWrap: "anywhere" }}>{fresh.token}</span>
                  <span style={{ color: "var(--fg-soft)" }}>— copy it now; it's shown exactly once</span>
                  <button className="m-btn sm" type="button" style={{ marginLeft: "auto" }} onClick={() => void copyFresh()}>⧉ Copy</button>
                </div>
              )}
              {mintError && (
                <div className="m-mono" style={{ marginTop: 8, fontSize: 11.5, color: "var(--err)", overflowWrap: "anywhere" }}>{mintError}</div>
              )}
              <div className="m-mono" style={{ marginTop: 9, fontSize: 10.5, color: "var(--muted)", lineHeight: 1.5 }}>
                ktx_ passes let the Chrome extension submit jobs as you. Raw value appears once, right after minting.
              </div>
            </div>
            {listError && (
              <div className="m-mono" style={{ marginTop: 10, fontSize: 11.5, color: "var(--err)", overflowWrap: "anywhere" }}>
                Couldn’t load passes: {listError}{" "}
                <button className="m-btn sm ghost" type="button" onClick={() => void refresh()}>↻ Retry</button>
              </div>
            )}
            {actionError && (
              <div className="m-mono" style={{ marginTop: 10, fontSize: 11.5, color: "var(--err)", overflowWrap: "anywhere" }}>Revoke failed: {actionError}</div>
            )}
            {!loaded ? (
              <PassesSkeleton />
            ) : tokens.length === 0 && !listError ? (
              <div className="m-mono" style={{ marginTop: 6, padding: "16px 2px", borderTop: "1px solid var(--border-soft)", fontSize: 11.5, color: "var(--muted)" }}>
                No passes yet — mint one above and paste it into the extension's options page.
              </div>
            ) : (
              <div style={{ marginTop: 6 }}>
                {tokens.map((t) => <PassRow key={t.id} token={t} onRevoke={onRevoke}></PassRow>)}
              </div>
            )}
          </SettingsSection>

          <div className="m-mono" style={{ marginTop: "auto", paddingTop: 26, display: "flex", gap: 16, fontSize: 11, color: "var(--muted)", borderTop: "1px dashed var(--border-soft)", alignItems: "center", flexWrap: "wrap" }}>
            {version && <span>karaoke v{version}</span>}
            <a href={K_REPO} target="_blank" rel="noopener" style={{ color: "var(--info)", textDecoration: "none" }}>github.com/BeFeast/karaoke ↗</a>
            <span>open source · MIT</span>
            <span style={{ marginLeft: "auto" }}>self-hosted · your music never leaves your boxes</span>
          </div>
        </div>

        <ConfirmDialog state={confirmState} onClose={() => setConfirmState(null)} />
        <Toast message={toast} onDone={() => setToast(null)} />
      </div>
    </div>
  );
}
