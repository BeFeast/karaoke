import { type ReactNode, useCallback, useEffect, useState } from "react";
import {
  type ExtensionTokenMinted,
  type ExtensionTokenOut,
  listTokens,
  mintToken,
  revokeToken,
} from "../api";
import { formatRelativeTime } from "../lib/jobListUtils";
import { goDashboard } from "../router";
import { MarqueeTopBar } from "./Booth";
import { ConfirmDialog, type ConfirmState } from "./ConfirmDialog";
import { Toast } from "./Toast";

const DEFAULT_LABEL = "Chrome extension";

// The server 403s a mint from an actor that may not mint (trusted-LAN,
// extension-token callers) — translate that into a hint instead of dumping
// the raw "403 Forbidden: {…}" string on the user.
const MINT_FORBIDDEN_HINT =
  "Minting requires Clerk sign-in or the machine bearer. " +
  "Trusted-LAN callers can list and revoke tokens but not mint them.";

// Single-column chrome: topbar + centered pane with a "Back" affordance, no
// dashboard sidebar. Booth rooms are always light — theming is scoped to the
// stage room (#154), so no theme hook is needed here.
function SettingsShell({ authControl, children }: { authControl?: ReactNode; children: ReactNode }) {
  return (
    <div className="app app-item">
      <MarqueeTopBar authControl={authControl} />
      <main className="main">
        <div className="pane pane-narrow">
          <button type="button" className="link-btn back-link" onClick={goDashboard}>
            ← Back to jobs
          </button>
          {children}
        </div>
      </main>
    </div>
  );
}

function TokensSkeleton() {
  return (
    <div className="tokens" aria-hidden>
      {[0, 1].map((i) => (
        <div className="skel-job" key={i}>
          <span className="skel skel-line s" />
          <div>
            <span className="skel skel-line m" />
            <span className="skel skel-line l" />
          </div>
        </div>
      ))}
    </div>
  );
}

function TokenRow({
  token,
  onRevoke,
}: {
  token: ExtensionTokenOut;
  onRevoke: (token: ExtensionTokenOut) => void;
}) {
  const created = formatRelativeTime(token.created_at);
  const lastUsed = formatRelativeTime(token.last_used_at);
  return (
    <div className="token-row">
      <div className="token-num mono">#{token.id}</div>
      <div className="token-body">
        <div className="token-top">
          <span className="token-label">{token.label?.trim() || "(no label)"}</span>
          {token.disabled && <span className="chip err">revoked</span>}
        </div>
        <div className="token-meta">
          {created && <span title={token.created_at}>created {created}</span>}
          <span title={token.last_used_at ?? undefined}>
            {lastUsed ? `last used ${lastUsed}` : "never used"}
          </span>
          <span className="token-owner" title={token.owner_subject}>
            {token.owner_subject}
          </span>
        </div>
      </div>
      {!token.disabled && (
        <button
          type="button"
          className="link-btn danger"
          onClick={() => onRevoke(token)}
          title="Revoke this token"
        >
          Revoke
        </button>
      )}
    </div>
  );
}

export function SettingsPage({ authControl }: { authControl?: ReactNode }) {
  const [tokens, setTokens] = useState<ExtensionTokenOut[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [label, setLabel] = useState(DEFAULT_LABEL);
  const [minting, setMinting] = useState(false);
  const [mintError, setMintError] = useState<string | null>(null);
  const [minted, setMinted] = useState<ExtensionTokenMinted | null>(null);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);
  const [toast, setToast] = useState<string | null>(null);

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

  const onMint = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (minting) return;
      setMinting(true);
      setMintError(null);
      try {
        setMinted(await mintToken(label));
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        setMintError(/^403\b/.test(msg) ? MINT_FORBIDDEN_HINT : msg);
      } finally {
        setMinting(false);
        await refresh();
      }
    },
    [label, minting, refresh],
  );

  const onCopyMinted = useCallback(async () => {
    if (!minted) return;
    try {
      await navigator.clipboard.writeText(minted.token);
      setToast("Token copied");
    } catch {
      // Clipboard API blocked (insecure context / permissions) — fall back.
      window.prompt("Copy this token:", minted.token);
    }
  }, [minted]);

  const onRevoke = useCallback(
    (token: ExtensionTokenOut) => {
      setConfirmState({
        title: "Revoke token",
        message: `Revoke token #${token.id} (${token.label?.trim() || "no label"})? Anything still using it will stop working.`,
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

  return (
    <SettingsShell authControl={authControl}>
      <div className="pane-header">
        <div>
          <h1 className="pane-h1">Settings</h1>
          <p className="pane-sub">
            {tokens.length} {tokens.length === 1 ? "token" : "tokens"}
          </p>
        </div>
      </div>

      <section className="tokens-panel">
        <div className="sec-label">extension tokens</div>

        <form className="submit-card" onSubmit={(e) => void onMint(e)}>
          <div className="submit-row">
            <input
              type="text"
              className="field"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder={DEFAULT_LABEL}
              maxLength={255}
              aria-label="Token label"
              disabled={minting}
            />
            <button type="submit" className="btn primary" disabled={minting}>
              {minting && <span className="spinner" aria-hidden />}
              {minting ? "Minting…" : "Mint token"}
            </button>
          </div>
          <p className="form-note">
            ktx_ tokens let the Chrome extension submit jobs as you. The raw value is shown
            exactly once, right after minting.
          </p>
          {mintError && <div className="form-error">Mint failed: {mintError}</div>}
        </form>

        {minted && (
          <div className="token-reveal" role="status">
            <div className="token-reveal-head">
              <span className="chip ok">new token</span>
              <span className="token-reveal-label">{minted.label}</span>
            </div>
            <code className="token-value">{minted.token}</code>
            <p className="token-reveal-note">
              Save it now — it will not be shown again. Only a hash is stored on the server.
            </p>
            <div className="token-reveal-actions">
              <button type="button" className="btn primary sm" onClick={() => void onCopyMinted()}>
                ⧉ Copy token
              </button>
              <button type="button" className="btn sm" onClick={() => setMinted(null)}>
                Done
              </button>
            </div>
          </div>
        )}

        {listError && (
          <div className="form-error" style={{ marginBottom: "16px" }}>
            Couldn’t load tokens: {listError}{" "}
            <button type="button" className="link-btn" onClick={() => void refresh()}>
              ↻ Retry
            </button>
          </div>
        )}
        {actionError && (
          <div className="form-error" style={{ marginBottom: "16px" }}>
            Revoke failed: {actionError}
          </div>
        )}

        {!loaded ? (
          <TokensSkeleton />
        ) : tokens.length === 0 && !listError ? (
          <div className="empty">
            <div className="empty-title">No tokens yet</div>
            <div>Mint one above and paste it into the extension’s options page.</div>
          </div>
        ) : (
          <div className="tokens">
            {tokens.map((t) => (
              <TokenRow key={t.id} token={t} onRevoke={onRevoke} />
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog state={confirmState} onClose={() => setConfirmState(null)} />
      <Toast message={toast} onDone={() => setToast(null)} />
    </SettingsShell>
  );
}
