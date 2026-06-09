#!/usr/bin/env bash
# Versioned build + deploy + verify + auto-rollback for the karaoke stack.
#
# Ported from scribe-service/scripts/release-deploy.sh (release pipeline #99),
# adapted for karaoke's Dockhand-managed stack: the recreate step goes through
# the Dockhand deploy wrapper (KARAOKE_RECREATE_CMD), never `docker compose
# up/down/restart` (HARD RULE — Dockhand is the only lifecycle path). The
# `docker build` / `docker tag` / `docker image` calls are build/registry ops,
# not lifecycle ops, so they stay.
#
# The verify step asserts that the running service reports the exact version we
# just deployed (GET /health -> .version, added in #99).
#
# Usage:
#   scripts/release-deploy.sh <X.Y.Z>            # build + deploy + verify
#   scripts/release-deploy.sh --rollback <X.Y.Z> # re-point to a prior tag only
#
# The script is idempotent (re-running for the already-deployed version is a
# no-op) and single-instance (flock). No host paths or hostnames are baked in;
# the stack directory, build context, recreate command, and health URL come
# from args/env:
#
#   KARAOKE_STACK_DIR     dir holding compose.yaml + deploy.sh (Dockhand stack)
#   KARAOKE_SRC_DIR       git checkout used as the build context
#                         (default: $KARAOKE_STACK_DIR/src)
#   KARAOKE_DOCKERFILE    Dockerfile path relative to the build context
#                         (default: docker/api/Dockerfile)
#   KARAOKE_HEALTH_URL    health endpoint returning JSON `.version` (e.g.
#                         http://host:13140/health)
#   KARAOKE_RECREATE_CMD  command that recreates the stack via Dockhand
#                         (default: $KARAOKE_STACK_DIR/deploy.sh start)
#   KARAOKE_IMAGE         image repository (default: karaoke)
#   KARAOKE_KEEP_IMAGES   versioned image tags to retain after prune (default: 5)
#   KARAOKE_VERIFY_TIMEOUT  seconds to wait for health to report the version
#                           (default: 120)
#   KARAOKE_SKIP_CANARY   set to 1 to skip the post-deploy /jobs canary check
#   KARAOKE_CANARY_URL    canary endpoint (default: $KARAOKE_HEALTH_URL with a
#                         trailing /health replaced by /jobs)
#   KARAOKE_SERVICE_TOKEN machine bearer used for the /jobs canary (no token ->
#                         canary is skipped, since /jobs requires auth)
#   KARAOKE_CANARY_CMD    full override for the canary; when set it is run
#                         instead of the default curl, its exit code is the verdict
#   KARAOKE_LOCK_FILE     flock path (default: $TMPDIR/karaoke-release-deploy.lock)
set -euo pipefail

IMAGE="${KARAOKE_IMAGE:-karaoke}"
KEEP_IMAGES="${KARAOKE_KEEP_IMAGES:-5}"
VERIFY_TIMEOUT="${KARAOKE_VERIFY_TIMEOUT:-120}"
LOCK_FILE="${KARAOKE_LOCK_FILE:-${TMPDIR:-/tmp}/karaoke-release-deploy.lock}"

log()   { printf '[release-deploy] %s\n' "$*"; }
err()   { printf '[release-deploy] ERROR: %s\n' "$*" >&2; }
alert() { printf '[release-deploy] ALERT: %s\n' "$*" >&2; }

usage() {
    cat >&2 <<'EOF'
usage:
  release-deploy.sh <X.Y.Z>             build + deploy version, verify, auto-rollback on failure
  release-deploy.sh --rollback <X.Y.Z>  re-point karaoke:current to a prior tag and recreate (no build)

required env: KARAOKE_STACK_DIR, KARAOKE_HEALTH_URL
optional env: KARAOKE_SRC_DIR KARAOKE_DOCKERFILE KARAOKE_RECREATE_CMD KARAOKE_IMAGE
              KARAOKE_KEEP_IMAGES KARAOKE_VERIFY_TIMEOUT KARAOKE_SKIP_CANARY
              KARAOKE_CANARY_URL KARAOKE_SERVICE_TOKEN KARAOKE_CANARY_CMD
EOF
}

