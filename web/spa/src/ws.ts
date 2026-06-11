// Live job-progress client for the SPA (issue #141).
//
// Connects to the backend's global `WS /ws` broadcast feed (issue #8) on the
// same origin the SPA was served from, and surfaces typed `stage_change` /
// `heartbeat` frames plus the socket's open/closed state. REST stays the
// source of truth for full job rows — the socket only carries incremental
// status/progress/stage_note updates — so callers keep polling as a fallback
// while the socket is down.
//
// Auth mirrors api.ts: when the Clerk shell has wired a token getter, the
// bearer token goes out as the first frame after connect (the browser
// WebSocket API can't set an Authorization header, and a query string would
// leak the JWT into access logs); in LAN mode nothing is sent and the
// server's trusted-LAN bypass authorises the caller. The server ignores
// frames it doesn't recognise, so this stays forward-compatible if/when
// /ws starts enforcing the handshake.

import { getAuthToken, type JobStatus } from "./api";

// Stages as they arrive over WS. `finalizing` is WS-only — emitted between
// the GPU window and `completed`, never persisted on the Job row — so it is
// deliberately not part of the REST `JobStatus` union.
export type WsJobStatus = JobStatus | "finalizing";

// Server `make_stage_event` (src/karaoke/api/ws.py) — pushed on every stage
// transition and replayed to late subscribers.
export interface StageChangeEvent {
  type: "stage_change";
  job_id: number;
  status: WsJobStatus;
  progress: number;
  stage_note: string | null;
  error: string | null;
  ts: string;
}

// Server `make_heartbeat_event` — emitted every few seconds while a job is
// in a non-terminal stage. Carries no stage_note/error.
export interface HeartbeatEvent {
  type: "heartbeat";
  job_id: number;
  status: WsJobStatus;
  progress: number | null;
  ts: string;
}

export type JobEvent = StageChangeEvent | HeartbeatEvent;

export function isTerminal(status: WsJobStatus): boolean {
  return status === "completed" || status === "failed" || status === "cancelled";
}

export interface JobSocketOptions {
  /** Called for every parsed job event. Exceptions are swallowed. */
  onEvent: (event: JobEvent) => void;
  /** Called with true on open and false on close; reconnects re-fire it. */
  onOpenChange?: (open: boolean) => void;
}

const BACKOFF_BASE_MS = 1_000;
const BACKOFF_CAP_MS = 30_000;

function wsUrl(): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}/ws`;
}

// Narrow one raw frame to a JobEvent. `cost_update` / `error` frames (and
// anything unknown) return null — the dashboard only tracks stage state.
function parseJobEvent(data: unknown): JobEvent | null {
  if (typeof data !== "string") return null;
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null) return null;
  const obj = raw as Record<string, unknown>;
  if (typeof obj.job_id !== "number" || typeof obj.status !== "string") return null;
  const ts = typeof obj.ts === "string" ? obj.ts : "";
  if (obj.type === "stage_change" && typeof obj.progress === "number") {
    return {
      type: "stage_change",
      job_id: obj.job_id,
      status: obj.status as WsJobStatus,
      progress: obj.progress,
      stage_note: typeof obj.stage_note === "string" ? obj.stage_note : null,
      error: typeof obj.error === "string" ? obj.error : null,
      ts,
    };
  }
  if (obj.type === "heartbeat") {
    return {
      type: "heartbeat",
      job_id: obj.job_id,
      status: obj.status as WsJobStatus,
      progress: typeof obj.progress === "number" ? obj.progress : null,
      ts,
    };
  }
  return null;
}

/**
 * Open the live feed and keep it open: reconnect with capped exponential
 * backoff (1s, 2s, 4s … 30s; reset on a successful open). Returns a dispose
 * function that stops reconnecting and closes the socket.
 */
export function connectJobSocket(opts: JobSocketOptions): () => void {
  let ws: WebSocket | null = null;
  let attempts = 0;
  let timer: number | null = null;
  let disposed = false;

  const scheduleReconnect = () => {
    if (disposed || timer !== null) return;
    const delay = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** Math.min(attempts, 5));
    attempts += 1;
    timer = window.setTimeout(() => {
      timer = null;
      open();
    }, delay);
  };

  const open = () => {
    if (disposed) return;
    let socket: WebSocket;
    try {
      socket = new WebSocket(wsUrl());
    } catch {
      // Constructor throws on malformed URLs / blocked contexts — retry,
      // the fallback poller covers the gap.
      scheduleReconnect();
      return;
    }
    ws = socket;

    socket.onopen = () => {
      if (socket !== ws) return;
      attempts = 0;
      // Authenticate like REST: bearer (when wired) as the first frame.
      void getAuthToken()
        .then((token) => {
          if (token && socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ action: "auth", token }));
          }
        })
        .catch(() => {
          // Token getter failed (e.g. Clerk session expiring) — the socket
          // stays usable; REST calls surface the auth problem.
        });
      opts.onOpenChange?.(true);
    };

    socket.onmessage = (msg: MessageEvent) => {
      const event = parseJobEvent(msg.data);
      if (!event) return;
      try {
        opts.onEvent(event);
      } catch {
        // A handler bug must never take down the socket loop.
      }
    };

    socket.onclose = () => {
      if (socket !== ws) return;
      ws = null;
      opts.onOpenChange?.(false);
      scheduleReconnect();
    };

    // The close event always follows error — onclose owns the reconnect.
    socket.onerror = () => {};
  };

  open();

  return () => {
    disposed = true;
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
    const socket = ws;
    ws = null;
    if (socket) {
      socket.onopen = null;
      socket.onmessage = null;
      socket.onclose = null;
      socket.onerror = null;
      try {
        socket.close();
      } catch {
        // Already closed — nothing to clean up.
      }
    }
  };
}
