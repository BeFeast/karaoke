# karaoke

Self-hosted service: take a YouTube (or any `yt-dlp`-supported) URL and split it into
isolated **vocals** and an instrumental **playback** track, transcribe the **lyrics**,
and serve in-browser playback and share links.

Coordinator runs on `devbox` (residential IP, owns `yt-dlp` + `ffmpeg`); GPU stages
(Demucs separation + Whisper lyrics) run on **ephemeral on-demand vast.ai** instances
with hard cost caps and guaranteed teardown. Auth is multi-layer (Clerk JWT, machine
bearer, trusted-LAN, `ktx_` extension tokens) — scribe parity. Live progress is pushed
over WebSocket. A Chrome extension (MV3, "Karaoke Submitter") submits the current tab.

Status: **scaffolding** — just the empty seed; implementation tracked in issues.

## Where things live

- **PRD + architecture diagram + agent contract** (canonical, in the operator's Obsidian vault):
  - `Dev/Areas/karaoke/_index.md`, `AGENTS.md`, `karaoke-prd.md`
  - `_Assets/Excalidraw/karaoke-architecture.excalidraw.md`
- **Reference history** (read-only, not running): the original `KaraokeService` prototype on
  TrueNAS Odin (`/mnt/Odin/Applications/KaraokeService`). Not a rollback target. TrueNAS keeps
  one role for this product: an NFS artifact store.
- **Real dev checkout:** `workshop:/mnt/storage/src/karaoke`. The local Mac checkout is
  reference-only.
- **Runtime stack** *(to provision)*: `devbox:/opt/stacks/karaoke/`, port `:13140`.

## Tech

- Python with `uv`. Lint `ruff`, tests `pytest`.
- FastAPI + WebSocket; Postgres (Alembic) for owners/jobs/tokens; Valkey/Redis for the queue.
- Chrome extension (MV3) under `extension/chrome/` *(to add)*.
- Android submitter app (share target + per-job YouTube cookies, CI-built debug APK)
  under `mobile/android/` — see [`docs/mobile-android.md`](docs/mobile-android.md).
- vast.ai GPU image `ghcr.io/befeast/karaoke-vast:cuda12.4` *(to add under `docker/vast/`)*.
- YouTube anti-bot: the coordinator image bundles pinned `yt-dlp`,
  `yt-dlp-ejs`, `deno`, Node/npm, and the `bgutil-ytdlp-pot-provider` yt-dlp
  plugin; downloads use backoff, and a `bgutil-ytdlp-pot-provider` sidecar
  (`karaoke-pot:4416`) supplies PO tokens. Setup, nightly canary, and rollback:
  [`docker/api/README.md`](docker/api/README.md) and
  [`docs/yt-dlp-runbook.md`](docs/yt-dlp-runbook.md).

## Live progress (WebSocket)

`WS /ws` (global broadcast) and `WS /ws/{job_id}` (per-job) push typed JSON
events — `stage_change` (`queued`/`downloading`/`separating`/`transcribing`/
`finalizing`/`completed`/`failed`), `heartbeat` (every
`KARAOKE_WS_HEARTBEAT_INTERVAL_S` seconds, default **5s**, while a stage is in
progress), `cost_update` (carries `vast_cost`; fired after vast provisioning
and on teardown), and `error`. On connect the server replays the latest known
stage/heartbeat for the job, so late subscribers see current state instantly
(`wscat -c ws://localhost:13140/ws/<job_id>`). WS is served on the same
listener as HTTP (`:13140`); `:13141` is reserved (`KARAOKE_WS_PORT`) should
the deployment split WS onto its own listener. `/jobs/{id}/status` polling
stays supported as the fallback channel. Full event schema:
[`src/karaoke/api/ws.py`](src/karaoke/api/ws.py).

## Audio-file upload

Besides a URL, a job can start from a local audio file (own recording,
purchased track): `POST /jobs/upload`, multipart fields `file` (required:
`.mp3` / `.m4a` / `.wav` / `.flac` / `.ogg`) + `title` (optional). Same auth
layers and 201 `JobOut` contract as `POST /jobs`:

```bash
curl -F 'file=@song.mp3' -H 'Authorization: Bearer <token>' \
  http://10.10.0.13:13140/jobs/upload
```

The job's `source_url` carries an `upload://<filename>` sentinel and the
pipeline skips yt-dlp entirely; metadata (artist/track/album/duration) comes
from the file's tags via `ffprobe`, feeding the same LRCLIB lyrics lookup.
Upload size is capped by `KARAOKE_MAX_UPLOAD_BYTES` (default 200 MiB; 413 when
exceeded). **The public host sits behind a Cloudflare tunnel whose free plan
hard-caps request bodies at 100 MB at the edge** — Cloudflare rejects bigger
uploads with its own 413 before they reach the app, so larger files are
LAN-only.

## Verification gate

A job is done only when `vocals.mp3`, `karaoke.mp3`, and `lyrics.txt` exist on the NFS store,
the share page plays both audio tracks, owner scoping holds for both SPA and extension
submitters, and `metadata.json` shows a real `vast_instance_id` + `vast_cost` and the
instance has been destroyed. Health-only / empty-queue / 404 screenshots are not acceptance
evidence.

## Releases

Releases use versioned images (`karaoke:X.Y.Z`) with a moving `karaoke:current`
alias. A release is cut on devbox with
[`scripts/release-deploy.sh`](scripts/release-deploy.sh) `<X.Y.Z>`: build ->
re-tag `karaoke:current` -> Dockhand recreate -> verify `GET /health` reports
the deployed version (auto-rollback on failure). `--rollback <X.Y.Z>` re-points
to a prior tag with no rebuild. Per-release notes live in
[`CHANGELOG.md`](CHANGELOG.md); rollback + the one-time compose repoint in
[`docs/runbooks/release-rollback.md`](docs/runbooks/release-rollback.md).

## License

MIT — see [`LICENSE`](LICENSE).
