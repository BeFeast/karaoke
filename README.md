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
- vast.ai GPU image `ghcr.io/befeast/karaoke-vast:cuda12.4` *(to add under `docker/vast/`)*.

## Verification gate

A job is done only when `vocals.mp3`, `karaoke.mp3`, and `lyrics.txt` exist on the NFS store,
the share page plays both audio tracks, owner scoping holds for both SPA and extension
submitters, and `metadata.json` shows a real `vast_instance_id` + `vast_cost` and the
instance has been destroyed. Health-only / empty-queue / 404 screenshots are not acceptance
evidence.

## License

MIT — see [`LICENSE`](LICENSE).
