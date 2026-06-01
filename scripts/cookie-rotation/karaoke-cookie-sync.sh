#!/bin/sh
# Karaoke YouTube cookie auto-rotation supplier (BeFeast/karaoke issue #10).
#
# Re-exports the logged-in Firefox YouTube/Google session cookies and POSTs the
# Netscape jar to the coordinator's /cookies/youtube endpoint, so session-gated
# videos keep downloading after YouTube rotates the session — with no manual step.
#
# Security: logs COUNTS ONLY. Never logs the bearer token or any cookie value.
# The token is read from a 0600 file (or KARAOKE_COOKIE_TOKEN env for one-shot
# tests) and passed to curl via a 0600 --config file so it never appears in argv
# (ps). The cookie jar lives in a private mktemp file removed on exit.
set -eu

BASE_URL="${KARAOKE_BASE_URL:-http://10.10.0.13:13140}"
TOKEN_FILE="${KARAOKE_COOKIE_TOKEN_FILE:-$HOME/.config/karaoke/cookie-sync.token}"
YTDLP="${YTDLP:-/usr/local/bin/yt-dlp}"
PROBE_URL="${KARAOKE_PROBE_URL:-https://www.youtube.com/watch?v=8zCEVoMZmr0}"
LOG="${KARAOKE_COOKIE_LOG:-$HOME/Library/Logs/karaoke-cookie-sync.log}"

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
log() { printf '%s %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$*" >> "$LOG"; }

# --- token: env override (one-shot tests) else 0600 file ---
TOKEN="${KARAOKE_COOKIE_TOKEN:-}"
if [ -z "$TOKEN" ]; then
  if [ ! -f "$TOKEN_FILE" ]; then log "FAIL no token file at $TOKEN_FILE"; exit 1; fi
  TOKEN="$(cat "$TOKEN_FILE")"
fi
if [ -z "$TOKEN" ]; then log "FAIL empty token"; exit 1; fi

# Work in a private temp dir. The jar path MUST NOT pre-exist: yt-dlp's
# --cookies flag loads an existing file first, and an empty file fails its
# Netscape check ("does not look like a Netscape format cookies file") and it
# then refuses to write. A fresh path inside a 0700 dir avoids that.
WORKDIR="$(mktemp -d -t karaoke-cookie)"
chmod 700 "$WORKDIR"
JAR="$WORKDIR/jar.txt"
CFG="$WORKDIR/curl.cfg"
RESP_BODY="$WORKDIR/resp.txt"
trap 'rm -rf "$WORKDIR"' EXIT INT TERM

# --- dump the logged-in Firefox cookie jar ---
# --playlist-items 0 avoids a heavy network extraction; yt-dlp still writes the
# browser-derived cookies to --cookies. Stderr/stdout discarded (may warn).
"$YTDLP" --cookies-from-browser firefox --cookies "$JAR" \
  --simulate --skip-download --playlist-items 0 "$PROBE_URL" >/dev/null 2>&1 || true
[ -f "$JAR" ] && chmod 600 "$JAR"

if [ ! -s "$JAR" ]; then
  log "FAIL no cookie jar written (Firefox profile unreadable / not present)"
  exit 1
fi
YT="$(grep -c 'youtube.com' "$JAR" 2>/dev/null || true)"
YT="${YT:-0}"
if [ "$YT" -lt 1 ]; then
  log "FAIL jar has no youtube.com cookies (Firefox not logged in to YouTube?)"
  exit 1
fi

# --- POST to the coordinator (token via -K config, never in argv) ---
printf 'header = "Authorization: Bearer %s"\n' "$TOKEN" > "$CFG"
CODE="$(curl -s -K "$CFG" -o "$RESP_BODY" -w '%{http_code}' \
  -X POST -H 'Content-Type: text/plain; charset=utf-8' \
  --data-binary @"$JAR" --max-time 30 "$BASE_URL/cookies/youtube" 2>/dev/null)" \
  || { log "FAIL curl transport error (coordinator unreachable?)"; exit 1; }

# Response body is counts only (accepted/cookies/youtube_cookies/bytes) — safe.
BODY="$(tr -d '\n' < "$RESP_BODY" 2>/dev/null || true)"
if [ "$CODE" = "200" ]; then
  log "OK http=$CODE youtube_in_jar=$YT resp=$BODY"
  exit 0
fi
log "FAIL http=$CODE resp=$BODY"
exit 1
