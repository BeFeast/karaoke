# karaoke

Self-hosted service: take a YouTube (or any `yt-dlp`-supported) URL and split it into
isolated **vocals** and an instrumental **playback** track, transcribe the **lyrics**,
and serve in-browser playback and share links.

Coordinator runs on `devbox` (residential IP, owns `yt-dlp` + `ffmpeg`); GPU stages
(Demucs separation + Whisper lyrics) run on **ephemeral on-demand vast.ai** instances
with hard cost caps and guaranteed teardown. Auth is multi-layer (Clerk JWT, machine
bearer, trusted-LAN, `ktx_` extension tokens) — scribe parity. Live progress is pushed
over WebSocket. A Chrome extension (MV3, "Karaoke Submitter") submits the current tab.

Status: API/worker scaffolding plus a CPU-local development pipeline. Production
GPU jobs are still tracked in follow-up issues.

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
- vast.ai GPU image `ghcr.io/befeast/karaoke-vast:cuda12.4` *(to add under `docker/vast/`)*.
- YouTube anti-bot: the coordinator image bundles pinned `yt-dlp`,
  `yt-dlp-ejs`, `deno`, Node/npm, and the `bgutil-ytdlp-pot-provider` yt-dlp
  plugin; downloads use backoff, and a `bgutil-ytdlp-pot-provider` sidecar
  (`karaoke-pot:4416`) supplies PO tokens. Setup, nightly canary, and rollback:
  [`docker/api/README.md`](docker/api/README.md) and
  [`docs/yt-dlp-runbook.md`](docs/yt-dlp-runbook.md).

## CPU-local development loop

M0 includes a local end-to-end runner for mining and validating the prototype
pipeline without the API, database, WebSocket progress, auth, RunPod, or vast.ai:

```bash
uv sync --frozen --all-extras --all-groups
uv run karaoke run "https://www.youtube.com/watch?v=..." --output-dir ./artifacts/test-job
```

The local runner expects the media tools to be available on the workstation:

- `yt-dlp` from the pinned project dependency, with the same coordinator flags
  used by the worker: YouTube player-client extractor args, optional bgutil PO
  token provider, optional per-call cookies, and the EJS remote-component solver.
- A JavaScript runtime for the EJS solver (`node`/`deno`, matching the
  coordinator image setup).
- `ffmpeg` on `PATH`.
- `demucs` on `PATH`; the prototype `remove-vocals.sh` behavior is ported to
  Python as `demucs --two-stems vocals -n htdemucs --device cpu`.
- `faster-whisper` importable in the local environment for CPU transcription.

The default device is intentionally `cpu-local`; any other `--device` is
rejected by this CLI path. The production worker remains responsible for
ephemeral GPU execution.

The per-job artifact tree matches PRD §6 for the local runner:

```text
<job>/
  source/
    source.mp3
  stems/
    htdemucs/
      source/
        vocals.mp3
        no_vocals.mp3
  exports/
    karaoke.mp3
    vocals.mp3
  lyrics/
    lyrics.txt
  logs/
    worker.log
  metadata.json
```

`metadata.json` records `"device": "cpu-local"` and does not include
`vast_instance_id` or `vast_cost`.

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
