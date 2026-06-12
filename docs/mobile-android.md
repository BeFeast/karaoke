# Android submitter app (issue #169)

A minimal native Android app ("Karaoke Submitter", `mobile/android/`) that
submits videos to the karaoke coordinator from a phone. It registers as a
share target for the YouTube app, can sign in to YouTube in an embedded
WebView, and attaches the resulting cookies **per job** — the same contract
the Chrome extension uses on desktop (see
[`per-job-cookies.md`](per-job-cookies.md)): cookies ride with one
`POST /jobs` submit, are used for exactly one `yt-dlp` run, and are **never
stored server-side**.

There is no Play Store listing and no release signing — the app is a
**debug APK built by CI**, sideload only. Merging changes under
`mobile/android/` never deploys anything to the stack.

## Getting the APK

1. Open the repo's GitHub Actions, pick the latest green run on `main`
   (or the CI run of the PR you care about).
2. Download the `karaoke-submitter-debug-apk` artifact (30-day retention) and
   unzip it to get `app-debug.apk`.
3. Copy it onto the phone and open it. Android will ask to allow installs
   from unknown sources for the file manager / browser doing the install —
   allow it (Settings → Apps → Special app access → Install unknown apps).

## First-run setup

1. Open the app → **Settings**.
2. **Base URL**: keep `https://karaoke.oklabs.uk` (default) or point at your
   own deployment.
3. **Access pass**: in the web app, open `<base URL>/app/#/settings` → "Mint
   pass" and paste the `ktx_…` token into the app. The pass is stored in
   `EncryptedSharedPreferences` on the device and is never logged.

The main screen shows a last-5 jobs mini-feed (`GET /jobs?limit=5`). The
`ktx_` pass is owner-scoped: the feed shows only jobs submitted with that
pass, and each row links out to the job's share page.

## Submitting

- **Share target:** in the YouTube app (or any app sharing a link as text),
  Share → "Karaoke Submitter". The first `http(s)://` URL in the shared text
  is prefilled; confirm with Submit.
- **Manual:** paste any yt-dlp-supported URL into the URL field and Submit.

The receipt under the button shows the created job's id, title, and status.
Error mapping: 401 → re-check the pass in Settings; 413 → cookie payload too
large; 422 → the server's (value-free) validation detail.

## YouTube sign-in and cookies

- **No cookies → public videos only.** Without a YouTube session the server
  runs a public, cookie-less download; session-gated videos fail with the
  gated-video error.
- **WebView sign-in:** "Sign in to YouTube" opens a WebView on the Google
  sign-in page. The app masks the two WebView fingerprints Google checks
  (user-agent `; wv` token, `X-Requested-With` header) — the standard
  workaround for the "This browser or app may not be secure" block. After
  signing in, tap Done; the main screen shows "YouTube: signed in
  (N cookies)".
- **Cookie shape:** Android's `CookieManager` exposes only `name=value` pairs,
  so the app synthesizes Netscape attributes (domain `.youtube.com` /
  `.google.com`, path `/`, secure, session expiry) and serializes a jar whose
  data lines match the Chrome extension's serializer byte-for-byte for
  identical input.
- **Manual fallback — paste cookies.txt:** if Google hard-blocks the WebView
  sign-in, export a Netscape `cookies.txt` from a signed-in desktop browser
  (e.g. the "Get cookies.txt LOCALLY" extension) and paste it via
  "Paste cookies.txt". The jar is validated like the server validates it
  (incl. the ≥1 `youtube.com` cookie gate), stored encrypted on the device,
  and takes precedence over WebView cookies until cleared.
- Cookies are attached to a job **only when** at least one `youtube.com`
  cookie is present; otherwise the `youtube_cookies` field is omitted
  entirely. Per-job lifecycle, restart semantics, and the security model are
  documented in [`per-job-cookies.md`](per-job-cookies.md).

## Building locally / in CI

The Gradle project is self-contained under `mobile/android/` (committed
wrapper, JDK 17, AGP 8.7, Kotlin 2.1, `minSdk 26`, `targetSdk 35`):

```bash
cd mobile/android
./gradlew testDebugUnitTest lintDebug   # pure-JVM unit tests + Android lint
./gradlew assembleDebug                 # app/build/outputs/apk/debug/app-debug.apk
```

CI runs exactly those commands in the `android` job and uploads the APK
artifact. The job always reports success on PRs that do not touch
`mobile/android/**` (change-detection first step + step-level guards).

iOS is intentionally out of scope here — the iOS path is a Shortcut without
cookies, tracked separately under #78.
