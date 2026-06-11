// Same-origin API client for the Karaoke Submitter SPA.
//
// The SPA is served by FastAPI under /app, so every request here is a plain
// same-origin fetch to /config, /me, /jobs, etc. When Clerk is active and the
// user is signed in, a token getter is wired in so requests carry
// `Authorization: Bearer <jwt>`. In LAN mode the getter is null and no auth
// header is sent — the API's trusted-LAN bypass authorises the caller.

export type JobStatus =
  | "queued"
  | "downloading"
  | "separating"
  | "transcribing"
  | "completed"
  | "failed"
  | "cancelled";

// One ready output file of a job (server `JobArtifactOut`).
export interface JobArtifactOut {
  kind: string;
  name: string;
  size: number | null;
}

// Mirrors the server `JobOut` projection in src/karaoke/api/routes.py 1:1 —
// keep the two in sync field-for-field. Datetimes arrive as ISO 8601 strings.
export interface JobOut {
  id: number;
  job_token: string;
  source_url: string;
  title: string | null;
  artist: string | null;
  track: string | null;
  album: string | null;
  duration: number | null;
  status: JobStatus;
  progress: number;
  stage_note: string | null;
  error: string | null;
  share_url: string;
  owner_subject: string;
  // GPU bookkeeping, runtime-neutral names — the ORM columns keep their
  // legacy `vast_*` names but the RunPod path writes them too.
  gpu_instance_id: string | null;
  gpu_cost_micros: number | null;
  created_at: string;
  completed_at: string | null;
  artifacts: JobArtifactOut[];
}

export interface MeOut {
  subject: string;
  email: string | null;
  display_name: string | null;
  state: string;
  is_admin: boolean;
}

export interface RuntimeConfig {
  clerk_publishable_key: string;
  clerk_enabled: boolean;
  // Whether THIS request passed the server's trusted-LAN check — LAN clients
  // keep anonymous LAN mode even when Clerk is enabled for public clients.
  trusted_client: boolean;
  public_base_url: string;
}

// One output file of a job (server `ArtifactOut`).
export interface ArtifactOut {
  kind: string;
  relative_path: string;
  content_type: string | null;
}

// Public share-page payload (server `SharePayload`) — fetched from
// GET /share/{token} with Accept: application/json. The token itself is the
// unlisted-access secret, so only owner *display* attributes are exposed.
export interface SharePayload {
  job_token: string;
  title: string | null;
  status: JobStatus;
  progress: number;
  owner_display_name: string | null;
  artifacts: ArtifactOut[];
}

// Mint response (server `ExtensionTokenMinted` in src/karaoke/api/tokens.py) —
// the only place the raw `ktx_…` value ever appears. Show it once, never store it.
export interface ExtensionTokenMinted {
  id: number;
  token: string;
  label: string;
  created_at: string;
}

// Owner-visible token row (server `ExtensionTokenOut`) — never the raw value.
export interface ExtensionTokenOut {
  id: number;
  label: string | null;
  disabled: boolean;
  created_at: string;
  last_used_at: string | null;
  owner_subject: string;
}

export interface CreateJobInput {
  url: string;
  title?: string;
}

// Returns a Clerk JWT (or null in LAN mode / when signed out).
export type TokenGetter = () => Promise<string | null>;

let tokenGetter: TokenGetter | null = null;

// Wire the Clerk session-token getter once the user is signed in. Passing null
// reverts to LAN mode (no Authorization header).
export function setTokenGetter(getter: TokenGetter | null): void {
  tokenGetter = getter;
}

