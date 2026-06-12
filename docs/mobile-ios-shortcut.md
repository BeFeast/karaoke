# iOS: submit to Karaoke with a Shortcut

Refs #78. iOS has no extension/app — a stock **Shortcuts** automation is the submitter. It POSTs the shared URL to `POST /jobs` with a `ktx_` access pass. It sends **no YouTube cookies**, so it covers **public videos only**; session-gated videos ("Sign in to confirm you're not a bot") will fail and should be submitted from the Chrome extension or the Android app instead (see `docs/per-job-cookies.md`).

Examples below use `https://karaoke.oklabs.uk` — substitute your deployment's public base URL.

## 1. Mint an access pass (`ktx_` token)

1. Open `https://karaoke.oklabs.uk/app/#/settings` in a browser and sign in.
2. In the passes section, enter a name (e.g. `iphone-shortcut`) and tap **Mint pass**.
3. Copy the full `ktx_…` value from the reveal banner — **it is shown exactly once**. If you lose it, revoke the pass and mint a new one.

Note: minting requires a signed-in (Clerk) user or the machine bearer. A trusted-LAN browser session without sign-in can list and revoke passes but gets **403** on mint — sign in, or mint via the API with the machine bearer from a trusted host.

## 2. Build the Shortcut

1. Open **Shortcuts** → **+** → name it **Send to Karaoke**.
2. Tap the info panel (ⓘ) → enable **Show in Share Sheet**. In the **Receive** input configuration at the top of the actions list, set it to accept **URLs** (you can add **Text** too) from **Share Sheet**; set *If there's no input* → **Ask For** → **Text** (lets you run it manually by pasting a link).
3. (Recommended hardening) Add action **Get URLs from Input** with input = **Shortcut Input** — some apps share rich text around the link; this extracts the bare URL. Follow it with **Get Item from List** → **First Item**: share payloads sometimes carry several URLs, and the server accepts any non-empty string — a joined list would silently create a doomed job.
4. Add action **Get Contents of URL**:
   - URL: `https://karaoke.oklabs.uk/jobs`
   - Tap **Show More**:
     - **Method**: `POST`
     - **Headers**: add key `Authorization`, value `Bearer ktx_…` (the literal word `Bearer`, one space, then the full pass — paste it exactly).
     - **Request Body**: `JSON` → add a **Text** field with key `url` and value = the **URLs** variable from step 3 (or **Shortcut Input** if you skipped it). The action sets `Content-Type: application/json` automatically.
5. Add action **Get Dictionary Value**: Get **Value** for key `id` in **Contents of URL** (the action parses the JSON response into a dictionary).
6. Add action **Show Notification**: body `Karaoke: queued job #` followed by the **Dictionary Value** variable.

## 3. Use it

In the YouTube app (or Safari) on the video: **Share → Send to Karaoke** (it may be under "More"/the Shortcuts row). You get a "queued job #N" notification on success (HTTP 201). Progress and the result player are in the web app at `https://karaoke.oklabs.uk/app/` — the response also carries `share_url` (the public result page) if you want to surface it in the notification instead.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Notification never appears / error banner | Response wasn't 201 | Temporarily replace **Show Notification** with **Show Result** on **Contents of URL** to see the raw response |
| `401` / "unrecognised bearer token" | Pass revoked, mistyped, or the `Bearer ` prefix is missing in the header value | Re-paste the header value as `Bearer ktx_…`; mint a fresh pass if revoked |
| `422` validation error | Only happens when the `url` field is missing/empty | Check the JSON body wiring. Note: non-URL text is ACCEPTED (201) and the job fails later at the download stage — if you get queued jobs that immediately fail, add the **Get URLs from Input** + **First Item** steps (step 3 above) |
| Job is created but fails with "Sign in to confirm you're not a bot" | Session-gated video; the Shortcut path sends no cookies **by design** | Expected on iOS. Resubmit from the Chrome extension or the Android app, which attach per-job YouTube cookies |
| HTML/Cloudflare challenge page instead of JSON | Edge bot protection challenged the Shortcuts client | Machine clients are verified to pass the current edge config; if this appears after an edge-rule change, the operator needs a WAF skip rule for the API path |

What works without cookies: regular public videos AND most age-restricted ones (the pipeline's embedded player clients bypass the age gate — live-verified). What doesn't: session-gated ("Sign in to confirm you're not a bot") and members-only videos.

## Security note

- The `ktx_` pass is embedded in the Shortcut on your device. Shortcuts sync via iCloud — anyone with access to your iCloud/Shortcuts can use it. **Never share/export the Shortcut file without removing the token first.**
- Blast radius is small by design: a `ktx_` pass is owner-scoped (it sees and manages only its own jobs via `GET /jobs`), is not admin, and cannot mint further passes.
- Revoke at any time at `https://karaoke.oklabs.uk/app/#/settings` (delete the pass row) — the Shortcut then gets 401 on the next submit.