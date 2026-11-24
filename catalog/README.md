# Song catalog pilot

The dated manifest records the public YouTube Music playlists and original
positions for 15 Hebrew and 15 English selections. Genres marked uncertain
are provisional editorial tags.

- [Trending 20 Israel](https://music.youtube.com/playlist?list=OLAK5uy_mSuTPbhkJmOk5Hk21K2FKPv2rWx_5ag6w)
- [Top 100 Music Videos Global](https://music.youtube.com/playlist?list=PL4fGSI1pDJn5kI81J1fYWK5eZRl1zJ5kM)
- [Top 50 Rock Music Videos United States](https://music.youtube.com/playlist?list=PL4fGSI1pDJn5LOptOQixqnzXNGjNXAgYY)

`playback_basis` is the operator's record that the public source may be
processed and its stems and lyrics may be delivered to signed-in Sonereli
users. A chart entry supplies discovery metadata. Set the field only after
confirming playback eligibility. Leave it empty to include metadata and skip
precompute. Use the same approved manifest for all writes so importing it
again applies the intended permissions. Setting an existing entry's
`playback_basis` back to null disables its existing shared Stage, including
owner links. Set `available` or `metadata_allowed` to false to hide the
recording and disable its exports.

The pilot uses the coordinator's database, artifact volume, yt-dlp, and
configured RunPod endpoint. Run from the coordinator application environment
with its normal settings loaded.

```sh
# Offline validation. No database writes or external calls.
karaoke-catalog-pilot --dry-run

# Import discovery metadata and retire recordings outside the two pilot languages.
karaoke-catalog-pilot --import-only

# Process at most one cleared video. Supply the verified endpoint pricing ceiling.
karaoke-catalog-pilot --manifest approved.json --run --limit 1 --usd-per-hour-ceiling RATE

# Recheck an eligible asset in review, and publish only if all checks pass.
karaoke-catalog-pilot --auto-check ASSET_ID

# Read-only JSONL input for the evening review gallery.
karaoke-catalog-pilot --gallery
```

`--wait` waits for foreground downloads and the cooldown between starts.
The command exits when the UTC day or overall budget cannot cover the next
reservation. Each finished or interrupted attempt stays in the database.
A second runner cannot start while the first holds the PostgreSQL lock.
HTTP 403, HTTP 429, and bot-gate failures pause the pilot. Once the issue is
resolved, use `--resume` with `--run` to process subsequent recordings.
Failed attempts are retained and never retried automatically.

The lifetime and UTC-day caps are each $1.00. The reservation is 1.5 times the
largest observed completed ledger charge, rounded up, with a $0.05 floor. It
must also cover at least 30 seconds of GPU execution and the 120-second
startup and billing allowance at the verified hourly ceiling. The ceiling
converts the reservation into a provider execution timeout. The ledger
charges uncertain GPU attempts the full reservation, counts a job that
crosses midnight against both days, and retains charges for retired assets.

For a session-independent run, start a detached exec in the running devbox
container. Set `RATE` to the verified endpoint hourly ceiling. This command
uses the already deployed image and writes the run output to the artifact
volume:

```sh
sudo -n docker exec -d karaoke sh -c '
    set -eu
    set -a
    . /secrets/karaoke.env
    set +a
    umask 077
    log_dir="${KARAOKE_ARTIFACT_ROOT:-/srv/artifacts}/catalog"
    mkdir -p "$log_dir"
    exec /app/.venv/bin/karaoke-catalog-pilot --run --wait --usd-per-hour-ceiling "$1" >> "$log_dir/pilot-run.log" 2>&1
' sh "$RATE"
```

The exec survives SSH disconnect. If the container is recreated, rerun the
command after it starts to recover any interrupted attempt from Postgres.
Use `--resume` after resolving a download gate.

## Auto-check

Completed exports enter `review` before the checker runs. It decodes both
stems with FFmpeg, compares their decoded duration and RMS with the thresholds,
and measures leaked vocals using a time-aligned waveform projection on each
channel in the 250–3400 Hz band. It estimates vocal activity and onsets from
50 ms RMS frames. Line timestamps must increase and the active frames must
have enough lyric coverage, with a bounded line window and no long uncovered
vocal passage. The timing check uses the median nearest vocal onset offset.

Thresholds are in [auto-check.json](auto-check.json). The pilot commits the
asset state and its JSON report together. Reports contain the actor
`auto-check`, measured values, threshold values, and failing checks. Review
assets remain available to the evening gallery and are hidden from shared
playback. A pass removes the intermediate work files after commit.

### Calibration on 2026-11-24

Measured retained production exports in read-only mode. The Hebrew and
English references had both stems and synced lyrics. The gap metric allows
up to 0.5 s of intervening quiet. Unknown lyrics metrics indicate rejection
before the coverage and onset checks.

| Production job | Language | Decoded length | RMS vocal / instrumental | Gain / correlation | Active lyrics coverage | Largest uncovered vocal passage | Median onset offset | Result |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| #383 | he | 206.925 / 207 s | .103 / .151 | .059 / .093 | 97.85% | 1.70 s | .210 s | pass |
| #392 | en | 232.533 / 233 s | .070 / .087 | .012 / .014 | 100% | 0 s | .385 s | pass |
| #458 | he | 163.480 / 164 s | .140 / .198 | .036 / .064 | 31.52% | 32.80 s | .140 s | review for coverage and gap |
| #435 | en | 213.043 / 213 s | .089 / .090 | .064 / .114 | unknown | unknown | unknown | review for timestamps |

The RMS floor of .015 and leakage caps of .12 gain and .20 correlation leave
headroom over those measured on the references. The fixed requirements are
2 s duration tolerance, 80% coverage and a .50 s onset offset limit. Synthetic
fixtures exercise leakage, silence, corrupt or missing exports, short stems,
missing and unordered lyrics, insufficient coverage, long gaps and shifted
lyric starts.
