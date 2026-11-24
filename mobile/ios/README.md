# Sonereli for iPhone

Sonereli uses the public coordinator at `https://sonereli.oklabs.uk`. The
app reads the coordinator's Clerk publishable key from `/config`, signs in
with ClerkKit and attaches a session JWT to each owner API request. The
coordinator keeps ownership and artifact handling on the existing API.

## Share from YouTube

1. In YouTube, open a video and choose **Share → Sonereli**. Use **More** if
   Sonereli is in the additional destinations on the iOS share sheet.
2. Tap **Готово**, then open Sonereli. The share extension saves the URL in
   the shared App Group. iOS share extensions return to the source app.
3. Sign in when prompted and confirm **Подготовить песню**. After completion,
   choose **Sing**. The song also appears in **Мои песни**.

The Stage keeps both stems on one audio timeline. The backing track starts at
full volume and vocals start muted. Raise the vocals slider to hear both.
The media controls on the Lock Screen use the same playback position as the
app. When the Stage closes, its temporary audio files are removed.

## Simulator verification

On a Mac with Xcode 26 or later and an iPhone simulator runtime:

```sh
scripts/verify ios
scripts/verify ios-stage
scripts/verify ios-share
```

The first command runs a Release simulator build and both Debug XCUITests.
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
