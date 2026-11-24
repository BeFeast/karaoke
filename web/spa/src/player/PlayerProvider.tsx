// The song lives above navigation (Sonereli UI/UX v1 step 8): one player for
// the whole app. The Stage opens a song session here; minimizing the Stage or
// switching tabs keeps it playing, and the mini-player above Add song shows
// it. The provider also remembers the position for Continue singing, keeps
// the per-song Original vocals level, and feeds the Media Session (lock
// screen / Control Center: metadata, play/pause, ±5 s, seek).

import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { fetchProtectedArtifact } from "../api";
import type { LyricLine } from "../components/SyncedLyrics";
import { coverUrl, saveLastPlayed } from "../lib/library";
import { clampPct, saveSongVocalPct, songVocalPct } from "../lib/vocals";
import { type KaraokePlayerApi, useKaraokePlayer } from "./useKaraokePlayer";

export interface SongSession {
  token: string;
  title: string;
  artist: string | null;
  sourceUrl: string;
  duration: number | null;
  instrumentalUrl: string;
  vocalsUrl: string | null;
  /** Catalog / Clerk-owned stems need the bearer to download. */
  protectedStage: boolean;
  /** A guest share link: no mini-player, no Continue singing. */
  shared: boolean;
  /** Sung lyric lines (mini-player line, lyric at a scrub point). */
  lines: LyricLine[];
}

export interface PlayerSession {
  session: SongSession | null;
  player: KaraokePlayerApi;
  /** Original vocals, 0–100. */
  vocalPct: number;
  setVocalPct: (pct: number) => void;
  /** Mic toggle: 0 ↔ the previous level. */
  toggleVocals: () => void;
  /** Start (or refresh) the session for a song. Another song replaces it. */
  open: (session: SongSession) => void;
  close: () => void;
}

const PlayerContext = createContext<PlayerSession | null>(null);
// The song alone, without the 10 Hz clock: screens that only need to know
// whether a song is open don't re-render while it plays.
const SongContext = createContext<SongSession | null>(null);

export function useCurrentSong(): SongSession | null {
  return useContext(SongContext);
}

export function usePlayerSession(): PlayerSession {
  const value = useContext(PlayerContext);
  if (!value) throw new Error("usePlayerSession needs a PlayerProvider");
  return value;
}

/** The session, or null outside a provider (screens that may render without one). */
export function useOptionalPlayerSession(): PlayerSession | null {
  return useContext(PlayerContext);
}

export function PlayerProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SongSession | null>(null);
  const player = useKaraokePlayer({
    instrumentalUrl: session?.instrumentalUrl ?? null,
    vocalsUrl: session?.vocalsUrl ?? null,
    fetchStem: session?.protectedStage ? fetchProtectedArtifact : undefined,
  });
  const playerRef = useRef(player);
  playerRef.current = player;
  const sessionRef = useRef(session);
  sessionRef.current = session;
  const lastOn = useRef(25);

  const open = useCallback((next: SongSession) => {
    const current = sessionRef.current;
    if (current?.token === next.token) {
      // Same song: refresh what changed (lyrics arrive after the audio).
      if (current.lines !== next.lines || current.title !== next.title || current.artist !== next.artist) {
        setSession({ ...current, lines: next.lines, title: next.title, artist: next.artist });
      }
      return;
    }
    const pct = songVocalPct(next.token);
    lastOn.current = pct > 0 ? pct : 25;
    playerRef.current.setVocalLevel(pct / 100);
    setSession(next);
  }, []);

  const close = useCallback(() => setSession(null), []);

  const setVocalPct = useCallback((pct: number) => {
    const value = clampPct(pct);
    if (value > 0) lastOn.current = value;
    playerRef.current.setVocalLevel(value / 100);
    const token = sessionRef.current?.token;
    if (token) saveSongVocalPct(token, value);
  }, []);

  const vocalPct = Math.round(player.vocalLevel * 100);
  const toggleVocals = useCallback(() => {
    setVocalPct(Math.round(playerRef.current.vocalLevel * 100) > 0 ? 0 : lastOn.current);
  }, [setVocalPct]);

  // Continue singing (step 4): remember the song and the position while it
  // plays, on every screen.
  const savedAt = useRef(-10);
  useEffect(() => {
    const time = player.currentTime;
    if (!session || session.shared || time <= 0 || Math.abs(time - savedAt.current) < 1) return;
    savedAt.current = time;
    saveLastPlayed({
      token: session.token, title: session.title, artist: session.artist, sourceUrl: session.sourceUrl,
      position: time, duration: session.duration, at: Date.now(),
    });
  }, [player.currentTime, session]);

  useMediaSession(session, player, playerRef);

  const value = useMemo<PlayerSession>(
    () => ({ session, player, vocalPct, setVocalPct, toggleVocals, open, close }),
    [session, player, vocalPct, setVocalPct, toggleVocals, open, close],
  );
  return (
    <SongContext.Provider value={session}>
      <PlayerContext.Provider value={value}>{children}</PlayerContext.Provider>
    </SongContext.Provider>
  );
}

function useMediaSession(
  session: SongSession | null,
  player: KaraokePlayerApi,
  playerRef: React.MutableRefObject<KaraokePlayerApi>,
) {
  const media = typeof navigator !== "undefined" && "mediaSession" in navigator ? navigator.mediaSession : null;
  const token = session?.token;
  const title = session?.title;
  const artist = session?.artist;
  const sourceUrl = session?.sourceUrl;

  useEffect(() => {
    if (!media || !token) return;
    const cover = sourceUrl ? coverUrl(sourceUrl) : null;
    try {
      media.metadata = new MediaMetadata({
        title: title ?? "",
        artist: artist ? `${artist} · Sonereli` : "Sonereli",
        artwork: cover ? [{ src: cover, sizes: "320x180", type: "image/jpeg" }] : [],
      });
    } catch {
      /* MediaMetadata unsupported */
    }
    const handlers: [MediaSessionAction, MediaSessionActionHandler][] = [
      ["play", () => !playerRef.current.playing && playerRef.current.playPause()],
      ["pause", () => playerRef.current.playing && playerRef.current.playPause()],
      ["seekbackward", (d) => playerRef.current.skip(-(d.seekOffset ?? 5))],
      ["seekforward", (d) => playerRef.current.skip(d.seekOffset ?? 5)],
      ["seekto", (d) => d.seekTime != null && playerRef.current.seek(d.seekTime)],
    ];
    for (const [action, handler] of handlers) {
      try {
        media.setActionHandler(action, handler);
      } catch {
        /* action unsupported */
      }
    }
    return () => {
      for (const [action] of handlers) {
        try {
          media.setActionHandler(action, null);
        } catch {
          /* ignore */
        }
      }
      media.metadata = null;
    };
  }, [media, token, title, artist, sourceUrl, playerRef]);

  useEffect(() => {
    if (media && token) media.playbackState = player.playing ? "playing" : "paused";
  }, [media, token, player.playing]);

  const second = Math.floor(player.currentTime);
  useEffect(() => {
    if (!media?.setPositionState || !token || !(player.duration > 0)) return;
    try {
      media.setPositionState({
        duration: player.duration,
        position: Math.min(player.duration, Math.max(0, playerRef.current.currentTime)),
        playbackRate: player.rate,
      });
    } catch {
      /* invalid state while loading */
    }
  }, [media, token, player.duration, player.rate, second, playerRef]);
}
