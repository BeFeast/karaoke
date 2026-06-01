# Cookie auto-rotation — headless operator supplier (prisma cron)

Keeps the coordinator's logged-in YouTube `cookies.txt` fresh **without a
browser or the Chrome extension**, for the self-host operator. A `launchd`
agent on a Mac (`prisma`) that holds a logged-in YouTube session in **Firefox**
re-exports the cookie jar every 6h and `POST`s it to the coordinator's
`POST /cookies/youtube` endpoint (atomic write + last-known-good — see
[`../../docs/cookie-rotation.md`](../../docs/cookie-rotation.md)).

This is the **operator path** (issue #10). The Chrome extension (Phase 1)
remains the path for end users — their cookies refresh as a side effect of
submitting videos.

## Why Firefox + yt-dlp (not Chrome)

`yt-dlp --cookies-from-browser firefox` reads Firefox's `cookies.sqlite`
directly, even when Firefox is closed, and Firefox cookie values are **not**
encrypted with the macOS keychain — so the export is fully headless (no GUI, no
keychain prompt). Chrome on macOS encrypts its cookies with the keychain, which
needs an unlocked GUI session, so it is unsuitable for a cron.

## Files

- `karaoke-cookie-sync.sh` — exports the Firefox jar and `POST`s it. Reads the
  bearer token from `~/.config/karaoke/cookie-sync.token` (`0600`) or the
  `KARAOKE_COOKIE_TOKEN` env (one-shot tests). The token is passed to `curl`
  via a `0600` `--config` file so it never appears in `ps`/argv. Logs **counts
  only** to `~/Library/Logs/karaoke-cookie-sync.log` — never the token or any
  cookie value. Writes the jar into a fresh `mktemp -d` dir (a pre-existing
  empty file makes `yt-dlp --cookies` refuse to write).
- `uk.labs.ok.karaoke-cookie-sync.plist` — `launchd` agent: 6h `StartInterval`
  + `RunAtLoad`.
- `mint-cookie-token.py` — mints a scoped `ktx_` token (run inside the
  coordinator container; see below).

## 1. Mint a scoped `ktx_` token (recommended)

Use an extension-class token, **not** the machine bearer: its blast radius is
limited to cookie rotation + its own owner-scoped jobs
(`AuthState.extension_token`), never machine-admin. There is no mint HTTP
endpoint — `mint-cookie-token.py` inserts a row directly, storing only the
SHA-256 of the raw token and writing the raw token to **stdout** (diagnostics to
stderr). Pipe the raw token straight into prisma's `0600` file — never print or
store it elsewhere:

```bash
ssh devbox 'docker cp - karaoke:/tmp/mint.py < /dev/null; \
  docker cp scripts/cookie-rotation/mint-cookie-token.py karaoke:/tmp/mint.py; \
  docker exec karaoke sh -lc "set -a; . /secrets/karaoke.env; set +a; cd /app && /app/.venv/bin/python /tmp/mint.py; rm -f /tmp/mint.py"' \
  | ssh prisma 'umask 077; mkdir -p ~/.config/karaoke; cat > ~/.config/karaoke/cookie-sync.token; chmod 600 ~/.config/karaoke/cookie-sync.token'
```

> **Gotcha (important):** `docker exec` does **not** inherit the
> entrypoint-sourced secrets. Without `set -a; . /secrets/karaoke.env`, the
> script silently uses the default SQLite DB (`sqlite+aiosqlite:///./karaoke.db`)
> and the minted token will **not** authenticate against the live (Postgres)
> API. Always source the secrets before running it.

(Alternatively, use the machine bearer `KARAOKE_SERVICE_TOKEN` — quicker, but it
grants machine-admin and is not recommended for a file on a second host.)

## 2. Install on prisma

```bash
install -m 755 scripts/cookie-rotation/karaoke-cookie-sync.sh ~/.local/bin/karaoke-cookie-sync.sh
install -m 644 scripts/cookie-rotation/uk.labs.ok.karaoke-cookie-sync.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/uk.labs.ok.karaoke-cookie-sync.plist
launchctl kickstart -k gui/$(id -u)/uk.labs.ok.karaoke-cookie-sync   # one immediate fire
tail -1 ~/Library/Logs/karaoke-cookie-sync.log                       # expect: OK http=200 ...
```

Requires: `yt-dlp` on `PATH` (or set `YTDLP=`), a Firefox profile logged into
YouTube, and LAN reach to the coordinator (`KARAOKE_BASE_URL`, default
`http://10.10.0.13:13140`).

## 3. Verify (server side)

```bash
ssh devbox 'docker exec karaoke /app/.venv/bin/python -c "import os,urllib.request as u;t=os.environ[\"KARAOKE_SERVICE_TOKEN\"];print(u.urlopen(u.Request(\"http://127.0.0.1:8000/cookies/youtube\",headers={\"Authorization\":\"Bearer \"+t})).read().decode())"'
# → {"configured":true,"present":true,...,"last_good_present":true} with a fresh modified_at
```

## Uninstall

```bash
launchctl bootout gui/$(id -u)/uk.labs.ok.karaoke-cookie-sync
rm ~/Library/LaunchAgents/uk.labs.ok.karaoke-cookie-sync.plist ~/.local/bin/karaoke-cookie-sync.sh
```
