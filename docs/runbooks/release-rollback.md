# Release rollback

How karaoke releases are tagged on the devbox, and how to roll back to a
previous version in one command. Part of the release pipeline (#99); pairs with
[`scripts/release-deploy.sh`](../../scripts/release-deploy.sh).

karaoke's stack is **Dockhand-managed** — `docker compose up/down/restart`
against it is a HARD RULE violation. Every recreate below goes through the
Dockhand deploy wrapper (`/opt/stacks/karaoke/deploy.sh start`, which POSTs to
the Dockhand API). `docker build` / `docker tag` / `docker image` are
build/registry ops, not lifecycle ops, so they are fine to run directly.

## Image tagging

`release-deploy.sh` builds the app image once and tags it twice:

- `karaoke:<version>` — an immutable, per-release tag (e.g. `karaoke:0.1.0`).
  This is the durable artifact you roll back *to*.
- `karaoke:current` — a moving alias re-pointed to the version being deployed.

A deploy is therefore "re-tag `karaoke:current` → recreate the container via
Dockhand → verify `/health` reports the deployed version"; a rollback is the
same operation aimed at an older `karaoke:<version>` tag, with no rebuild.

## Required compose change (one-time, deploy-time — NOT yet applied)

Today the devbox stack `compose.yaml` **builds** `./src` and serves
`image: karaoke:local`:

```yaml
services:
  karaoke:
    build:
      context: ./src
      dockerfile: docker/api/Dockerfile
    image: karaoke:local
```

For the release model, the serving service must pin the **stable versioned
alias** `karaoke:current` (which `release-deploy.sh` re-points), so a deploy or
rollback is "re-tag + recreate" with no rebuild:

```yaml
services:
  karaoke:
    image: karaoke:current        # served tag; release-deploy.sh re-points it
    # `build:` removed from the served service. Dev rebuilds still work via
    # `docker build -t karaoke:local -f docker/api/Dockerfile ./src` and a
    # `docker tag karaoke:local karaoke:current` for a local smoke test.
```

> ⚠️ This compose edit is **not** part of the repo (the stack `compose.yaml`
> lives only on `devbox:/opt/stacks/karaoke/`). Apply it on devbox via the
> Dockhand API (`PUT /api/stacks/karaoke/compose?env=1`) — never hand-edit and
> `docker compose up`. Until it is applied, run the **first** scripted release
> by seeding `karaoke:current` once: build `karaoke:0.1.0`, `docker tag
> karaoke:0.1.0 karaoke:current`, then repoint compose.

## Retention (keep-last-5)

`release-deploy.sh` keeps the **5 most recent** `karaoke:<version>` tags and
prunes older ones after a successful deploy (`KARAOKE_KEEP_IMAGES`, default 5).
This bounds disk use while guaranteeing the last few releases are always
available to roll back to.

List the release tags currently retained (newest first):

```sh
docker images karaoke --format '{{.Tag}}\t{{.CreatedAt}}' \
  | grep -vE '^(current|local)\b' | sort -r
```

Roll back only to a tag that still appears in that list. Tags pruned by the
keep-last-5 policy must be rebuilt from the matching git ref before they can be
deployed again.

## Roll back in one command

The script's `--rollback` mode re-points `karaoke:current` at a prior tag and
recreates via Dockhand (no rebuild):

```sh
KARAOKE_STACK_DIR=/opt/stacks/karaoke \
KARAOKE_HEALTH_URL=http://10.10.0.13:13140/health \
  /opt/stacks/karaoke/scripts/release-deploy.sh --rollback 0.1.0
```

(Substitute the host/port via env; nothing is baked into the script.) It will
re-tag, run `/opt/stacks/karaoke/deploy.sh start`, then poll `/health` until it
reports `0.1.0`.

Manual equivalent (same two steps), if you need to do it by hand:

```sh
docker tag karaoke:0.1.0 karaoke:current     # re-point the served alias
/opt/stacks/karaoke/deploy.sh start          # Dockhand recreate (NOT compose up)
```

## Verify

```sh
# Which image is the running container actually on?
docker inspect --format '{{.Config.Image}}' karaoke

# App is up and reports the rollback target.
curl -fsS http://10.10.0.13:13140/health
```

Confirm the reported `.version` matches the rollback target before declaring
the rollback done. (`release-deploy.sh` does this automatically and ALERTs on a
mismatch.)

## Roll forward

Re-pointing `karaoke:current` back at the newer `karaoke:<version>` tag and
recreating restores the previous release — the forward image is still present
as long as it is within the keep-last-5 window:

```sh
docker tag karaoke:<newer> karaoke:current
/opt/stacks/karaoke/deploy.sh start
```

## Cutting a release

`release-deploy.sh <X.Y.Z>` does the full path on devbox: check out the
`vX.Y.Z` tag in the build context, `docker build` → `karaoke:X.Y.Z`, re-tag
`karaoke:current`, prune old tags, Dockhand-recreate, then verify `/health`
version + the optional authed `GET /jobs` canary, auto-rolling back to the
prior versioned image on any failure. Required env: `KARAOKE_STACK_DIR`,
`KARAOKE_HEALTH_URL`. See the script header for the full env contract.

Once Maestro is unpaused with the staged `versioning:` / `deploy_cmd` block
(see the operator's `~/.maestro/maestro.d/karaoke.yaml`), each merged PR
auto-bumps `pyproject.toml`, tags `vX.Y.Z`, cuts a GitHub release, and runs
this deploy. Until then, cut releases manually with the script.
