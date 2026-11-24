import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { ClerkProvider, SignedIn, SignedOut, useAuth, useClerk, useUser } from "@clerk/clerk-react";
import { App } from "./App";
import { FeedbackButton, FeedbackProvider } from "./feedback/Feedback";
import { startFeedbackLog } from "./feedback/diagnostics";
import { getConfig, type RuntimeConfig, setTokenGetter } from "./api";
import { SonereliMark } from "./components/marks";
import { SettingsPage } from "./components/SettingsPage";
import { Stage } from "./components/Stage";
import { SignInScreen, UserAvatar } from "./components/SignInWall";
import { clerkStageToken, navigate, settingsHash, useRoute } from "./router";
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

// Routes between the dashboard (App), the stage room (/app/#/job/:token) and
// the settings page (/app/#/settings). All are rendered inside the auth
// context so owner-scoped reads carry the Clerk bearer when signed in; on the
// LAN they work without auth too. The stage room takes no auth chrome — its
// header is the design's share-page bar (#154).
function Routed({ config, authControl, catalogEnabled = false }: { config: RuntimeConfig; authControl?: React.ReactNode; catalogEnabled?: boolean }) {
  const route = useRoute();
  if (route.name === "item") {
    return <Stage key={route.token} token={route.token} publicBaseUrl={config.public_base_url} />;
  }
  if (route.name === "settings") {
    return <SettingsPage authControl={authControl} />;
  }
  return <App config={config} authControl={authControl} catalogEnabled={catalogEnabled}
    mySongs={route.name !== "catalog"} shelf={route.name === "catalog" ? route.shelf : undefined} />;
}

function Boot({ children }: { children: React.ReactNode }) {
  return (
    <div className="center">
      <span className="brand-mark">
        <SonereliMark size={28} />
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
  return (
    <SignInScreen onSignIn={() => void clerk.openSignIn({
      appearance: { layout: { logoImageUrl: "/favicon.svg", logoLinkUrl: "/app/" } },
    })} />
  );
}

// Clerk routing: the stage room (#/job/:token) renders WITHOUT the sign-in
// wall — possession of the unlisted token authorises the share read
// server-side, so anonymous visitors must reach it (#154). Owners who are
// signed in still carry the Clerk bearer via ClerkTokenBridge. Every other
// route keeps the SignedOut → sign-in wall gate.
function ClerkRouted({ config }: { config: RuntimeConfig }) {
  const route = useRoute();
  if (route.name === "item" && !clerkStageToken(route.token)) {
    return <Stage key={route.token} token={route.token} publicBaseUrl={config.public_base_url} />;
  }
  return (
    <>
      <SignedOut>
        <SignInGate />
      </SignedOut>
      <SignedIn>
        <Routed config={config} authControl={<ClerkAvatar />} catalogEnabled />
      </SignedIn>
    </>
  );
}

function ClerkShell({ config }: { config: RuntimeConfig }) {
  return (
    <ClerkProvider publishableKey={config.clerk_publishable_key}>
      <ClerkTokenBridge />
      <ClerkRouted config={config} />
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
          {config.clerk_enabled && <a href="?sign-in=1"> · Sign in</a>}
        </span>
      }
    />
  );
}

async function bootstrap() {
  const root = createRoot(document.getElementById("root") as HTMLElement);
  const share = /^\/share\/([^/]+)\/?$/.exec(window.location.pathname);
  if (share) {
    root.render(
      <StrictMode>
        <FeedbackProvider>
          <Stage token={decodeURIComponent(share[1])} publicBaseUrl={window.location.origin} shared />
        </FeedbackProvider>
      </StrictMode>,
    );
    return;
  }

  let config: RuntimeConfig;
  try {
    config = await getConfig();
  } catch (err) {
    root.render(
      <FeedbackProvider>
        <Boot>
          <h1>Sonereli</h1>
          <p className="error">
            Failed to load runtime config: {err instanceof Error ? err.message : String(err)}
          </p>
          <FeedbackButton />
        </Boot>
      </FeedbackProvider>,
    );
    return;
  }

  // Preserve the LAN Booth, with an explicit route into Clerk on trusted networks.
  const useClerkShell = config.clerk_enabled && (
    !config.trusted_client
    || new URLSearchParams(window.location.search).has("sign-in")
    || /(?:^|;\s*)__session=/.test(document.cookie)
    || /^#\/job\/(?:cat|clerk)\./.test(window.location.hash)
  );
  root.render(
    <StrictMode>
      <FeedbackProvider baseUrl={config.feedback_base_url}>
        {useClerkShell ? (
          <ClerkShell config={config} />
        ) : (
          <LanShell config={config} />
        )}
      </FeedbackProvider>
    </StrictMode>,
  );
}

startFeedbackLog();
void bootstrap();
