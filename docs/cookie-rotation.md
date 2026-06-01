# YouTube cookie auto-rotation (issue #73)

Session-gated YouTube videos ("Sign in to confirm you're not a bot" on the
player API for a *specific* video — per-video auth, **not** an IP ban) download
only when the coordinator passes a logged-in `cookies.txt` to `yt-dlp` (see
#68/#71). YouTube rotates the session, so a manually-exported file expires and
the service silently regresses to "gated videos fail" until someone re-exports
and re-`scp`s the jar.

This document specifies an **automated cookie-refresh flow**: the user's
already-logged-in browser (Phase 1: Chrome extension) — and later a mobile app
(Phase 2: native iOS/Android) — pushes fresh cookies to the coordinator, which
stores them where the pipeline already reads them.

## Goals

- Zero manual steps to keep gated downloads working: the jar refreshes itself
  from a logged-in browser/app the user already controls.
- Cookies are treated as secrets end-to-end: never logged, never echoed, stored
  `0600`, restricted-auth endpoint.
- The pipeline keeps reading exactly one path (`KARAOKE_YTDLP_COOKIES_FILE`); no
  change to the download path (`worker/pipeline.py::_ytdlp_aux_args`).
- Robust to partial writes and bad uploads (atomic replace + last-known-good).

## Architecture

```
┌─────────────────────┐         POST /cookies/youtube           ┌──────────────────────┐
│ Logged-in browser   │   text/plain Netscape cookies.txt       │ Coordinator (FastAPI) │
│  Chrome extension   │ ──────────────────────────────────────► │  /cookies/youtube     │
│  (ktx_ token)       │   Authorization: Bearer ktx_…           │  validate + atomic    │
└─────────────────────┘                                         │  write + last-good    │
        ▲  chrome.cookies.getAll({domain})                      └───────────┬───────────┘
        │  serialize → Netscape                                             │ writes
        │  on submit + every 6h alarm                                       ▼
                                                          KARAOKE_YTDLP_COOKIES_FILE
                                                          (writable dir mount, 0600)
                                                                            │ reads (per-job copy)
                                                                            ▼
                                                          worker/pipeline.py _ytdlp_aux_args
                                                          → yt-dlp --cookies <per-job temp>
```

## Storage model — the key decision

**Problem.** The rotation endpoint must *write* the jar, but the live stack
currently mounts it **single-file read-only**:

```yaml
# compose.yaml (current)
- ./secrets/youtube-cookies.txt:/cookies/youtube-cookies.txt:ro
```

A single-file bind mount cannot be replaced atomically: `os.replace()` onto a
bind-mounted file fails (`EBUSY` / cross-mount rename), and a `:ro` mount is
unwritable anyway. Atomic write requires creating a temp file in the **same
directory** as the target and `rename(2)`-ing it over the target — which is only
possible when that directory is a writable mount.

**Decision — bind the parent *directory* writable.** The canonical jar lives in
a directory mount the coordinator owns:

```yaml
# compose.yaml (proposed — DEPLOY IMPLICATION, coordinate with operator)
- ./cookies:/cookies            # rw bind of the directory (was :ro single file)
# environment unchanged:
- KARAOKE_YTDLP_COOKIES_FILE=/cookies/youtube-cookies.txt
```

- The coordinator container runs as **root** (no `USER` in `docker/api/Dockerfile`),
  so it can write `/cookies/*` with no UID juggling.
- `os.replace("/cookies/.ytc-XXXX.tmp", "/cookies/youtube-cookies.txt")` is now
  a same-directory rename → atomic; readers (the pipeline) never see a partial
  file.
- Last-known-good is kept as a sibling `/cookies/youtube-cookies.txt.previous`.
- **Migration:** move the existing seed file
  `secrets/youtube-cookies.txt` → `cookies/youtube-cookies.txt` on the host so
  gated downloads keep working until the first extension push lands. Public
  videos work with no jar at all.

Rejected alternatives:

- **Named volume `karaoke-cookies:/cookies`** — also works (writable, atomic
  rename) but loses host visibility for manual seed/inspection. A host-bind
  directory keeps the operator's `scp`-a-seed workflow and is easy to inspect.
- **Endpoint writes to a separate path, pipeline reads another** — needs a sync
  step; pointless indirection. Write where the pipeline reads.
- **Keep `:ro`, write elsewhere, non-atomic copy** — readers could observe a
  truncated jar mid-write. Rejected.

This compose change is the **only** runtime/deploy implication. It is **not**
applied here — it touches the live stack and is surfaced to the team lead /
operator. Until applied, the endpoint returns `503 cookie store is not
configured` in any environment where the path's directory is unwritable (and,
in dev/CI, tests point it at a tmp dir).

