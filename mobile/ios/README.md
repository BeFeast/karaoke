# Sonereli for iPhone

Sonereli uses the public coordinator at `https://sonereli.oklabs.uk`. The
app reads the coordinator's Clerk publishable key from `/config`, signs in
with ClerkKit and attaches a session JWT to each owner API request. The
coordinator keeps ownership and artifact handling on the existing API.

## Share from YouTube

1. In YouTube, open a video and choose **Share → Sonereli**. Use **More** if
   Sonereli is in the additional destinations on the iOS share sheet.
2. Tap **Done**, then open Sonereli. The share extension saves the URL in
   the shared App Group. iOS share extensions return to the source app.
3. Sign in when prompted. Add song opens with the shared link; tap
   **Prepare song**. Processing follows the job over the WebSocket; when it
   is ready, tap **Start singing**. The song also appears in **My songs**.

## Interface (UI/UX v1, 0.2.0)

Three tabs, My songs · Catalog · Settings, with **Add song** pinned above the
tab bar on My songs and Catalog. The UI follows the device language (English
or Russian); Hebrew appears only in content. Brand v3 colours, dark only;
Anton (display) and Inter are bundled under the SIL Open Font License
(`App/Fonts/*-OFL.txt`).

The Stage keeps both stems on one audio timeline. The backing track always
plays at full volume; **Original vocals** rides a perceptual (dB) curve. The
first song on a device starts at 50%, later songs at the level last chosen on
the Stage or in Settings; the mic toggle mutes without changing it. The song
lives above navigation: minimizing the Stage leaves a mini-player on every
tab. The Lock Screen shows artwork, ±5 s and play/pause on the same playback
position as the app. Temporary audio files are removed when the song closes.

## Simulator verification

On a Mac with Xcode 26 or later and an iPhone simulator runtime:

```sh
scripts/verify ios
scripts/verify ios-stage
scripts/verify ios-share
```

The first command runs a Release simulator build and the Debug XCUITests:
the Stage with Hebrew lyrics and the mini-player, the share → Processing →
Sing flow, an English gallery of every main screen and a Russian pass.
The fixture sign-in and share inbox used by the tests exist only in Debug.
The checks exercise the Stage with different stem sample rates and mixed
Hebrew text, plus the share confirmation and progress flow with fixture
events. Logs, commit/build metadata, the result bundle and screenshots are
under `mobile/ios/.build-ci/` by default.

`SONERELI_SIM_DESTINATION` overrides the default iPhone destination.
`SONERELI_TOOLS_DIR` can point at the pinned HedRoom XcodeGen cache. The M4
lane is run via the existing linked T3 thread. The Forgejo workflow runs
the same command when a registered macOS runner is available and the
repository variable `SONERELI_MACOS_RUNNER` is set to `enabled`.

## TestFlight

- Register explicit App IDs `com.befeast.sonereli` and
  `com.befeast.sonereli.share` on the Apple Developer team and attach
  `group.com.befeast.sonereli` to both. Use the corresponding Sonereli app
  record in App Store Connect.
- In the existing Clerk instance that issues the coordinator's JWTs,
  configure the native application for the team and bundle ID. Allow
  `com.befeast.sonereli://callback` for OAuth. If using passkeys or
  Universal Links, provision and verify the associated domains for those
  flows. Validate live sign-in against `GET /me` before distribution.
- Run the simulator suite on the exact intended commit. Use an App Store
  Connect API key with provisioning and upload access for Sonereli. Set the
  key metadata in `SONERELI_ASC_KEY_ID` and `SONERELI_ASC_ISSUER_ID` and
  install the decoded private key in altool's `private_keys` directory.
- Run `SONERELI_SOURCE_SHA=<full-sha> mobile/ios/scripts/m4-archive-upload.sh`
  from a clean checkout. Build numbers default to the commit count.
  `SONERELI_BUILD_NUMBER` can override the count for a repeat upload.
  `--no-upload` stops at App Store Connect validation.
- Once processing completes, assign the build to the internal group and
  verify it installs and plays on an iPhone. For external testing prepare a
  Beta App Review contact, a usable demo account and a source authorized
  for review. Invite the tester with their Apple Account email after review.

After installing the delivered build, create one authorized real job from
YouTube Share, confirm it in the app, watch WebSocket progress through
completion and play both stems in the Stage. On the phone, test play/pause
on the Lock Screen, seek, return to the active Hebrew lyric and check an
audio route change. Leave the verification job in place until the worker is
terminal, then delete only that job and verify its export URLs are gone.

For an authorized live QA run, provide a one-use Clerk ticket in a private
temporary file through `SONERELI_QA_TICKET_FILE`, the corresponding tester ID
in `SONERELI_QA_SUBJECT`, and the authorized YouTube URL in
`SONERELI_QA_YOUTUBE_URL`. Run `scripts/verify ios-live` on a simulator with
no other Sonereli account signed in. This scenario creates one paid job.
The live lane uses ad-hoc simulator signing for Keychain access. It uses
native ClerkKit and the live coordinator, and checks completion on
the WebSocket before playing both stems. Record and clean up the created
job once terminal. This scenario does not drive the YouTube app or verify
the physical Lock Screen.
