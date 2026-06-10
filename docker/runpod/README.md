# karaoke RunPod Serverless worker

GPU image + Serverless handler for the karaoke pipeline. Companion to
`docker/vast/` (which targets vast.ai); this directory targets RunPod
Serverless. Refs #33 (Track A).

Coordinator (`devbox`) still owns YouTube download + ffmpeg normalize.
This image runs only the GPU stages: **BS-Roformer** stem separation
(`model_bs_roformer_ep_317_sdr_12.9755`, via
[`audio-separator`](https://github.com/nomadkaraoke/python-audio-separator);
replaces the previous Demucs `htdemucs`) and faster-whisper (large-v3-turbo,
lyrics transcription).

## Handler invocation contract

RunPod posts a JSON job; the handler returns JSON.

### Input — `event["input"]`

```json
{
  "audio_base64": "<base64-encoded WAV bytes>",
  "mode": "demucs" | "whisper" | "both",
  "align_text": "<plain lyrics to force-align>",
  "align_lang": "eng"
}
```

`mode` defaults to `"both"`. Anything else raises `ValueError` and the job
is marked FAILED — the handler never silently swallows errors. The
`"demucs"` mode name is kept verbatim for backward compatibility with the
coordinator even though the separation engine is now BS-Roformer.

In `mode=="both"` Whisper transcribes the **separated vocals stem**, not
the raw input — that gives cleaner lyrics (and BS-Roformer's cleaner stem
is expected to improve transcription/alignment as a side effect).

`align_text` (optional, #55): when supplied and separation runs, the handler
force-aligns this plain-text against the vocal stem with
`ctc-forced-aligner` (MMS-300m) and returns a synthesized line-level LRC in
`aligned_lrc`. `align_lang` is an ISO-639-3 code (default `eng`). Alignment
is purely additive and **never fatal**: on failure (or with an old image
that ignores the field) the handler simply omits `aligned_lrc`, and the
coordinator falls back to the plain text / Whisper transcript.

### Output

| field             | demucs | whisper | both |
| ----------------- | :----: | :-----: | :--: |
| `vocals_b64`      |   x    |         |  x   |
| `instrumental_b64`|   x    |         |  x   |
| `lyrics_txt`      |        |    x    |  x   |
| `lyrics_json`     |        |    x    |  x   |
| `aligned_lrc`*    |   x    |         |  x   |
| `aligned_lang`*   |   x    |         |  x   |
| `gpu_model`       |   x    |    x    |  x   |
| `elapsed_s`       |   x    |    x    |  x   |

`*` only present when `align_text` was supplied **and** alignment
succeeded.

`*_b64` are base64-encoded `.wav` bytes. audio-separator emits a vocals
stem and an instrumental stem; the handler forces deterministic
`vocals.wav` / `instrumental.wav` output names (via `custom_output_names`)
and maps the instrumental stem to `instrumental_b64` (the old
`no_vocals.wav` role).

`lyrics_json` shape mirrors `docker/vast/server.py`:

```json
{
  "language": "en",
  "language_probability": 0.99,
  "duration": 187.4,
  "segments": [
    {
      "start": 0.0, "end": 2.4, "text": "...",
      "words": [{"start": 0.0, "end": 0.4, "word": "...", "probability": 0.97}]
    }
  ]
}
```

`gpu_model` comes from `torch.cuda.get_device_name(0)` (or `"cpu"` on a
non-GPU sanity run); `elapsed_s` wraps the entire handler body via
`time.monotonic()`.

## Build + push

Built on `workshop` with the existing `karbuilder` buildx builder so the
BuildKit cache lives on `/mnt/storage` (the box `/` only has ~12 GB
free). Run from the repo root:

```bash
docker buildx build \
  --builder karbuilder \
  --platform linux/amd64 \
  -t ghcr.io/befeast/karaoke-runpod:cuda12.4-r6 \
  --push \
  docker/runpod/
```

> **#98 rebuild — BS-Roformer.** The current live image is
> `ghcr.io/befeast/karaoke-runpod:cuda12.4-r4` (Demucs htdemucs + force-align).
> The BS-Roformer swap (audio-separator + the pre-cached
> `model_bs_roformer_ep_317_sdr_12.9755.ckpt`) lands in the **next** tag
> (`…-r5`). The handler JSON I/O contract is unchanged, so the coordinator
> needs no changes. Until that image is built, pushed, **and the RunPod
> template is repointed + its warm workers flushed**
> (`scripts/runpod_provision.py`, see #75), the live handler keeps running
> Demucs. Do the rebuild + endpoint update under supervision after this PR
> merges; do not bump the endpoint from CI. **Rollback**: repoint the
> template back to `cuda12.4-r4`; the coordinator is untouched and the
> runtime baseline is preserved as release `v0.1.0` / image `karaoke:0.1.0`.

After the **first** push of a new package the image must be flipped to
**public** in the GHCR UI (`https://github.com/orgs/BeFeast/packages` →
karaoke-runpod → Package settings → Change visibility → Public). RunPod
Serverless pulls without auth, so a private image will leave workers stuck
on `pulling`. (`karaoke-runpod` is already public from the r3/r4 pushes;
new tags on the same package inherit that visibility.)

## Provisioning the endpoint

`scripts/runpod_provision.py` is idempotent — first run creates
`karaoke-poc`, subsequent runs PATCH the same endpoint. From the repo
root:

```bash
RUNPOD_API_KEY=... uv run python scripts/runpod_provision.py
```

The locked spec (image, GPU type pool, workers, timeouts, flashboot)
lives in `TEMPLATE_SPEC` / `ENDPOINT_SPEC` inside the script — do not edit
without flagging it on issue #33. To roll the endpoint onto the r5 image,
bump `TEMPLATE_SPEC["imageName"]` to `…cuda12.4-r6` and re-run: the script
detects the image change and flushes warm/standby workers (`workersMax`
bounce to 0 → drain → restore) so the next job cold-pulls r5 instead of a
stale r4 worker (#75). The script prints a final
`RUNPOD_ENDPOINT_ID=<id>` line that the operator pipes into Infisical
manually; the script never touches Infisical.

**GPU pool / VRAM note.** The endpoint's `gpuTypeIds` pool floor is the
RTX A4000 (16 GB). BS-Roformer at audio-separator's default chunk/overlap
settings runs comfortably under that, but if a future model or larger
segment size OOMs on the 16 GB tier, either narrow the pool to ≥20 GB GPUs
or pass smaller `mdxc`/`roformer` segment params to the `Separator` in
`handler.py::_get_separator`.

## Why this image differs from `docker/vast/`

- No `openssh-client`, no `EXPOSE`, no HTTP server — Serverless invokes
  the Python handler directly through its own job queue.
- Adds the `runpod` SDK and uses `runpod.serverless.start({"handler":
  handler})` as the entrypoint.
- Same CUDA 12.4 base, deadsnakes python3.12, torch cu124, audio-separator
  (BS-Roformer) + faster-whisper, and the same BS-Roformer +
  large-v3-turbo pre-cache so cold-start does not re-download weights.
  (`docker/vast/` still runs Demucs htdemucs; the BS-Roformer swap is
  RunPod-only for now.)
