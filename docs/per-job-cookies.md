# Per-job YouTube cookies (issue #77)

Session-gated YouTube videos ("Sign in to confirm you're not a bot" on the
player API for a *specific* video — per-video auth, **not** an IP ban) download
only when `yt-dlp` carries a logged-in session. Cookies for that are
**per-job, client-supplied, and ephemeral** — this is the only cookie path.
There is no server-side jar, no rotation endpoint, and no cookie file setting
(#132 retired the #68/#73-era central jar).

## Contract

The submitting client (the Chrome extension on desktop — see
`extension/chrome/README.md`) attaches the user's logged-in YouTube/Google
cookies to the job itself:

- **Field:** `POST /jobs` body, optional `youtube_cookies` — a Netscape
  `cookies.txt` blob as a JSON string. Omitted / `null` / whitespace-only means
  "no cookies": the job proceeds as a public, cookie-less download.
- **Size cap:** 1 MiB (`413` beyond). A real logged-in jar is a few KiB.
- **Format validation** (`cookies_store.validate_netscape_cookies`): each
  non-comment/non-blank line (including yt-dlp `#HttpOnly_` lines) splits into
  exactly **7** tab-separated fields; include-subdomains & secure flags are
  `TRUE`/`FALSE`; expiry is an integer; name is non-empty; **≥1** cookie scoped
  to `youtube.com` (guards against the wrong jar). Failure is `422` with a
  **value-free** message (line index / field count / flag name — never cookie
  contents) and no job is created.

## Lifecycle

1. **Submit:** the validated blob is stashed in an in-process, memory-only
   registry keyed by job id (`karaoke.worker.job_cookies`). It is never written
   to the database and never logged; API responses and logs carry counts or
   nothing, never values. Cancelling or deleting the job before the worker
   starts discards the stashed blob.
2. **Download:** the worker pops the blob exactly once and writes it to a
   `0600` temp file (`ytc-job-*.txt` under the process temp dir) for the
   single `yt-dlp` invocation (`--cookies <temp>`). The temp is deleted on
   context exit — success or failure (`pipeline._ytdlp_aux_args`).
3. **Done:** nothing persists. Subsequent stages (GPU separation, lyrics) never
   see cookies.

## Restart semantics

A coordinator restart drops the in-memory registry — no cookie blob survives a
restart, by design. Boot reconcile (#130) then fails out in-flight jobs and
re-dispatches still-`queued` ones **without** their original cookies: a gated
video fails with the normal gated-video error, whose hint tells the user to
resubmit via the Chrome extension from a browser signed in to YouTube. Public
videos are unaffected.

## Security model (summary)

| Concern | Mitigation |
|---|---|
| Cookies are secrets | memory-only registry; `0600` per-job temp deleted after the download; never logged/echoed; value-free validation errors |
| Oversized / wrong payload | 1 MiB cap (`413`); Netscape validation requiring ≥1 `youtube.com` cookie (`422`) |
| Persistence creep | never in the DB, never in artifacts; a restart wipes the registry |
| Scope creep | consumed once, by the download stage only; GPU stages never receive cookies |
