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
3. For suspicious bounded interior fragments, retry independent transcription
   with VAD disabled and no lyric prompt. Require corroborated neighboring
   anchors; do not guess whole-song offsets or use a later repeated chorus.
   Limit the number and total duration of crops and honor the existing job
   timeout/cost limits. Retry failure retains the baseline and diagnostics.
4. Reconcile accepted alignments with ASR. Restore a score-filtered line only
   when temporally local independent evidence supports it. Repair implausible
   internal word timing only with reliable matching ASR timestamps. Never
   insert unsupported canonical verses just to reach full reference coverage.
5. Audit the selected export, not merely the aligner input or kept-line subset.
   Preserve unresolved omissions, additions and timing disagreements for review.
   Cropped retry evidence remains separate from first-pass ASR.

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
