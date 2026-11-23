# AGENTS.md — BeFeast/karaoke

Code facts for agents working inside this repo. Process, roles, hosts, release approval and runtime operations live in the private vault area `Dev/Areas/karaoke/AGENTS.md`; it wins on process. Development happens on Forgejo; the GitHub copy exists for the `gpu-image` workflow.

## Purpose and layout

Karaoke takes a YouTube (or any `yt-dlp`-supported) URL or an uploaded audio file, splits it into **vocals** and an instrumental **playback** track, produces synced **lyrics**, and serves in-browser playback and share links. A coordinator (FastAPI, queue, Postgres) downloads and normalizes audio; GPU stages (separation, transcription, alignment) run on RunPod Serverless, with vast.ai as a fallback only. Progress is pushed over WebSocket.

```
src/karaoke/api/      FastAPI app, routes, auth layers, WebSocket, SPA static serving
src/karaoke/worker/   pipeline, scheduler, RunPod/vast clients, R2 transfer, lyrics sources and alignment
src/karaoke/db/       models; migrations live in alembic/
web/spa/              submitter and player SPA (Vite, React, bun)
extension/chrome/     MV3 submitter extension (bun)
mobile/android/       Android share-target submitter (Gradle)
docker/               api, runpod and vast images
scripts/              release-deploy.sh, runpod_provision.py, canaries, lyrics tools
docs/                 runbooks (release and rollback, yt-dlp, per-job cookies), mobile notes
```

## Commands

Durations are from Forgejo CI (2026-09-29 to 2026-10-29); local runs not measured.

| What | Command | Where | Time |
|---|---|---|---|
| Setup | `uv sync --frozen --all-extras --all-groups` (Python 3.12) | anywhere | not measured |
| Lint and tests | `uv run ruff check src tests && uv run pytest -q` | local or CI job `lint-test` | 54–191 s |
| Migrations | `uv run alembic upgrade head` against a throwaway Postgres | CI job `migrations` | 56–62 s |
| SPA | `cd web/spa && bun install --frozen-lockfile && bun run typecheck && bun run test && bun run guards && bun run build` | local or CI job `spa` | ~12 s |
| Extension | `cd extension/chrome && bun install --frozen-lockfile && bun test` | local or CI jobs `extension`, `extension-version-guard` | 7–14 s |
| Android | `cd mobile/android && ./gradlew --no-daemon testDebugUnitTest lintDebug assembleDebug` | CI job `android` (skips itself when Android files are untouched) | 8–149 s |
| GPU image | `gpu-image` workflow (`.github/workflows/gpu-image.yml`) | CI only | not measured |

Use `uv` for Python and `bun` for Node; do not switch to `pip`, `npm` or `npx`.

## Merge gate

Run `scripts/verify <scenario>|all` for browser and real-job acceptance. See [docs/verify.md](docs/verify.md).

Workflow `ci` (`.github/workflows/ci.yml`, run by Forgejo Actions) on the PR head: `lint-test`, `migrations`, `extension-version-guard`, `extension`, `spa`, `android`. No branch protection is configured on Forgejo. Releases are separate from merges: a version bump PR (`pyproject.toml`, `uv lock`, `CHANGELOG.md`), then a tag and release `vX.Y.Z`, then a deploy approved in the vault area. Auth, owner scoping, per-job cookies, the release script and migrations get one async review by another model family.

## Hard rules

1. **yt-dlp runs on the coordinator** (residential IP) with a JS runtime, the EJS remote-component solver and per-job cookies. Datacenter GPU hosts cannot download from YouTube reliably; the GPU images carry no yt-dlp.
2. **GPU stages run only on ephemeral serverless or on-demand GPU workers**, never on a fixed GPU host. A vast.ai instance is always destroyed in `finally`, with its cost recorded and per-job and daily caps honored.
3. **Multi-layer auth stays**: public, trusted LAN, machine bearer, Clerk user and `ktx_` extension tokens. Jobs are owner-scoped; share links are owner-aware and unlisted per-job tokens. Never regress to a single bearer.
4. **WebSocket is the canonical progress channel**; `/status` polling stays supported, and the worker pushes an event on every stage transition.
5. **Deploys recreate the stack through Dockhand** (`scripts/release-deploy.sh`), never `docker compose up/down/restart`. `docker build` and `docker tag` are allowed.
6. **A green CI or `/health` is not a shipped job.** Runtime acceptance needs a real job with stems and lyrics stored and a share page that plays.
7. PR bodies use `Refs #N` or `Implements #N` unless every acceptance criterion, runtime ones included, is proven.

## Public-repo hygiene

The repo is public. Never commit secrets, cookies, tokens, `.env` files, artifacts, internal hostnames, IP addresses or absolute personal paths. Runtime secrets come from Infisical at runtime and are never baked into images.
