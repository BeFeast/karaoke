import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { ClerkProvider, SignedIn, SignedOut, useAuth, useClerk, useUser } from "@clerk/clerk-react";
import { App } from "./App";
import { getConfig, type RuntimeConfig, setTokenGetter } from "./api";
import { ItemPage } from "./components/ItemPage";
import { MicMark } from "./components/marks";
import { SettingsPage } from "./components/SettingsPage";
import { SignInScreen, UserAvatar } from "./components/SignInWall";
import { navigate, settingsHash, useRoute } from "./router";
// Self-hosted Marquee webfonts (#152) — latin subsets only, no external
// font <link>. Geist = UI, Geist Mono = mono, Bricolage Grotesque = display,
// Bungee = signage. Weights cover the 400–650 range the stylesheet asks for.
import "@fontsource/geist/latin-400.css";
import "@fontsource/geist/latin-500.css";
import "@fontsource/geist/latin-600.css";
import "@fontsource/geist/latin-700.css";
import "@fontsource/geist-mono/latin-400.css";
import "@fontsource/geist-mono/latin-500.css";
import "@fontsource/geist-mono/latin-600.css";
import "@fontsource/geist-mono/latin-700.css";
import "@fontsource/bricolage-grotesque/latin-400.css";
import "@fontsource/bricolage-grotesque/latin-500.css";
import "@fontsource/bricolage-grotesque/latin-600.css";
import "@fontsource/bricolage-grotesque/latin-700.css";
import "@fontsource/bungee/latin-400.css";
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
        <MicMark size={28} accent="var(--accent-fg)" ink="var(--accent-fg)" />
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

// The design's UserAvatar (SignInWall.tsx) fed with the real Clerk session:
// identity from useUser(), the menu actions from the Clerk client.
function ClerkAvatar() {
  const { user } = useUser();
  const clerk = useClerk();
  const email = user?.primaryEmailAddress?.emailAddress ?? "";
  return (
    <UserAvatar
      name={user?.fullName || user?.username || email}
      email={email}
      onSignOut={() => void clerk.signOut({ redirectUrl: "/app/" })}
      onSettings={() => navigate(settingsHash())}
      onManage={() => void clerk.openUserProfile()}
    />
  );
}

// S1 — the stage door. The CTA opens the real Clerk sign-in (modal flow).
function SignInGate() {
  const clerk = useClerk();
  return <SignInScreen onSignIn={() => void clerk.openSignIn()} />;
}

function ClerkShell({ config }: { config: RuntimeConfig }) {
  return (
    <ClerkProvider publishableKey={config.clerk_publishable_key}>
      <ClerkTokenBridge />
      <SignedOut>
        <SignInGate />
      </SignedOut>
      <SignedIn>
        <Routed config={config} authControl={<ClerkAvatar />} />
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
        <span className="m-chip" style={{ textTransform: "uppercase", letterSpacing: "0.05em", fontSize: 10 }}>
          trusted lan
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
