# Karaoke Chrome Extension

Small Manifest V3 operator tool for submitting video URLs to the Karaoke service
(YouTube / `yt-dlp`-supported pages → vocals + instrumental playback + lyrics).

## Install

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Choose Load unpacked.
4. Select `extension/chrome`.

No build step is required.

## Configure

Open the extension options page and set:

- Karaoke base URL — defaults to the public deployment `https://karaoke.oklabs.uk`,
  so the extension works from any machine. On the LAN you can point it at the
  runtime directly, e.g. `http://10.10.0.13:13140`. The host permission patterns
  in `manifest.json` cover `*.oklabs.uk` and the LAN runtime; any other origin is
  requested at save time via `chrome.permissions.request`.
- Optional bearer token. Create a Chrome extension token (`ktx_...`) in Karaoke
  Settings, then paste it here. A token is required when the configured Karaoke
  URL is protected, especially when using it outside a trusted LAN.

Saving the base URL asks Chrome for permission to reach that Karaoke origin.
The token is stored in Chrome sync storage and sent as `Authorization: Bearer ...`
only when configured. No token is hardcoded in the extension.

## YouTube cookies (per-job, ephemeral)

Some videos ask you to "sign in to confirm you're not a bot". To download those,
Karaoke needs a logged-in YouTube session. This extension is the cookie supplier:
on **every submit** it reads your browser's YouTube/Google cookies
(`chrome.cookies.getAll`) and attaches them to that one request as the optional
`youtube_cookies` field of `POST /jobs`. The server uses them for that job's
download only and never persists them.

- Just stay signed in to YouTube in the browser running the extension. There is
  nothing to configure and no periodic background sync.
- Cookie values never touch `chrome.storage`, the badge, logs, or notifications —
  they go straight into the per-job request body and nowhere else.
- If you are not signed in to YouTube, the field is omitted entirely and the
  server falls back to a public (un-authenticated) download.

Per-job cookies are the only cookie path: the server keeps no cookie jar and
has no upload endpoint, so there is no server-side credential to keep fresh —
each submit carries its own session and the server forgets it after the
download.

## API

The extension talks to Karaoke's coordinator over HTTP and (eventually) WebSocket:

- `POST /jobs` — submit a URL. Body:
  `{"url":"...","source":"chrome-extension","youtube_cookies":"<Netscape cookies.txt>"}`.
  `youtube_cookies` is optional and present only when the browser has a logged-in
  YouTube session.
- `GET /jobs/{id}/status` — poll a job (polling fallback only).
- `WS /ws` — canonical live progress channel; subscribe instead of polling.

The token model is the same `ktx_...` Chrome-extension token shape used by
scribe (do not regress to a single shared bearer).

## Manual Verification

1. Load the unpacked extension and keep the default Karaoke base URL
   (`https://karaoke.oklabs.uk`) or set a local/runtime Karaoke URL.
2. If using a non-default Karaoke URL, open the extension options page, save
   the base URL, and approve Chrome's host access prompt.
3. While **signed in to YouTube** in this browser, open a session-gated video
   page and click the toolbar action; confirm a success notification and that
   the job downloads (the per-job cookies were attached).
4. Open a public video page supported by `yt-dlp` and click the toolbar action.
   Confirm Chrome shows a success notification and clicking it opens the job
   page.
5. Right-click a video page and choose Submit this video page to Karaoke;
   confirm success or already-known status is shown clearly.
6. Right-click a video link and choose Submit video link to Karaoke; confirm
   success or already-known status is shown clearly.
7. For a protected Karaoke URL, leave the bearer token blank and submit again;
   confirm a 401/403 notification explains that auth is required.
8. Set an invalid `ktx_...` bearer token for a protected Karaoke URL and submit
   again; confirm the notification explains that the token is invalid or
   unauthorized.
9. Set the base URL to an unreachable host and submit again; confirm the
   notification includes a useful connectivity error.
10. Submit a non-http(s) toolbar page; confirm the extension reports that an
    http(s) video page is required.

## Versioning

Any PR that changes files under `extension/chrome/` (except this README) must
also bump the `"version"` field in `manifest.json` — the CI job
`extension-version-guard` enforces this on every pull request. The extension
version line is independent of the service version in `pyproject.toml`; do not
sync them. The options page shows the loaded version in its footer (read at
runtime from `chrome.runtime.getManifest().version`), so you can check whether
an unpacked copy is current without opening files.

## Tests

```bash
cd extension/chrome
bun test
```

Covers the Netscape serializer and the `POST /jobs` body builder (`youtube_cookies`
present when cookies exist, omitted cleanly when none).

## Icons

The current `icons/` directory ships placeholder PNGs copied from the scribe
extension and renamed to `karaoke-{16,48,128}.png`. Replace with real Karaoke
artwork before publishing — see `icons/TODO.md`.