## Endpoint — `POST /cookies/youtube`

- **Auth:** restricted to `AuthState.extension_token` (the `ktx_` Chrome
  extension token) **or** `AuthState.machine_bearer` (`KARAOKE_SERVICE_TOKEN`).
  A trusted-LAN-anonymous or Clerk-user request is rejected `403` — neither
  layer implies possession of a logged-in YouTube session. (`require_cookie_writer`.)
- **Body:** raw `text/plain` Netscape `cookies.txt` (no JSON wrapping → no
  escaping of tabs/newlines). Capped at **1 MiB** (`413` beyond).
- **Validation** (`cookies_store.validate_netscape_cookies`): UTF-8; each
  non-comment/non-blank line (including yt-dlp `#HttpOnly_` lines) splits into
  exactly **7** tab-separated fields; include-subdomains & secure flags are
  `TRUE`/`FALSE`; expiry is an integer; name is non-empty; **≥1** cookie scoped
  to `youtube.com` (guards against the wrong jar). Errors are **value-free** —
  they carry only line index / field count / flag name, never cookie contents
  (`422` on failure, and the existing jar is left untouched = last-known-good).
- **Atomic write** (`cookies_store.write_cookies_atomically`): snapshot current
  jar → `…​.previous`, write temp in the same dir, `fsync`, `chmod 0600`,
  `os.replace`. Guarded by a process `asyncio.Lock` so concurrent posters can't
  interleave the snapshot+replace.
- **Response:** non-secret counts only — `{accepted, cookies, youtube_cookies,
  bytes, last_good_kept}`.
- **Logging:** counts + auth state only; **never** values.

### `GET /cookies/youtube` (same auth)

Returns non-secret jar metadata for ops / the extension options page:
`{configured, present, bytes, modified_at, last_good_present}`. No cookie
values.

## Phase 1 — Chrome extension (implemented)

`extension/chrome` (MV3 "Karaoke Submitter") already submits jobs with a `ktx_`
token. Added:

- **`manifest.json`**: `cookies` permission; host permissions
  `https://*.youtube.com/*` and `https://*.google.com/*` (required to read those
  cookies); `background.type: "module"` (to `import` the serializer); version
  bump `0.2.0`.
- **`cookies.js`** (pure, dependency-free, no `chrome.*`): `serializeNetscapeCookies`
  → Netscape `cookies.txt`. Host-only → bare domain + `FALSE`; domain cookies →
  leading dot + `TRUE`; **httpOnly → `#HttpOnly_` prefix** (the essential
  YouTube auth cookies — SID/HSID/SSID/`__Secure-*` — are httpOnly); session →
  expiry `0`; values stripped of stray tab/newline so the 7-field layout can't
  break. Unit-tested by `cookies.test.js` (`bun test`).
- **`service_worker.js`**: `refreshYoutubeCookies()` reads
  `chrome.cookies.getAll({domain})` for `youtube.com` **and** `google.com`
  (google.com carries the shared account auth cookies that strengthen yt-dlp's
  logged-in requests), de-dupes, serializes, and `POST`s `text/plain` with the
  `ktx_` bearer. It runs **on every submit** (the user is demonstrably active +
  logged in) and **every 6h** via `chrome.alarms`, plus on install and on an
  options-page "Sync now" message. Skips silently when no token is configured or
  the browser isn't logged in to YouTube (records a non-secret status for the
  options page; never a noisy notification). Cookie values never touch
  `chrome.storage` or the badge — only counts/status.
- **`options.html` / `options.js`**: a "YouTube cookies" section with a "Sync
  YouTube cookies now" button and a last-sync status line.

### Why both submit-time and a periodic alarm

Submit-time refresh covers the common case (the user just submitted a video, so
the session is hot). The 6h alarm covers the long-lived-tab / "submitted via
extension days ago" case so the server-side jar doesn't go stale between
submits. MV3 alarms survive service-worker suspension.

## Phase 2 — native mobile app (design only, not implemented)

Goal: the same auto-rotation from iOS/Android, where there is no Chrome
extension and no `chrome.cookies` API. A small native app (or a thin extension
to a future Karaoke mobile app) extracts the logged-in YouTube session from a
`WKWebView` (iOS) / `WebView` (Android) and POSTs it to the **same**
`/cookies/youtube` endpoint with a `ktx_`-class token.

### iOS / Android approach

- **React Native shell** (preferred — shares one codebase, matches the React
  thread across the fleet) wrapping a platform WebView, **or** thin native
  (SwiftUI + `WKWebView` / Kotlin + `android.webkit.WebView`) if RN's cookie
  bridges prove flaky.
