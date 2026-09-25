# Lyric completeness, repair and listening review

Refs #272; depends on #270 / PR #271 (ordered repeated-line agreement).

## Acceptance contract

Audio completion and lyric acceptance are independent. `completed` means that
playable stems exist. It does not mean that every sung word is correct.

Every newly finalized result records `metadata.json.lyrics_quality`, also saved
as `exports/lyrics.quality.json` and exposed as `quality` on the lyrics API.

- `needs_review`: omissions, additions, text disagreement, insufficient independent
  evidence, missing word timing or suspicious timing remain. Playback is a preview.
- `checked`: automated evidence checks passed. This is explicitly not a listening
  review or a guarantee of perfect transcription.
- `reviewed`: the owner explicitly confirmed listening to the entire song and
  checking words, repeated lines and timestamps. This is a human assertion,
  separately recorded; it is not inferred from a confidence threshold.
- Missing quality (old jobs) is displayed as not checked.

Agreement must use the full reference denominator, retain repeated occurrences,
account for missing and extra words, and inspect independent ASR evidence. A
perfect match against the retained subset is not a completeness measurement.
No track names, recording IDs or song-specific offsets are part of the algorithm.

## Automatic processing

1. Separate vocals once. Obtain independent Whisper text and word timestamps.
2. Force-align the candidate lyrics. Keep pre-filter line/word evidence and
   explicit rejection reasons, including VAD and acoustic confidence.
3. Reuse trusted, temporally local full-audio ASR words before making extra
   model calls. For remaining suspicious fragments, retry independent transcription
   with VAD disabled and no lyric prompt. Include neighboring complete phrases
   as context; merge overlapping windows into one decode and prioritize missing
   text ahead of timing-only problems. Corroborated anchors bound the search;
   known audio boundaries also permit first/last-line windows. Never guess a
   whole-song offset or borrow a later repeated chorus.
   The retry budget is 60 seconds of total cropped audio, at most 25 seconds per
   window, and a 60-second cooperative wall deadline, with a defensive maximum
   of 12 model calls. The overall job timeout remains the hard limit. Retry
   failure or an exhausted budget retains the baseline and explicit diagnostics.
   An exact lexical mismatch may use one alternate neighboring-phrase context
   within the same budget; decoding remains independent of the desired lyrics.
4. Reconcile accepted alignments with ASR. Restore a score-filtered line only
   when temporally local independent evidence supports it. Repair implausible
   internal word timing only with reliable matching ASR timestamps. Never
   invent missing canonical verses just to reach full reference coverage.
   Retain plausible existing alignment text with explicit provisional issues
   when acoustic/timing evidence passes preservation gates but independent ASR
   does not confirm every word. This is uncertain text, not verified recovery;
   the final quality remains `needs_review` and unreliable word timing is removed.
5. Audit the selected export, not merely the aligner input or kept-line subset.
   Preserve unresolved omissions, additions and timing disagreements for review.
   Evidence records distinguish reused full-audio ASR from cropped retry ASR;
   the coordinator validates each source before preserving a recovered line.
   Duplicate observations from overlapping ASR attempts count once. Conflicting
   observations remain explicit issues; cropping cannot erase contrary evidence.

The GPU response is additive: older workers remain usable, but their missing
retry/diagnostic evidence cannot be interpreted as a successful retry. Raw
artifacts and retry reports enable investigation without re-running separation.
The `vast` fallback does not gain RunPod crop retries; coordinator reconciliation
and review still apply to its output.

## Owner correction

The stage exposes issue locations, seek controls and an LRC editor alongside the
vocal player. Saving requires an explicit listening confirmation. Public share
readers see the quality state but cannot edit through possession of the share
URL alone.

`PUT /jobs/{id}/lyrics-review` accepts `lrc`, `expected_revision`, and `confirm`.
It checks the existing ownership rules, completed audio status, timestamp syntax
and bounds, and an optimistic revision. A stale revision returns 409, preserving
the user's draft. Previous lyrics, metadata and artifact sizes are backed up under
`work/lyrics-reviews/<revision-id>/`; failed writes restore prior files and roll
back the database transaction. Saving updates only lyric artifacts and their
sizes, not stems or GPU outputs. The lyrics response is private/no-store because
review capability depends on the requesting owner.

## Verification and limits

