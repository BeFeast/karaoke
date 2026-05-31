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

export interface JobOut {
  id: number;
  job_token: string;
  source_url: string;
  title: string | null;
  status: JobStatus;
  progress: number;
  error: string | null;
  share_url: string;
  owner_subject: string;
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
  public_base_url: string;
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
