# karaoke Vast GPU image

`ghcr.io/befeast/karaoke-vast:cuda12.4` is the CUDA 12.4 fallback GPU image
for ephemeral vast.ai jobs. The coordinator on `devbox` still owns YouTube
download and audio normalization; this image only runs the GPU stages:

1. Demucs `htdemucs` vocal/instrumental separation.
2. `faster-whisper` `large-v3-turbo` lyrics transcription with `float16` on CUDA.

The image deliberately contains no `yt-dlp`, no Node/Deno runtime, and no
`git`. Those belong on the coordinator side, not on datacenter GPU hosts.

## Entrypoint contract

The container runs a single `uv run` entrypoint:

```bash
uv run --no-project --active python /work/entrypoint.py <input-audio> <output-dir>
```

`<input-audio>` is a WAV or MP3 file staged into the container. `<output-dir>`
is created if needed and receives:

- `vocals.mp3`
- `no_vocals.mp3`
- `lyrics.txt`
- `lyrics.json`

The script fails before loading models if `nvidia-smi` is unavailable or
`torch.cuda.is_available()` is false. CPU fallback is intentionally disabled.

## Build

Run from the repo root on `workshop`:

```bash
docker buildx build \
  --builder karbuilder \
  --platform linux/amd64 \
  -t ghcr.io/befeast/karaoke-vast:cuda12.4 \
  -f docker/vast/Dockerfile \
  --load \
  docker/vast
```

`--platform linux/amd64` is mandatory because vast.ai hosts are x86_64. The
`karbuilder` builder keeps BuildKit cache on `/mnt/storage`; use the local
builder only if it has enough disk.

## Push and pull verification

GHCR push requires a GitHub token with `write:packages` for `BeFeast`:

```bash
echo "$GHCR_PAT" | docker login ghcr.io -u <github-user> --password-stdin
docker push ghcr.io/befeast/karaoke-vast:cuda12.4
docker pull ghcr.io/befeast/karaoke-vast:cuda12.4
```

After first publish, make the package public in the GHCR UI if vast.ai needs
unauthenticated pulls.

## Smoke test on a CUDA host

Use a real CUDA 12.4-capable host, not CI:

```bash
docker run --rm --gpus all \
  -v "$PWD/sample.wav:/input/sample.wav:ro" \
  -v "$PWD/out:/out" \
  ghcr.io/befeast/karaoke-vast:cuda12.4 \
  /input/sample.wav /out
```

Expected files:

```bash
ls -lh out/vocals.mp3 out/no_vocals.mp3 out/lyrics.txt out/lyrics.json
```

GPU visibility check:

```bash
docker run --rm --gpus all ghcr.io/befeast/karaoke-vast:cuda12.4 \
  /bin/sh -lc 'nvidia-smi'
```

CPU-only fail-fast check:

```bash
docker run --rm ghcr.io/befeast/karaoke-vast:cuda12.4 \
  /input/sample.wav /out
```

That invocation must fail before Demucs or Whisper model loading.

Forbidden tool check:

```bash
docker run --rm --entrypoint sh ghcr.io/befeast/karaoke-vast:cuda12.4 \
  -c 'which yt-dlp node deno git || true'
```

The command should print nothing.

## Pre-cached weights

Build-time cache contents:

- Demucs: `htdemucs`, stored under `TORCH_HOME=/root/.cache/torch`.
- faster-whisper: `large-v3-turbo`, stored under
  `HF_HOME=/root/.cache/huggingface`.

Runtime Whisper uses `device="cuda"` and `compute_type="float16"`. The build
uses CPU `int8` only to force the Hugging Face download during image creation.

## Size

Target size is approximately 10 GB because the image includes CUDA 12.4,
cuDNN runtime libraries, PyTorch CUDA wheels, Demucs, faster-whisper, and
pre-cached model weights.

Pushed manifest:

- Index digest:
  `sha256:4f5dfc8970cd6ea991a6f197f61936672d4ac88eb37689045dc658ed63060d09`
- Linux/amd64 image digest:
  `sha256:557c0c3fee84b64dac4567c553adaaf3ed44045925423454b03a4bc6b257e9b6`
- Actual image size from the pushed registry manifest: 6.50 GiB compressed
  (`6,975,153,032` bytes).

`docker images` measurement for the fresh manifest was blocked on `workshop`
by local Docker store pressure (`/var/lib/containerd` had 1.7 GB available
and failed while extracting the 3.0 GB CUDA/Torch layer). Re-run after freeing
local Docker space:

```bash
docker images ghcr.io/befeast/karaoke-vast:cuda12.4
```
