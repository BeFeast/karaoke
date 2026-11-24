// Song link parsing for the Add song screen. Mirrors the server's YouTube
// normalization (#277): youtube.com/watch, m./music.youtube.com/watch,
// youtu.be and shorts all resolve to the same 11-character video id.

export type SongLink =
  | { kind: "youtube"; id: string; url: string }
  | { kind: "url"; url: string }
  | { kind: "invalid" }
  | { kind: "empty" };

const ID = /^[A-Za-z0-9_-]{11}$/;
const YOUTUBE_HOSTS = new Set(["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"]);

export function youtubeId(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return null;
  }
  const host = url.hostname.toLowerCase();
  let id: string | null = null;
  if (host === "youtu.be" || host === "www.youtu.be") {
    id = url.pathname.split("/")[1] ?? null;
  } else if (YOUTUBE_HOSTS.has(host)) {
    if (url.pathname === "/watch") id = url.searchParams.get("v");
    else if (url.pathname.startsWith("/shorts/") || url.pathname.startsWith("/live/")) id = url.pathname.split("/")[2] ?? null;
  }
  return id && ID.test(id) ? id : null;
}

export function parseSongLink(raw: string): SongLink {
  const text = raw.trim();
  if (!text) return { kind: "empty" };
  // A bare "youtu.be/…" or "youtube.com/…" without a scheme is still a link.
  const candidate = /^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`;
  const id = youtubeId(candidate);
  if (id) return { kind: "youtube", id, url: candidate };
  try {
    const url = new URL(candidate);
    if ((url.protocol === "https:" || url.protocol === "http:") && url.hostname.includes(".") && !/\s/.test(text)) {
      return { kind: "url", url: candidate };
    }
  } catch {
    /* not a URL */
  }
  return { kind: "invalid" };
}
