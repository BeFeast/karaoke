// Tiny hash-based router for the Submitter SPA.
//
// We use HASH routing (`/app/#/job/:token`) rather than real path routing so
// the whole feature stays frontend-only: a hard refresh of a deep link still
// loads `/app/index.html` (StaticFiles serves it), and the client reads the
// fragment — no server-side catch-all / SPA fallback is required.
//
// Routes:
//   #/                     → dashboard (also the empty hash and `#`)
//   #/job/:token           → item page for a single job
//   #/settings             → settings page (ktx_ extension tokens)

import { useEffect, useState } from "react";

export type CatalogShelf = { axis: "collection" | "genre" | "decade"; value: string };

export type Route =
  | { name: "dashboard" }
  | { name: "item"; token: string }
  | { name: "settings" }
  | { name: "my-songs" }
  | { name: "catalog"; shelf: CatalogShelf };

function parseHash(hash: string): Route {
  // Strip a leading "#" and optional leading "/".
  let path = hash.replace(/^#/, "");
  if (path.startsWith("/")) path = path.slice(1);
  const segments = path.split("/").filter(Boolean);

  if (segments[0] === "job" && segments[1]) {
    return { name: "item", token: decodeURIComponent(segments[1]) };
  }
  if (segments[0] === "my-songs") return { name: "my-songs" };
  if (segments[0] === "catalog" && segments[2]) {
    const axis = segments[1];
    if (axis === "collection" || axis === "genre" || axis === "decade") {
      try {
        return { name: "catalog", shelf: { axis, value: decodeURIComponent(segments[2]) } };
      } catch {
        return { name: "dashboard" };
      }
    }
  }
  if (segments[0] === "settings") {
    return { name: "settings" };
  }
  return { name: "dashboard" };
}

/** Reactively resolve the current route from `location.hash`. */
export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  useEffect(() => {
    const onChange = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

/** Build the in-app (hash) link for a job's item page. */
export function itemHash(token: string): string {
  return `#/job/${encodeURIComponent(token)}`;
}

/** Build the in-app (hash) link for the settings page. */
export function settingsHash(): string {
  return "#/settings";
}

/** Build a public player link for the clipboard. The job token grants read access
 * to the share even when the recipient has neither Clerk nor LAN credentials. */
export function publicShareUrl(publicBaseUrl: string, token: string): string {
  return `${publicBaseUrl.replace(/\/+$/, "")}/share/${encodeURIComponent(token)}`;
}

/** Navigate within the SPA by updating the hash. */
export function navigate(hash: string): void {
  if (window.location.hash === hash) return;
  window.location.hash = hash;
}

/** Go back to the dashboard route. */
export function goDashboard(): void {
  navigate("#/");
}

export function catalogHash(shelf?: CatalogShelf): string {
  return shelf ? `#/catalog/${shelf.axis}/${encodeURIComponent(shelf.value)}` : "#/";
}

export const mySongsHash = "#/my-songs";

export function clerkStageToken(token: string): boolean {
  return token.startsWith("cat.") || token.startsWith("clerk.");
}
