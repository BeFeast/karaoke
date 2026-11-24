# Song catalog pilot

The dated manifest records the public YouTube Music playlists and original
positions for 30 selections. Use the playlist IDs with
`https://music.youtube.com/playlist?list=<playlist ID>`. Genres marked
uncertain are provisional editorial tags.

`playback_basis` is the operator's record that the public source may be
processed and its stems and lyrics may be delivered to signed-in Sonereli
users. A chart entry supplies discovery metadata. Set the field only after
confirming playback eligibility. Leave it empty to include metadata and skip
precompute. Use the same approved manifest for all writes so importing it
again applies the intended permissions. Setting an existing entry's
`playback_basis` back to null disables its existing shared Stage, including
owner links.

The pilot uses the coordinator's database, artifact volume, yt-dlp, and
configured RunPod endpoint. Run from the coordinator application environment
with its normal settings loaded.

```sh
# Offline validation. No database writes or external calls.
karaoke-catalog-pilot --dry-run

# Publish discovery metadata without any downloads.
karaoke-catalog-pilot --import-only

# Process at most one cleared video. Supply the verified endpoint pricing ceiling.
karaoke-catalog-pilot --manifest approved.json --run --limit 1 --usd-per-hour-ceiling RATE

# After listening to the intro and chorus of both stems with the synced lyrics.
karaoke-catalog-pilot --manifest approved.json --publish VIDEO_ID
```

`--wait` waits for foreground downloads and the cooldown between starts.
The command exits when the UTC day or overall budget cannot cover the next
reservation. Each finished or interrupted attempt stays in the database.
A second runner cannot start while the first holds the PostgreSQL lock.
HTTP 403, HTTP 429, and bot-gate failures pause the pilot. Once the issue is
resolved, use `--resume` with `--run` to process subsequent recordings.
Failed attempts are retained and never retried automatically.

Before processing, the runner reserves up to eight minutes of GPU execution
and two additional minutes for startup and billing overhead at the supplied
hourly ceiling. It charges uncertain GPU attempts the full reservation and
counts a job that crosses midnight against both UTC days. The publish output
reports the job's GPU estimate and the corresponding charge to the ledger.