async function authHeaders(): Promise<HeadersInit> {
  if (!tokenGetter) return {};
  const token = await tokenGetter();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: HeadersInit = {
    ...(await authHeaders()),
    ...(init.headers ?? {}),
  };
  const resp = await fetch(path, { ...init, headers });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`.trim());
  }
  return (await resp.json()) as T;
}

// /config is public — never attach auth (fetched before sign-in).
export async function getConfig(): Promise<RuntimeConfig> {
  const resp = await fetch("/config", { headers: { Accept: "application/json" } });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`config fetch failed: ${resp.status} ${text}`.trim());
  }
  return (await resp.json()) as RuntimeConfig;
}

export function getMe(): Promise<MeOut> {
  return request<MeOut>("/me", { headers: { Accept: "application/json" } });
}

export function listJobs(limit = 50): Promise<JobOut[]> {
  return request<JobOut[]>(`/jobs?limit=${encodeURIComponent(String(limit))}`, {
    headers: { Accept: "application/json" },
  });
}

export function createJob(input: CreateJobInput): Promise<JobOut> {
  const body: CreateJobInput = { url: input.url };
  if (input.title && input.title.trim()) body.title = input.title.trim();
  return request<JobOut>("/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(body),
  });
}

// Delete a job + its artifacts (204, no body).
export async function deleteJob(id: number): Promise<void> {
  const resp = await fetch(`/jobs/${id}`, {
    method: "DELETE",
    headers: { ...(await authHeaders()) },
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`.trim());
  }
}

// Cancel an in-flight job; the worker stops at the next stage boundary.
export function cancelJob(id: number): Promise<JobOut> {
  return request<JobOut>(`/jobs/${id}/cancel`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
}

// Bulk-remove the caller's failed jobs. Returns how many were deleted.
export function clearFailedJobs(): Promise<{ deleted: number }> {
  return request<{ deleted: number }>("/jobs/clear-failed", {
    method: "POST",
    headers: { Accept: "application/json" },
  });
}

// Mint a fresh ktx_ extension token. The server 403s for callers that may not
// mint (trusted-LAN, extension-token actors) — surface that readably upstream.
export function mintToken(label?: string): Promise<ExtensionTokenMinted> {
  const trimmed = label?.trim();
  return request<ExtensionTokenMinted>("/tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(trimmed ? { label: trimmed } : {}),
  });
}

// List the caller's extension tokens (admin callers — trusted-LAN / machine
// bearer — see every row), newest first.
export function listTokens(): Promise<ExtensionTokenOut[]> {
  return request<ExtensionTokenOut[]>("/tokens", {
    headers: { Accept: "application/json" },
  });
}

// Soft-revoke an extension token (204, no body).
export async function revokeToken(id: number): Promise<void> {
  const resp = await fetch(`/tokens/${id}`, {
    method: "DELETE",
    headers: { ...(await authHeaders()) },
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`.trim());
  }
}

// Fetch a single job's share payload. Same endpoint as the server-rendered
// share page, but Accept: application/json returns SharePayload JSON.
// Possession of the token authorises the read; auth headers are still attached
// (when present) so owner-scoping stays correct.
export function getShare(token: string): Promise<SharePayload> {
  return request<SharePayload>(`/share/${encodeURIComponent(token)}`, {
    headers: { Accept: "application/json" },
  });
}

// Fetch plain lyrics text for a job, or null when not (yet) available.
// Same-origin relative path; the artifact endpoint authorises on the token.
export async function getLyricsText(token: string): Promise<string | null> {
  const resp = await fetch(`/share/${encodeURIComponent(token)}/lyrics.txt`, {
    headers: { ...(await authHeaders()) },
  });
  if (!resp.ok) return null;
  return await resp.text();
}

// Fetch the synced LRC body for a job, or null when no synced lyrics exist
// (the endpoint 404s when the `lyrics.lrc` artifact wasn't written — i.e.
// LRCLIB had no synced match). Same-origin raw text; the artifact endpoint
// authorises on the token, mirroring getLyricsText.
export async function getLyricsLrc(token: string): Promise<string | null> {
  const resp = await fetch(`/share/${encodeURIComponent(token)}/lyrics.lrc`, {
    headers: { ...(await authHeaders()) },
  });
  if (!resp.ok) return null;
  return await resp.text();
}