- **Login:** load `https://accounts.google.com` / `https://m.youtube.com` in the
  WebView; the user signs in once. The app does **not** handle the password — it
  only reads cookies the WebView already holds.
- **Cookie extraction:**
  - iOS: `WKWebsiteDataStore.default().httpCookieStore.getAllCookies { … }` →
    array of `HTTPCookie`. Filter `domain` containing `youtube.com` / `google.com`.
  - Android: `android.webkit.CookieManager.getInstance().getCookie(url)` per
    relevant URL (returns the `name=value; …` header form), or the richer
    per-cookie API on newer WebView versions. Note Android's `CookieManager`
    does **not** expose httpOnly flags or expiry per-cookie cleanly — see
    caveat below.
  - RN: a library such as `@react-native-cookies/cookies` (`CookieManager.get(url, true)`
    — the `useWebKit`/`getAll` variants) bridges both platforms.
- **Serialize → Netscape** using the **same field rules** as `cookies.js`
  (factor the format into a shared spec; the doc's serialization rules are the
  contract). Mark httpOnly cookies with `#HttpOnly_` where the platform exposes
  the flag.
- **POST** `text/plain` to `/cookies/youtube` with the bearer token, on app
  foreground + a background refresh task (iOS `BGAppRefreshTask`, Android
  `WorkManager` periodic, ~6h), mirroring the extension cadence.

### Phase 2 caveats / open questions (resolve at implementation)

- **httpOnly visibility.** Android `CookieManager.getCookie` returns only
  `name=value` pairs and hides httpOnly/secure/expiry metadata; the essential
  YouTube auth cookies are httpOnly. Mitigations: use the WebKit-backed RN cookie
  API (`getAll(useWebKit:true)` exposes more fields on iOS), or default httpOnly
  cookies' Netscape flags conservatively (`#HttpOnly_`, `TRUE` secure, session
  expiry) when the platform can't tell us. Validate against a real gated video
  before shipping.
- **Token provisioning on mobile.** Reuse the `ktx_` extension-token model (mint
  in Karaoke settings, paste/scan into the app) or add a Clerk-authenticated
  mint endpoint. A dedicated `kmx_` (mobile) prefix could be introduced but the
  endpoint auth is unchanged — any extension-class token works.
- **App Store / Play policies.** Reading the user's *own* logged-in cookies from
  a WebView the user authenticated in is allowed; document the data flow (cookies
  go only to the user's self-hosted Karaoke server) in the privacy policy.
- **Background execution limits.** iOS background refresh is best-effort
  (system-scheduled); rely on foreground refresh as the floor, same as the
  extension's submit-time refresh.

## Security model (summary)

| Concern | Mitigation |
|---|---|
| Cookies are secrets | `0600` file; never logged/echoed; value-free validation errors; counts-only responses |
| Endpoint abuse | Auth = extension-token / machine-bearer only (`403` for LAN-anon & Clerk); 1 MiB cap |
| Wrong jar uploaded | Validation requires ≥1 `youtube.com` cookie |
| Partial write | Atomic `os.replace` within a writable dir mount; `fsync` before replace |
| Bad rotation | Last-known-good kept as `…​.previous`; canonical only ever holds a validated jar |
| Concurrent writers | Process `asyncio.Lock` around snapshot+replace |
| At rest | Host-bind dir on the devbox stack (LAN); cookies already a manually-managed secret today — this does not widen exposure |

## Files

- `src/karaoke/api/cookies_store.py` — validation + atomic write (pure, tested).
- `src/karaoke/api/routes.py` — `POST`/`GET /cookies/youtube` + `require_cookie_writer`.
- `tests/api/test_cookies.py` — auth, validation, atomic write, last-known-good,
  no value leakage, status.
- `extension/chrome/{manifest.json,cookies.js,service_worker.js,options.html,options.js}` —
  Phase 1.
- `extension/chrome/cookies.test.js` — serializer unit tests (`bun test`).
- **`compose.yaml`** — writable `/cookies` dir mount: **not changed here**;
  deploy implication for the operator (see Storage model).

## Operator path — headless cron (prisma supplier)

For the self-host operator, a browserless alternative to the Chrome extension
keeps the jar fresh: a `launchd` cron on a Mac with a logged-in YouTube session
in **Firefox** re-exports the jar every 6h (`yt-dlp --cookies-from-browser
firefox`) and `POST`s it to the same `/cookies/youtube` endpoint with a scoped
`ktx_` token. No browser process, no GUI, no extension — `yt-dlp` reads
Firefox's `cookies.sqlite` directly (Firefox cookies are not macOS-keychain
encrypted, unlike Chrome). See
[`scripts/cookie-rotation/README.md`](../scripts/cookie-rotation/README.md) for
the mint + install runbook. The extension (Phase 1) stays the path for end users.
