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

## Provisioning Status

- **Infisical runtime path:** live in project `services`
  (`5b5038c7-46d5-496f-bfa6-6184cb41e143`), environment `prod`, path `/karaoke`.
  The path is the source of runtime secrets for a clean container boot.
- **Infisical key list:** `KARAOKE_SERVICE_TOKEN`, `KARAOKE_PUBLIC_BASE_URL`,
  `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`, `CLERK_JWKS_ISSUER`, `DATABASE_URL`,
  and `VAST_API_KEY`.
- **Clerk mirror strategy:** `services/prod/karaoke/CLERK_*` manually mirrors the shared
  scribe Clerk app values. On Clerk key rotation, re-copy the current scribe values into
  karaoke and verify `/karaoke` before restarting the stack.
- **Vast mirror strategy:** `services/prod/karaoke/VAST_API_KEY` mirrors the same
  `external/prod/...` source used by scribe; it remains a runtime-only secret.

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

## More context

- Full PRD, architecture diagram, surfaces table, and provisioning status:
  the operator's Obsidian vault, `Dev/Areas/karaoke/`. That is the canonical project-manager
  control room — this `AGENTS.md` is the slim repo-side version.
- Read-only history: the original `KaraokeService` prototype on TrueNAS Odin
  (`/mnt/Odin/Applications/KaraokeService`). Not running, not a rollback. Mine it for
  working pipeline patterns (yt-dlp flags, Demucs wrapper, share-page HTML, timeouts) when
  porting equivalent logic into this repo.