# --- argument parsing -------------------------------------------------------
MODE="deploy"
VERSION=""
case "${1:-}" in
    -h|--help|"")
        usage
        [ -n "${1:-}" ] && exit 0 || exit 2
        ;;
    --rollback)
        MODE="rollback"
        VERSION="${2:-}"
        ;;
    *)
        VERSION="$1"
        ;;
esac

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    err "version must be X.Y.Z (got '${VERSION}')"
    usage
    exit 2
fi

STACK_DIR="${KARAOKE_STACK_DIR:-}"
SRC_DIR="${KARAOKE_SRC_DIR:-${STACK_DIR%/}/src}"
DOCKERFILE="${KARAOKE_DOCKERFILE:-docker/api/Dockerfile}"
HEALTH_URL="${KARAOKE_HEALTH_URL:-}"
RECREATE_CMD="${KARAOKE_RECREATE_CMD:-${STACK_DIR%/}/deploy.sh start}"
if [ -z "$STACK_DIR" ]; then err "KARAOKE_STACK_DIR is required"; exit 2; fi
if [ -z "$HEALTH_URL" ]; then err "KARAOKE_HEALTH_URL is required"; exit 2; fi

# --- single-instance lock ---------------------------------------------------
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    err "another release-deploy holds $LOCK_FILE; refusing to run concurrently"
    exit 1
fi

# --- docker helpers (build/tag/inspect/rm — NOT lifecycle) ------------------
image_id() { docker image inspect --format '{{.Id}}' "$1" 2>/dev/null; }

image_exists() { docker image inspect "$1" >/dev/null 2>&1; }

