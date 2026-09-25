# Offline lyrics evaluation

`scripts/evaluate_lyrics.py` compares candidate LRC against a human-labelled recording.
It runs locally without network, GPU provisioning, or audio downloads. It evaluates
stored results; it does not transcribe or align audio. No real song lyrics or audio
are included in the repository fixtures.

Prepare a private corpus outside the repository. Each entry must name the **same
recording/edit** as its candidate (studio/live, language, cuts, repeats, and spoken
words matter). Listen to the vocal audio and label the complete text. Add word starts
in seconds where a human has established them; use `null` where unresolved. Do not
use LRCLIB or ASR output alone as ground truth.

```json
{
  "schema_version": 1,
  "entries": [{
    "id": "synthetic-demo",
    "language": "en",
    "variant": "studio-example",
    "reference": {
      "text": "one two",
      "words": [{"text": "one", "start": 1.0}, {"text": "two", "start": 2.0}]
    },
    "candidate_lrc": "results/demo.lrc"
  }]
}
```

`candidate_lrc` is relative to the manifest directory (absolute paths also work).
IDs must be unique. `language` and `variant` are optional descriptive labels, not
translation or recording-matching instructions. `reference.words` is optional; when
present it must cover every normalized token in `reference.text`, in order. The
example candidate might contain `[00:01.00]<00:01.00>one <00:02.00>two <00:03.00>`.

```bash
uv run scripts/evaluate_lyrics.py /private/corpus/manifest.json > /tmp/lyrics-report.json
uv run scripts/evaluate_lyrics.py /private/corpus/manifest.json --thresholds /private/corpus/limits.json
```

The report contains each recording and a word-weighted aggregate:

- WER = `(substitutions + deletions + insertions) / reference_words`. It may exceed 1.
  Missing words are `deletions`; extra words are `insertions`. Case and punctuation
  are normalized using Unicode NFKC and casefold; apostrophes join contractions,
  other punctuation separates tokens. This tokenizer is not a language-specific
  word segmenter, so compare like-for-like languages and tokenization conventions.
- Word-start MAE, p50 and p95 use absolute timing differences in seconds for exact
  text matches with human and candidate word timestamps. Percentiles use nearest
  rank. Plain line timestamps and multi-word spans are **not** word timing evidence.
- `word_timing_coverage` always divides scored words by **all reference words**.
  `timed_reference_words` shows how many have labels;
  `timing_unscored_reference_words` includes missing/substituted words, missing
  labels/candidate timings, and ambiguous matches. No scored words yields `null`
  timing metrics, not zero error.
- Equal-cost text alignments (often a missing repeated chorus) set
  `alignment_ambiguous`. All timing for that entry remains unscored rather than
  assigning the surviving words to an arbitrary occurrence. WER remains meaningful;
  substitution/deletion/insertion counts use deterministic diagonal-first ties.

Optional threshold JSON example (values are **examples, not accepted product targets**):

```json
{
  "max_wer": 0,
  "max_missing_words": 0,
  "max_extra_words": 0,
  "max_word_start_mae_s": 0.1,
  "max_word_start_p95_s": 0.2,
  "min_word_timing_coverage": 1
}
```

Every entry must satisfy every supplied threshold; a good corpus mean cannot hide
one failed recording. An unscored timing metric fails a supplied timing threshold.
Exit status: `0` evaluation passed/no thresholds supplied, `1` threshold regression,
`2` invalid input. Save candidate reports per revision and compare on the same
frozen, human-reviewed corpus; synthetic tests validate the evaluator, not product
accuracy. A passing report does not establish perfect transcription for unseen songs.
