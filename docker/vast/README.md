# karaoke Vast GPU image

`ghcr.io/befeast/karaoke-vast:cuda12.4` — the GPU-only worker image used on
ephemeral [vast.ai](https://vast.ai) instances spawned per job. It owns the
two GPU stages of the pipeline:

1. **Demucs** (`htdemucs`) — splits the source audio into isolated vocals
   and an instrumental "playback" track.
2. **faster-whisper** (`large-v3-turbo`) — transcribes the lyrics from the
   isolated vocals.

Mirrors scribe's `docker/vast/Dockerfile` split: the coordinator on
`devbox` (residential IP) runs `yt-dlp` + `ffmpeg`, then ships a pre-decoded
audio file to Vast. This image deliberately ships **no** `yt-dlp`, **no**
`deno`, and **no** `git`.

Both model bundles (htdemucs, large-v3-turbo) are pre-cached at build time
so cold-start on Vast does not pay a model-download cost.

## Build & push

`buildx` with `--platform linux/amd64` is mandatory: vast.ai instances are
x86_64, and building on Apple Silicon (or any non-amd64 host) without the
flag produces an unusable arm64 image.

```bash
docker buildx build \
  --platform linux/amd64 \
  -t ghcr.io/befeast/karaoke-vast:cuda12.4 \
  -f docker/vast/Dockerfile \
  --push \
  docker/vast
```

The build context is `docker/vast` (this directory). The image is large
(several GB) because of CUDA + cuDNN + torch + cached weights — expect a
multi-minute build on a fast link, longer on first push.

### Auth for `--push`

GHCR push requires a GitHub PAT with `write:packages` for the `BeFeast`
org. Local one-liner:

```bash
echo "$GHCR_PAT" | docker login ghcr.io -u <github-user> --password-stdin
```

## Tag policy

- `cuda12.4` — pinned to the CUDA 12.4 base; bump only when intentionally
  re-pinning the CUDA generation.
- `cuda12.4-<yyyy-mm-dd>` — optional dated revision tag for rollback.

Do not push `latest`. Vast launch templates pin an explicit tag so a stray
`latest` push cannot silently change cold-start behavior across jobs.

## What runs on this image

The coordinator launches the worker entrypoint via SSH after vast.ai
provisioning. The entrypoint script (lives in the karaoke repo, not in this
image) activates `/opt/karaoke-venv` and runs Demucs + faster-whisper
against the staged audio. See the karaoke PRD §architecture for the full
job lifecycle and the cost / `finally`-destroy contract.

## Demucs + Whisper CUDA coexistence

Refs #15. The GPU worker image is intentionally a single CUDA 12.4 runtime
that runs Demucs (`htdemucs`) followed by faster-whisper (`large-v3-turbo`,
`float16`) in one GPU window. The accepted floor remains:

- Vast offer filter: `cuda_max_good >= 12.4`, `gpu_ram >= 16000`,
  `num_gpus == 1`.
- RunPod pool: 16 GB+ GPUs only for the locked endpoint template.
- Coordinator-owned work only (`yt-dlp`, JS challenge solvers, cookies) stays
  outside the GPU image.

### Stage isolation pattern

Keep Demucs behind a separate Python subprocess and run Whisper only after
that subprocess exits:

- Demucs: `/opt/karaoke-venv/bin/python -m demucs.separate ...`
- Whisper: lazy in-process `WhisperModel("large-v3-turbo", device="cuda",
  compute_type="float16")`

This boundary prevents Demucs tensors and its CUDA context from being retained
by the long-lived worker process. The RunPod handler exposes the same pattern
and returns additive telemetry under `metrics`:

```json
{
  "metrics": {
    "total_elapsed_s": 123.456,
    "isolation": {
      "demucs": "subprocess",
      "whisper": "in-process-lazy-model"
    },
    "stages": {
      "demucs": {
        "elapsed_s": 77.1,
        "start_vram_mb": 423,
        "peak_vram_mb": 3912,
        "end_vram_mb": 611
      },
      "whisper": {
        "elapsed_s": 31.4,
        "start_vram_mb": 611,
        "peak_vram_mb": 5590,
        "end_vram_mb": 5450
      }
    }
  }
}
```

The VRAM samples come from `nvidia-smi`, so they cover both subprocess and
in-process stages. A non-GPU smoke run or an image without `nvidia-smi` returns
`null` VRAM fields while preserving elapsed timings.

### Benchmark protocol for #15

Use one representative 4-minute normalized WAV and run `mode="both"` once per
GPU class: RTX 4090, A2000, A4000, and L4. Record:

- `gpu_model`
- `metrics.stages.demucs.peak_vram_mb`
- `metrics.stages.whisper.peak_vram_mb`
- `metrics.stages.*.end_vram_mb` to confirm Demucs drops before Whisper starts
- `metrics.total_elapsed_s`

The issue report should include these measured values and the corresponding
ceiling recommendation. Until live measurements are posted, keep the current
conservative budget rails: Vast `MAX_INSTANCE_SECONDS=1800`,
`KARAOKE_VAST_MAX_JOB_COST=0.35`; RunPod
`KARAOKE_RUNPOD_WALL_CEILING_S=1200`, `KARAOKE_RUNPOD_MAX_JOB_COST=0.50`.