# Versioned tags (X.Y.Z) of $IMAGE, ascending by semver.
versioned_tags() {
    local tag out=""
    while read -r tag; do
        [[ "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && out+="${tag}"$'\n'
    done < <(docker images --format '{{.Tag}}' "$IMAGE" 2>/dev/null)
    [ -n "$out" ] && printf '%s' "$out" | sort -t. -k1,1n -k2,2n -k3,3n -u
    return 0
}

# Version (X.Y.Z) that karaoke:current currently resolves to, if any.
current_version() {
    local cur
    cur="$(image_id "${IMAGE}:current")" || return 0
    [ -n "$cur" ] || return 0
    local t
    for t in $(versioned_tags); do
        if [ "$(image_id "${IMAGE}:${t}")" = "$cur" ]; then
            printf '%s' "$t"
            return 0
        fi
    done
}

# Parse `.version` out of the health endpoint JSON (no jq dependency).
health_version() {
    local body
    body="$(curl -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null)" || return 1
    printf '%s' "$body" \
        | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        | head -n1
}

# Wait until the health endpoint reports $1 as its version, or time out.
wait_for_health_version() {
    local want="$1" deadline got
    deadline=$(( $(date +%s) + VERIFY_TIMEOUT ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        got="$(health_version || true)"
        if [ "$got" = "$want" ]; then
            log "health reports version ${got}"
            return 0
        fi
        sleep 3
    done
    err "health version did not reach ${want} within ${VERIFY_TIMEOUT}s (last: '${got:-unreachable}')"
    return 1
}

# Post-deploy canary: an authed GET /jobs must return HTTP 200. Optional —
# skipped when KARAOKE_SKIP_CANARY=1 or no machine bearer is available. A full
# command override (KARAOKE_CANARY_CMD) wins when set.
canary_green() {
    if [ "${KARAOKE_SKIP_CANARY:-0}" = "1" ]; then
        log "post-deploy canary skipped (KARAOKE_SKIP_CANARY=1)"
        return 0
    fi
    if [ -n "${KARAOKE_CANARY_CMD:-}" ]; then
        log "running canary override: ${KARAOKE_CANARY_CMD}"
        if bash -c "$KARAOKE_CANARY_CMD"; then
            log "canary override green"
            return 0
        fi
        err "canary override RED"
        return 1
    fi
    local token="${KARAOKE_SERVICE_TOKEN:-}"
    if [ -z "$token" ]; then
        log "post-deploy canary skipped (no KARAOKE_SERVICE_TOKEN for the /jobs check)"
        return 0
    fi
    local canary_url code
    canary_url="${KARAOKE_CANARY_URL:-${HEALTH_URL%/health}/jobs}"
    log "running /jobs canary against ${canary_url}"
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 \
        -H "Authorization: Bearer ${token}" "$canary_url" 2>/dev/null || true)"
    if [ "$code" = "200" ]; then
        log "/jobs canary green (HTTP 200)"
        return 0
    fi
    err "/jobs canary RED (HTTP ${code:-000})"
    return 1
}

# Full verify: health reports the expected version AND the canary is green.
verify() {
    wait_for_health_version "$1" && canary_green
}

retag_current() {
    docker tag "${IMAGE}:$1" "${IMAGE}:current"
    log "${IMAGE}:current -> ${IMAGE}:$1"
}

# Recreate the stack via Dockhand (KARAOKE_RECREATE_CMD) — never `docker
# compose up/down/restart` directly.
recreate() {
    log "recreate via: ${RECREATE_CMD}"
    bash -c "$RECREATE_CMD"
}

# Keep only the newest $KEEP_IMAGES versioned tags; never drop the one
# karaoke:current points to.
prune_images() {
    local keep="$KEEP_IMAGES" protected total tags drop t
    protected="$(current_version || true)"
    tags="$(versioned_tags)"
    if [ -z "$tags" ]; then return 0; fi
    total="$(printf '%s\n' "$tags" | wc -l | tr -d ' ')"
    if [ "$total" -le "$keep" ]; then
        return 0
    fi
    drop="$(printf '%s\n' "$tags" | head -n "$((total - keep))")"
    for t in $drop; do
        [ "$t" = "$protected" ] && continue
        if docker image rm "${IMAGE}:${t}" >/dev/null 2>&1; then
            log "pruned ${IMAGE}:${t}"
        else
            log "kept ${IMAGE}:${t} (in use or removal failed)"
        fi
    done
}

# --- rollback mode ----------------------------------------------------------
if [ "$MODE" = "rollback" ]; then
    if ! image_exists "${IMAGE}:${VERSION}"; then
        err "cannot roll back to ${IMAGE}:${VERSION}: image not found"
        exit 1
    fi
    log "rollback requested -> ${VERSION}"
    retag_current "$VERSION"
    recreate
    if wait_for_health_version "$VERSION"; then
        log "rollback to ${VERSION} verified"
        exit 0
    fi
    alert "rollback to ${VERSION} FAILED to verify — manual intervention required"
    exit 1
fi

# --- deploy mode ------------------------------------------------------------
PREV_VERSION="$(current_version || true)"
log "deploying ${VERSION} (previous ${IMAGE}:current = ${PREV_VERSION:-none})"

# Idempotency: already deployed and healthy -> no-op (no rebuild, no recreate).
if image_exists "${IMAGE}:${VERSION}" \
    && [ "$(image_id "${IMAGE}:current")" = "$(image_id "${IMAGE}:${VERSION}")" ] \
    && [ "$(health_version || true)" = "$VERSION" ]; then
    log "${VERSION} already deployed and healthy — nothing to do"
    exit 0
fi

# 1. Check out the vX.Y.Z tag in the build context.
log "checking out v${VERSION} in ${SRC_DIR}"
git -C "$SRC_DIR" fetch --tags --quiet
git -C "$SRC_DIR" checkout --quiet "v${VERSION}"

# 2. Build the versioned image, retag current, prune old tags.
log "docker build -t ${IMAGE}:${VERSION} -f ${DOCKERFILE} ${SRC_DIR}"
docker build -t "${IMAGE}:${VERSION}" -f "${SRC_DIR%/}/${DOCKERFILE}" "$SRC_DIR"
retag_current "$VERSION"
prune_images

# 3. Recreate the stack (Dockhand).
recreate

# 4. Verify health version + canary.
if verify "$VERSION"; then
    log "deploy of ${VERSION} verified — ${IMAGE}:current is live"
    exit 0
fi

# 5. Auto-rollback on verify failure.
err "verify failed for ${VERSION}"
if [ -z "$PREV_VERSION" ] || ! image_exists "${IMAGE}:${PREV_VERSION}"; then
    alert "verify failed for ${VERSION} and no previous version to roll back to — runtime may be broken"
    exit 1
fi

alert "verify failed for ${VERSION} — rolling back to ${PREV_VERSION}"
retag_current "$PREV_VERSION"
recreate
if wait_for_health_version "$PREV_VERSION"; then
    alert "deploy of ${VERSION} FAILED; rolled back to last-good ${PREV_VERSION}"
    exit 1
fi
alert "deploy of ${VERSION} FAILED and rollback to ${PREV_VERSION} did NOT verify — manual intervention required"
exit 1
