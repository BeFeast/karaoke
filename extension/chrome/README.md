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

- Karaoke base URL — defaults to the LAN runtime `http://10.10.0.13:13140`.
  Public deployments use the URL configured as `KARAOKE_PUBLIC_BASE_URL` on the
  service (Cloudflare / NPM in front of the host). The host permission patterns
  in `manifest.json` cover the LAN runtime and `*.oklabs.uk`; any other origin
  is requested at save time via `chrome.permissions.request`.
- Optional bearer token. Create a Chrome extension token (`ktx_...`) in Karaoke
  Settings, then paste it here. A token is required when the configured Karaoke
  URL is protected, especially when using it outside a trusted LAN.

Saving the base URL asks Chrome for permission to reach that Karaoke origin.
The token is stored in Chrome sync storage and sent as `Authorization: Bearer ...`
only when configured. No token is hardcoded in the extension.

## API

The extension talks to Karaoke's coordinator over HTTP and (eventually) WebSocket:

- `POST /jobs` — submit a URL. Body: `{"url":"...","source":"chrome-extension"}`.
- `GET /jobs/{id}/status` — poll a job (polling fallback only).
- `WS /ws` — canonical live progress channel; subscribe instead of polling.

The token model is the same `ktx_...` Chrome-extension token shape used by
scribe (do not regress to a single shared bearer).

## Manual Verification

1. Load the unpacked extension and keep the default Karaoke base URL or set a
   local/runtime Karaoke URL.
2. If using a non-default Karaoke URL, open the extension options page, save
   the base URL, and approve Chrome's host access prompt.
3. Open a video page supported by `yt-dlp` and click the toolbar action.
4. Confirm Chrome shows a success notification and clicking it opens
   `{Karaoke base URL}/#/jobs/{job_id}`.
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

## Icons

The current `icons/` directory ships placeholder PNGs copied from the scribe
extension and renamed to `karaoke-{16,48,128}.png`. Replace with real Karaoke
artwork before publishing — see `icons/TODO.md`.
