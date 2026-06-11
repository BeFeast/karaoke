import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import {
  ClerkProvider,
  SignedIn,
  SignedOut,
  SignInButton,
  useAuth,
  UserButton,
} from "@clerk/clerk-react";
import { App } from "./App";
import { getConfig, type RuntimeConfig, setTokenGetter } from "./api";
import { ItemPage } from "./components/ItemPage";
import { SettingsPage } from "./components/SettingsPage";
import { MicMark } from "./components/TopBar";
import { useRoute } from "./router";
import "./styles.css";

// Routes between the dashboard (App), the item page (/app/#/job/:token) and
// the settings page (/app/#/settings). All are rendered inside the auth
// context so owner-scoped reads carry the Clerk bearer when signed in; on the
// LAN they work without auth too.
function Routed({ config, authControl }: { config: RuntimeConfig; authControl?: React.ReactNode }) {
  const route = useRoute();
  if (route.name === "item") {
    return <ItemPage token={route.token} authControl={authControl} />;
  }
  if (route.name === "settings") {
    return <SettingsPage authControl={authControl} />;
  }
  return <App config={config} authControl={authControl} />;
}

function Boot({ children }: { children: React.ReactNode }) {
  return (
    <div className="center">
      <span className="brand-mark">
        <MicMark />
      </span>
      {children}
    </div>
  );
}

// Bridges Clerk's session token into the API client whenever auth state
// changes, then clears it on sign-out / unmount.
function ClerkTokenBridge() {
  const { getToken, isSignedIn } = useAuth();
  useEffect(() => {
    if (isSignedIn) {
      setTokenGetter(() => getToken());
    } else {
      setTokenGetter(null);
    }
    return () => setTokenGetter(null);
  }, [getToken, isSignedIn]);
  return null;
}

function ClerkShell({ config }: { config: RuntimeConfig }) {
  return (
    <ClerkProvider publishableKey={config.clerk_publishable_key}>
      <ClerkTokenBridge />
      <SignedOut>
        <Boot>
          <h1>Karaoke</h1>
          <p>Sign in to submit and track jobs.</p>
          <SignInButton mode="modal">
            <button type="button" className="btn primary">
              Sign in
            </button>
          </SignInButton>
        </Boot>
      </SignedOut>
      <SignedIn>
        <Routed config={config} authControl={<UserButton afterSignOutUrl="/app/" />} />
      </SignedIn>
    </ClerkProvider>
  );
}

function LanShell({ config }: { config: RuntimeConfig }) {
  // No Clerk: the API's trusted-LAN bypass authorises us. Send no auth header.
  setTokenGetter(null);
  return (
    <Routed
      config={config}
      authControl={
        <span className="chip lan">
          <span className="dot" aria-hidden />
          LAN mode
        </span>
      }
    />
  );
}

async function bootstrap() {
  const root = createRoot(document.getElementById("root") as HTMLElement);
  let config: RuntimeConfig;
  try {
    config = await getConfig();
  } catch (err) {
    root.render(
      <Boot>
        <h1>Karaoke</h1>
        <p className="error">
          Failed to load runtime config: {err instanceof Error ? err.message : String(err)}
        </p>
      </Boot>,
    );
    return;
  }

  // Trusted-LAN clients stay anonymous (LanShell) even when Clerk is enabled;
  // only untrusted (public) clients get the Clerk sign-in wall.
  root.render(
    <StrictMode>
      {config.clerk_enabled && !config.trusted_client ? (
        <ClerkShell config={config} />
      ) : (
        <LanShell config={config} />
      )}
    </StrictMode>,
  );
}

void bootstrap();
