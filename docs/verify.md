# Browser acceptance

Run `scripts/verify <scenario>|all [--target prod|URL] [--reuse-job ID]` from a
checkout with `uv`. Scenario names match the files in [acceptance](../acceptance).
For example:

```sh
scripts/verify mobile-share-player --target prod --reuse-job 435
scripts/verify all --target prod
```

Set `KARAOKE_VERIFY_CDP_URL` to the trusted QA Chrome's HTTP debugging origin
(the resident profile on port 18811 on the CDP LXC). The verifier creates and
disposes its own browser context and tab for each profile. Both phone profiles
use a 390×844 CSS viewport and CDP touch input. The iPhone profile emulates the
Safari user agent in Chrome, so it covers layout and gestures under emulation.
A WebKit-specific check requires Safari on a device.

Use an authenticated Infisical CLI for the `services` project's `prod /karaoke`
folder. Set `KARAOKE_INFISICAL_PROJECT_ID` when the CLI lacks project context. The
verifier reads `KARAOKE_SERVICE_TOKEN` at runtime. It also accepts that variable
if an Infisical launcher already provided it. The SPA recognizes Clerk and
trusted LAN at startup. The submit scenario boots its LAN shell in the
isolated test tab when the coordinator reports a public client. The real
coordinator verifies the machine owner through `/me` and receives the bearer
only on same-origin owner API requests. This flow exercises the mobile Booth
and the machine owner path. Clerk sign-in has its own flow.

The share scenarios find a retained completed GPU job with synced lyrics for the
scenario's song. `--reuse-job` selects a specific completed fixture for each
share scenario in the run. Run the Hebrew scenario separately when using a
different fixture. The verifier checks the public link and the anonymous Stage
player separately. The `long-lines` scenario uses a pinned Hebrew patter song.
It resolves the share token from the coordinator at runtime, seeks to each
sung line to check font size and width, then plays through the longest line
to check wrapping, visibility and word timing on the share page, Stage and
Performance.

The submit scenario opens the mobile Booth, taps the real
submit button, verifies the Booth's WebSocket through completion, then taps the
new job's **Sing** link. It always creates a fresh job and consumes the
deployed coordinator's GPU budget. You can set `KARAOKE_CANARY_URLS` to use
its first entry. Choose a song with vocals and usable LRC. The default is
the repo's known YouTube preflight song, since the Forgejo canaries use short
download and GPU probes.

The standalone command writes `result.json`, console logs and before/after
screenshots under the printed temporary directory. Individual assertions show
actual and expected values. Any failed scenario produces a nonzero exit.
`KARAOKE_VERIFY_JOB_TIMEOUT_SECONDS` and `KARAOKE_VERIFY_CLEANUP_GRACE_SECONDS`
default to 900 seconds each. Cleanup targets only the new machine-owner job,
runs after success or failure, and waits for worker completion before
deleting the job and its exports through the API. After the grace period it
cancels and deletes, and flags that the worker's shutdown could not be
confirmed. The HTTP cleanup check proves that the API can no longer serve the
job or exports. A coordinator-side write after cancellation can still leave
orphan files ([#337](https://git.oklabs.uk/BeFeast/karaoke/issues/337)).

## Feedback acceptance

The feedback scenario opens a public share page at 390×844 and desktop sizes,
sends empty feedback twice per browser context, and checks the multipart body
and success receipt. Its default run intercepts feedback POSTs in the isolated
tab. No tracker issues are created. To create exactly one real canary issue
from the Android phone profile after the mocked checks pass, run:

```sh
scripts/verify feedback --target prod --reuse-job 435 --live-feedback
```

The SPA uses the public `/config` setting `feedback_base_url`. Set
`KARAOKE_FEEDBACK_BASE_URL` on the coordinator to override the default
`https://pomoi.co`. The configured origin must allow browser feedback requests
from the target app's origin.

## Song catalog acceptance

For `catalog-ready`, `catalog-miss`, and `catalog-shared-processing`, set
`KARAOKE_VERIFY_CLERK_TICKET_COMMAND` to the path of an executable that
prints a fresh, one-use Clerk sign-in ticket for QA user `1` or `2` when
called with that number. The verifier opens isolated browser contexts and
signs in through the deployed Clerk SDK. Use two distinct testing accounts
with permission to submit private jobs. Tickets and job tokens are
redacted from output. Never commit the ticket executable's credentials.

Publish at least one reviewed pilot recording first, using
[`catalog/README.md`](../catalog/README.md). The ready scenario checks both
mobile profiles, shelves and full shelf links, search, one-tap Stage, and
the owner link created by submitting an equivalent URL. The shared scenario
checks two Clerk owners and verifies that a guest receives neither exports
nor a public share. The private miss scenario creates one real GPU job and
deletes it on exit. To override the default private source, set
`KARAOKE_VERIFY_CATALOG_MISS_URL` to a public single video outside the
catalog that is suitable for processing and has audible lyrics.

`catalog-pilot` exercises the actual import, admission and pause logic in
an isolated database with a fixed clock and stub providers. It starts no
live precompute jobs.
