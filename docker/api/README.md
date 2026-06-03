# karaoke coordinator image (`karaoke:local`)

FastAPI API + in-process worker. Built by Dockhand at `/opt/stacks/karaoke`
(`image: karaoke:local`). The coordinator runs on `devbox`'s residential IP and
owns the YouTube download (`yt-dlp`) + ffmpeg normalize; GPU stages run on
ephemeral vast.ai / RunPod — never bake CUDA/Demucs/Whisper here.

## YouTube anti-bot mitigations (issue #68)

YouTube soft-bans the residential coordinator IP under load with *"Sign in to
confirm you're not a bot"*. The image carries three mitigations:

1. **deno** (pinned, on `PATH`). yt-dlp 2026 defaults to a **deno-only** JS
   challenge-solver; the base image ships `node` but not `deno`, which degrades
   signature / n-challenge solving. The Dockerfile installs the pinned
   `DENO_VERSION` release binary into `/usr/local/bin`. `node`/`npm` are kept
   for the EJS remote-component solver. The Python environment also pins
   `yt-dlp-ejs`, so the solver package is present in the coordinator image; the
   runtime still allows `--remote-components ejs:github` for yt-dlp's upstream
   fallback path.

2. **bgutil PO-token provider** (`bgutil-ytdlp-pot-provider` yt-dlp plugin, a
   `pyproject` dependency). It auto-fetches GVS PO tokens from a provider
   **sidecar** (HTTP server mode) so YouTube web/mweb clients aren't
   skipped / 403'd. The coordinator points the plugin at the sidecar via the
   `youtubepot-bgutilhttp:base_url` extractor-arg, built from the
   `KARAOKE_POT_PROVIDER_BASE_URL` setting (default `http://karaoke-pot:4416`).
   The provider server is **not** in this image — run it as the sidecar below.

3. **Backoff/retry.** `worker/pipeline.py` retries the download with exponential
   backoff (`15s → 45s → 120s`) when yt-dlp prints a bot-check / rate-limit
   fingerprint, then surfaces a clear, actionable error. Non-bot-check failures
   (private video, network) are raised immediately — no wasted backoff.

### bgutil PO-token provider sidecar (operator: apply via Dockhand)

The provider server runs as its own service on the same docker network as the
coordinator. Add this service to `devbox:/opt/stacks/karaoke/compose.yaml` and
apply via the Dockhand API (never `docker compose up` directly):

```yaml
  # bgutil PO-token provider (HTTP server mode) for yt-dlp's bgutil plugin.
  # Generates GVS PO tokens so the coordinator's YouTube downloads stop tripping
  # the "confirm you're not a bot" check (issue #68). The coordinator reaches it
  # at http://karaoke-pot:4416 (KARAOKE_POT_PROVIDER_BASE_URL).
  karaoke-pot:
    container_name: karaoke-pot
    image: brainicism/bgutil-ytdlp-pot-provider:1.3.1
    init: true                       # provider docs recommend running with --init
    restart: unless-stopped
    expose:
      - "4416"                       # internal-only; no host port needed
    networks:
      - db-dev-net                   # same external network the coordinator joins
    logging:
      driver: json-file
      options:
        max-size: "5m"
        max-file: "3"
```

Then add `karaoke-pot` to the coordinator's `depends_on` (optional but tidy):

```yaml
  karaoke:
    depends_on:
      infisical-agent:
        condition: service_started
      karaoke-pot:
        condition: service_started
```

| Setting | Value |
|---|---|
| Service name | `karaoke-pot` |
| Image | `brainicism/bgutil-ytdlp-pot-provider:1.3.1` |
| Port | `4416` (container-internal; no host publish needed) |
| Network | `db-dev-net` (the coordinator's existing external network) |
| Coordinator env | `KARAOKE_POT_PROVIDER_BASE_URL=http://karaoke-pot:4416` (default; no env needed if unchanged) |

To **disable** PO-token fetching entirely (e.g. before the sidecar is applied),
set `KARAOKE_POT_PROVIDER_BASE_URL=` (empty) — the pipeline then omits the
bgutil extractor-arg and yt-dlp simply runs without a PO token.

Minimum yt-dlp for the plugin is `2025.05.22`; the coordinator pins the
known-good stack exactly: `yt-dlp==2026.3.17` and `yt-dlp-ejs==0.8.0`.
Dependabot groups `yt-dlp`, `yt-dlp-ejs`, and `bgutil-ytdlp-pot-provider` into
weekly bump PRs. Merge those only after CI and the deployed canary both pass.

## Nightly yt-dlp canary

`.github/workflows/yt-dlp-canary.yml` runs daily at `08:23 UTC` and can also be
started manually. It submits a real job to the deployed coordinator, then
polls `/jobs/{id}/status` until the job reaches `separating`, `transcribing`, or
`completed`. That proves the coordinator-side `yt-dlp` download succeeded on
the residential-IP devbox without requiring the canary to wait for the full GPU
pipeline. After the check passes it cancels the job if it is still non-terminal.

Required GitHub secret:

| Name | Purpose |
|---|---|
| `KARAOKE_CANARY_BASE_URL` | Public coordinator base URL, for example `https://karaoke.example.com` |
| `KARAOKE_CANARY_SERVICE_TOKEN` | Machine bearer matching `KARAOKE_SERVICE_TOKEN` |

Optional GitHub vars:

| Name | Default |
|---|---|
| `KARAOKE_CANARY_URLS` | `https://www.youtube.com/watch?v=BaW_jenozKc` |
| `KARAOKE_CANARY_TIMEOUT_SECONDS` | `900` |
| `KARAOKE_CANARY_POLL_SECONDS` | `10` |

On failure the workflow opens or comments on an open issue titled
`yt-dlp canary failure`, then fails the workflow run.

## Rollback when upstream yt-dlp breaks YouTube

1. Find the last green canary run and its deployed git SHA / image.
2. Pin `yt-dlp` and, if needed, `yt-dlp-ejs` in `pyproject.toml` back to that
   last-known-good version. Do not use a lower-bound specifier.
3. Run `uv lock`, then the repo verification gate.
4. Commit and open a PR with `Refs #17` plus the failing canary link.
5. After merge, let the operator rebuild/redeploy the Dockhand
   `karaoke-worker` / coordinator image. Do not deploy from an agent session.
6. Re-run the `yt-dlp canary` workflow manually. Keep the tracking issue open
   until the deployed canary is green.

Never add `yt-dlp`, `yt-dlp-ejs`, `deno`, Node, or cookies to the RunPod/vast
GPU images; the coordinator owns all source downloads.

## Build / lifecycle

Built and started by Dockhand. See the repo `AGENTS.md` "Deployment And Runtime
Refresh" for the canonical (Dockhand-only) lifecycle path.

The Dockerfile keeps image rebuilds fast by installing OS-level JS/runtime
dependencies (`nodejs`, `npm`, pinned `deno`) before the Python dependency
layer (`uv sync --frozen --no-dev --no-install-project`). Bumping application
code therefore reuses the Node/Deno/EJS layers; bumping only Python deps
invalidates the dependency layer but not the JS runtime layer.
