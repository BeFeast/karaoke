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
   for the EJS remote-component solver.

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

Minimum yt-dlp for the plugin is `2025.05.22`; the coordinator pins
`yt-dlp>=2026.3.17`.

## Build / lifecycle

Built and started by Dockhand. See the repo `AGENTS.md` "Deployment And Runtime
Refresh" for the canonical (Dockhand-only) lifecycle path.
