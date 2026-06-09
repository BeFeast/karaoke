# AGENTS.md — `BeFeast/karaoke` (repo)

Repo-side instructions for Codex CLI / Claude Code / Cline / Gemini CLI working on the
**karaoke** codebase. The full project-manager operating contract lives in the operator's
Obsidian vault at `Dev/Areas/karaoke/AGENTS.md`; this file is the runtime contract for an
agent working **inside this repo**.

## What this product is

Karaoke takes a YouTube (or any `yt-dlp`-supported) URL and splits it into isolated
**vocals** and an instrumental **playback** track, transcribes the **lyrics**, and serves
in-browser playback and share links. Coordinator on `devbox` (residential IP); GPU stages
on **ephemeral vast.ai** with hard cost caps + guaranteed teardown; multi-layer auth
(Clerk JWT, machine bearer, trusted-LAN, `ktx_` extension tokens) — scribe parity; live
progress over WebSocket; a Chrome MV3 extension submits the current tab.

## Where to work

- **Real dev checkout:** `workshop:/mnt/storage/src/karaoke`. Edit/build/test/commit there.
- **Mac checkout:** reference-only. Read, `rg`, `git show`, diffs — never `uv sync` or
  build locally unless explicitly asked.
- **Runtime:** Dockhand stack `devbox:/opt/stacks/karaoke/` (`:13140`) once provisioned.
  GitHub `origin/main` and CI green is **not** proof a job actually ran. See the PRD's
  verification gate before claiming "done".
- **Artifacts:** TrueNAS Odin NFS share, mounted into the devbox stack via docker's NFS
  volume driver. No artifact data ever lives in this repo.

If `workshop:/mnt/storage/src/karaoke` and the Mac checkout disagree, **workshop is
authoritative**.

## Collaboration defaults

- Communicate with the operator (Oleg) in Russian, with English for technical terms verbatim.
- Code, comments, commit messages, PR titles/bodies stay in English.
- Use `uv` for Python (`uv run`, `uv sync`, `uvx`) and `bun` for Node/extension work
  (`bun install`, `bunx`). Don't switch to `pip`/`python`/`npm`/`node`/`npx`.
- Be direct; don't ask "should we stop?" — the operator will say.

## Hard rules (preserve as you implement)

1. **`yt-dlp` lives on devbox** (residential IP), with a Node JS runtime + EJS remote-component
   solver. Datacenter IPs (vast.ai hosts) cannot reliably download from YouTube.
2. **GPU stages live on vast.ai only.** Demucs separation **and** faster-whisper lyrics run
   inside a single ephemeral vast.ai instance per job. No fixed GPU host.
3. **Always destroy the vast instance in `finally`.** Record `vast_instance_id` + `vast_cost`
   on the job. Honor per-job + rolling daily cost caps; refuse the job rather than overrun.
4. **Multi-layer auth.** Don't regress to a single bearer token. Keep `public` /
   `trusted_lan` / `machine_bearer` / `clerk_user` + extension tokens (`ktx_…`). Jobs are
   owner-scoped; share links are both owner-aware and unlisted-token-aware.
5. **WebSocket is the canonical progress channel.** `/status` polling stays supported but
   the worker MUST push WS events on every stage transition.
6. **No secrets in the repo or images.** Read from Infisical (`services/prod/karaoke`) at
   runtime — service token, Clerk publishable + secret + JWKS issuer, Postgres URL,
   vast.ai API key, public base URL.

## Verification gate (before a PR)

```bash
git fetch origin
git rebase origin/main
uv sync --frozen --all-extras --all-groups
uv run ruff check src tests
uv run pytest -q
# (Alembic check + extension build commands to be added as those parts land.)
```

PR bodies use non-auto-closing references (`Refs #N` / `Implements #N`) unless the issue is
purely code/docs and every acceptance criterion — including runtime/deploy verification —
is proven. Health-only / empty-queue / 404 screenshots do not satisfy a runtime acceptance
criterion.

## Releases & deploy

Versioned-image release pipeline (#99), ported from scribe-service and adapted
for the Dockhand-managed stack.

- **Version surface:** `GET /health` returns `{"status":"ok","version":"X.Y.Z"}`,
  read from the installed package metadata (`importlib.metadata.version("karaoke")`,
  driven by `pyproject.toml`). The release-deploy verify step polls it.
- **`scripts/release-deploy.sh <X.Y.Z>`** (runs on devbox): check out `vX.Y.Z`,
  `docker build` -> `karaoke:X.Y.Z`, re-tag the served alias `karaoke:current`,
  prune to keep-last-5, recreate via Dockhand, then verify `/health` version +
  an optional authed `GET /jobs` canary; auto-rollback to the prior tag on
  failure. `--rollback X.Y.Z` re-points without a rebuild. Idempotent + flock.
- **HARD RULE:** the script (and any deploy) MUST recreate through Dockhand
  (`KARAOKE_RECREATE_CMD`, default `/opt/stacks/karaoke/deploy.sh start`), never
  `docker compose up/down/restart`. `docker build` / `docker tag` are allowed.
- **Compose repoint (deploy-time, on devbox -- not in this repo):** the stack
  `compose.yaml` must serve `image: karaoke:current` instead of building
  `karaoke:local`, so deploy/rollback are re-tag + recreate. See
  [`docs/runbooks/release-rollback.md`](docs/runbooks/release-rollback.md).
- **CHANGELOG.md** carries one `vX.Y.Z` section per release, generated from the
  merged PR titles in the tag range.
- **Maestro auto-release (staged):** the operator's
  `~/.maestro/maestro.d/karaoke.yaml` carries a staged (disabled) `versioning:`
  + `deploy_cmd` block mirroring scribe's. When enabled, every merge auto-bumps
  `pyproject.toml`, tags, releases, and deploys via the script. Until then,
  releases are cut manually with `scripts/release-deploy.sh`.

## More context

- Full PRD, architecture diagram, surfaces table, and provisioning status:
  the operator's Obsidian vault, `Dev/Areas/karaoke/`. That is the canonical project-manager
  control room — this `AGENTS.md` is the slim repo-side version.
- Read-only history: the original `KaraokeService` prototype on TrueNAS Odin
  (`/mnt/Odin/Applications/KaraokeService`). Not running, not a rollback. Mine it for
  working pipeline patterns (yt-dlp flags, Demucs wrapper, share-page HTML, timeouts) when
  porting equivalent logic into this repo.
