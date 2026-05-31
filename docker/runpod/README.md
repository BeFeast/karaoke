# karaoke RunPod Serverless worker

GPU image + Serverless handler for the karaoke pipeline. Companion to
`docker/vast/` (which targets vast.ai); this directory targets RunPod
Serverless. Refs #33 (Track A).

Coordinator (`devbox`) still owns YouTube download + ffmpeg normalize.
This image runs only the GPU stages: Demucs (htdemucs, vocal/instrumental
separation) and faster-whisper (large-v3-turbo, lyrics transcription).

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
is marked FAILED — the handler never silently swallows errors.

In `mode=="both"` Whisper transcribes the **separated vocals stem**, not
the raw input — that gives cleaner lyrics.

`align_text` (optional, #55): when supplied and Demucs runs, the handler
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

`*_b64` are base64-encoded `.wav` bytes (htdemucs writes `vocals.wav` and
`no_vocals.wav` — the latter is mapped to `instrumental_b64`).

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
  -t ghcr.io/befeast/karaoke-runpod:cuda12.4-r4 \
  --push \
  docker/runpod/
```

> **#55 rebuild required.** The current live image is
> `ghcr.io/befeast/karaoke-runpod:cuda12.4-r3`. The force-align support
> (`ctc-forced-aligner` + pre-cached MMS-300m + the `align_text`/`aligned_lrc`
> handler contract) lands in the **next** tag (`…-r4`). Until that image is
> built, pushed, and the RunPod endpoint/template is repointed to it, the
> live handler ignores `align_text` — and the coordinator degrades cleanly
> (LRCLIB plain text / Whisper). Do the rebuild + endpoint update under
> supervision after this PR merges; do not bump the endpoint from CI.

After the **first** push the image must be flipped to **public** in the
GHCR UI (`https://github.com/orgs/BeFeast/packages` → karaoke-runpod →
Package settings → Change visibility → Public). RunPod Serverless pulls
without auth, so a private image will leave workers stuck on `pulling`.

## Provisioning the endpoint

`scripts/runpod_provision.py` is idempotent — first run creates
`karaoke-poc`, subsequent runs PATCH the same endpoint. From the repo
root:

```bash
RUNPOD_API_KEY=... uv run python scripts/runpod_provision.py
```

The locked spec (image, GPU type pool, workers, timeouts, flashboot)
lives in `ENDPOINT_SPEC` inside the script — do not edit without
flagging it on issue #33. The script prints a final
`RUNPOD_ENDPOINT_ID=<id>` line that the operator pipes into Infisical
manually; the script never touches Infisical.

## Why this image differs from `docker/vast/`

- No `openssh-client`, no `EXPOSE`, no HTTP server — Serverless invokes
  the Python handler directly through its own job queue.
- Adds the `runpod` SDK and uses `runpod.serverless.start({"handler":
  handler})` as the entrypoint.
- Same CUDA 12.4 base, deadsnakes python3.12, torch cu124, demucs +
  faster-whisper, and the same htdemucs + large-v3-turbo pre-cache so
  cold-start does not re-download weights.
