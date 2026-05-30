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