Synthetic tests exercise complete text, missed verses, repeated occurrences,
wrong-text alignment, unsupported shortened edits, missing word timing,
implausible tail timing, retry budgets, crop offset conversion, failed retries,
owner isolation, stale revisions and write rollback. These are functional
regressions, not a measured accuracy benchmark across genres and languages.

Saved incident artifacts are replayed as an additional diagnostic and are not
committed into the repository. Restoration count is reported honestly; thresholds
are not tuned until a particular song reaches a desired count.

A listening-labelled evaluation corpus remains necessary to measure word error
rate and word-boundary error percentiles on real studio/live/multilingual audio.
Neither ASR/CTC agreement nor an all-green unit suite establishes 100% accuracy.
The initial correction editor uses LRC; waveform-based per-word editing is a
separate UX improvement. Production coordinator and GPU images must both be
updated to enable the complete retry path; a coordinator-only deploy provides
quality disclosure/reconciliation/review without new GPU fragment retries.

## Validation record — 2026-09-25

The implementation passed 628 tests on the Forgejo base and was separately applied
on the actual live coordinator base `42c6442` (v0.39.1), preserving its newer
selfcheck, cold-start and downloader changes. That candidate passed 631 tests plus
11 added evaluator tests. Its source is on `validation/272-live`; this records code
compatibility, not deployed behavior. Browser checks used an isolated SQLite
fixture, not production jobs: issue seek, correction/save, immediate refresh,
stale-tab conflict with draft retention, and the Performance status all worked.

The authorized GPU rollout has since published and activated v3 on the production
`karaoke-poc-2` endpoint using an isolated template. The active image is
`ghcr.io/befeast/karaoke-runpod@sha256:a23088865c1ab52b775451bba311beb843a9daef6f1c1b2d5e9e307a039e77d4`
(handler source `9b4f4fc`). The production coordinator still runs
`42c6442`, version `0.39.1`; the candidate coordinator and owner-review UI have not
been deployed. GPU publication, endpoint activation and coordinator deployment
are separate states.

Fresh real-audio replays measured these results against the 37-line reference:

| GPU retry candidate | Matched/output lines | Missing reference words | Quality |
| --- | --- | --- | --- |
| v1 | 33/37 | 18 of 188 | `needs_review` |
| v2 | 34/37 | 12 of 188 | `needs_review` |
| v3 with coordinator preservation | 37/37 | 0 of 188 | `needs_review` (two provisional lines) |

These are reference-coverage measurements, not human-confirmed accuracy. All
runs retain independent-ASR disagreements and unconfirmed output words. The v3
run completed in 55.202 seconds on an RTX 4090 and used six crop decodes covering
45.4 seconds of audio. Its alternate-context River retry passed the independent
evidence gates. Coordinator replay preserves all 37 lines / 188 reference words
with no missing or extra reference words, including two explicitly provisional
lines. It still records 13 unconfirmed output words and 14 unmatched ASR words;
three conflicting ASR observations remain explicit issues and one alignment
retains only line timing. The selected LRC passes the correction API timestamp
syntax/order validator against the 273.4-second audio duration. This is input
validation, not owner review. No complete-song listening acceptance
or claim of perfect transcription follows from the restored coverage.

Eight controlled crop/model probes varied narrow/wide context and
`large-v3`/`large-v3-turbo`. They did not resolve the remaining opener/catch lexical
errors represented by “Monday” and “drip”. A separate wider-context “River”
fragment passed the existing evidence/confidence/timing gates. That local success
does not establish complete-song correctness. Further bounded contextual retry
work must preserve the evidence gates and resource limits; inserting desired
reference text to improve the coverage count is not acceptance.

Operator evidence remains outside the repository on maestro:

- `/tmp/karaoke-debug-20260925/fresh-gpu-272/exports/lyrics.quality.json`
- `/tmp/karaoke-debug-20260925/fresh-gpu-272-v2/exports/lyrics.quality.json`
- `/tmp/karaoke-debug-20260925/fresh-gpu-272-v3/exports/lyrics.quality.json`
- `/tmp/karaoke-asr-context-20260925/*.json` (eight controlled probes)
- `/tmp/karaoke-large-v3-20260925/*.json` (wider-context comparison)

Those paths are temporary diagnostic evidence, not a durable labelled corpus.
No complete-song listening acceptance or cross-song accuracy benchmark has been
established. Release integration and CI still need to validate the complete
candidate. Production authorization exists; the remaining deployment hold is
verification, not a request for new approval.
