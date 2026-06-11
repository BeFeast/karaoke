# Karaoke Chrome Extension

Small Manifest V3 operator tool for submitting video URLs to the Karaoke service
(YouTube / `yt-dlp`-supported pages → vocals + instrumental playback + lyrics).

## Doorway UI (Marquee port, issue #155)

The popup and options pages are ports of the accepted "Karaoke Final" design
boards (`design/m-doorway.jsx` + `design/marquee-mark.jsx` hold the verbatim
export sources; the live pages adapt their DOM/classes/styles):

- **Popup-as-receipt** — the toolbar click opens `popup.html`, which submits
  the active tab through the service worker and renders the real `POST /jobs`
  response as the receipt card, plus the real `GET /jobs` "tonight" mini-feed.
  Reopening the popup on the same tab+URL shows the existing receipt instead
  of minting a second job (`chrome.storage.session` dedup).
- **Toolbar badge** — idle (none) / working (job progress %) / ready (✓) /
  error (!), following the submitted job via a poll alarm.
- **`marquee.css`** — the extension's single styling source: the Marquee token
  blocks (Wave-0 green bake literals) + the recipes block vendored byte-exact
  from `web/spa/src/styles.css`. Re-vendor from the SPA stylesheet; never edit
  by hand. `doorway.css` adds self-hosted webfonts (`fonts/`, the same
  fontsource woff2 files the SPA uses) and page resets.

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
   page and click the toolbar action; confirm the popup receipt shows the job
   with "youtube session ✓ rode along" and that the job downloads (the
   per-job cookies were attached).
4. Open a public video page supported by `yt-dlp` and click the toolbar action.
   Confirm the popup receipt renders the job, the "tonight" feed lists real
   jobs, and "open the booth →" opens the SPA. Close and reopen the popup on
   the same tab; confirm no second job is created.
5. Right-click a video page and choose Submit this video page to Karaoke;
   confirm a success notification and that clicking it opens the job page.
6. Right-click a video link and choose Submit video link to Karaoke; confirm
   a success notification.
7. For a protected Karaoke URL, leave the bearer token blank and submit again;
   confirm the receipt (toolbar) or notification (context menu) explains that
   auth is required.
8. Set an invalid `ktx_...` bearer token for a protected Karaoke URL and submit
   again; confirm the error explains that the token is invalid or unauthorized.
9. Set the base URL to an unreachable host and submit again; confirm the error
   includes a useful connectivity message.
10. Open the popup on a non-http(s) page (e.g. `chrome://extensions`); confirm
    the receipt says nothing was submitted and asks for an http(s) video page.

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

`icons/karaoke-{16,32,48,128}.png` and the SPA favicon (`web/spa/public/favicon.png`)
are rasterized from `icons/mark.svg` — the sign-in mic card (#163): the
SignInWall rounded card holding the MicMark glyph with its geometry kept
verbatim (see the SVG header) and the day-card green-bake literals. The mark
is pure geometry, so no font tooling is involved. To regenerate:

```bash
cd extension/chrome
bun install
bun run icons
```

Do not redraw the mark per size — `render-icons.mjs` renders the exact vector
at each target size.
