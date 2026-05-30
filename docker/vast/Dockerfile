# syntax=docker/dockerfile:1.6
# karaoke Vast worker — GPU stages ONLY (Demucs separation + Whisper lyrics).
#
# karaoke does YouTube download + ffmpeg normalize on the coordinator
# (devbox, residential IP) and ships a pre-decoded audio file to the Vast
# GPU, which only runs Demucs (vocal/instrumental separation) and
# faster-whisper (lyrics transcription). So this image drops everything
# the coordinator owns: yt-dlp, deno (EJS solver), unzip, git.
#
# Mirrors scribe's Vast image split (see scribe docker/vast/Dockerfile).
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/karaoke-venv/bin:/usr/local/bin:/root/.local/bin:${PATH}" \
    HF_HOME="/root/.cache/huggingface" \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_DOWNLOAD_TIMEOUT=120 \
    TORCH_HOME="/root/.cache/torch" \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1 \
    KARAOKE_VAST_PORT=8000

# ffmpeg stays: Demucs and faster-whisper both decode through it.
# Demucs htdemucs needs torchaudio's ffmpeg backend.
# Ubuntu 22.04 ships python3.10; pull python3.12 from deadsnakes PPA.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      gnupg \
      software-properties-common \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
      ffmpeg \
      openssh-client \
      python3.12 \
      python3.12-venv \
      python3.12-dev \
    && apt-get purge -y software-properties-common \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /var/log/* /var/cache/apt/*

# uv installed to /usr/local/bin so it survives PATH resets and any
# onstart fallback finds it. No deno / yt-dlp on the Vast side.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
    && (ln -sf /usr/local/bin/uv /root/.local/bin/uv 2>/dev/null \
        || (mkdir -p /root/.local/bin && ln -sf /usr/local/bin/uv /root/.local/bin/uv)) \
    && uv --version

# Build the Python venv with what the worker imports at runtime:
#   - torch / torchaudio CUDA 12.4 wheels (PyTorch official cu124 index)
#   - demucs (htdemucs separation)
#   - faster-whisper (large-v3-turbo lyrics transcription)
RUN uv venv --python python3.12 /opt/karaoke-venv \
    && /opt/karaoke-venv/bin/python -m ensurepip --upgrade \
    && uv pip install --python /opt/karaoke-venv/bin/python \
        --index-url https://download.pytorch.org/whl/cu124 \
        "torch==2.4.*" "torchaudio==2.4.*" \
    && uv pip install --python /opt/karaoke-venv/bin/python \
        "demucs>=4.0.1" \
        "faster-whisper>=1.1.1" \
    && /opt/karaoke-venv/bin/pip uninstall -y hf_xet 2>/dev/null || true

# Pre-cache Demucs htdemucs model weights at build time so cold-start on
# Vast does not pay a model-download cost. `demucs --help` triggers the
# model registry import; an explicit dry call on a tiny silent WAV
# materializes the htdemucs bag-of-models bundle into TORCH_HOME.
RUN /opt/karaoke-venv/bin/python - <<'PY'
# Force-download htdemucs weights via demucs's pretrained loader.
from demucs.pretrained import get_model
model = get_model("htdemucs")
print("htdemucs cached:", type(model).__name__)
PY

# Pre-cache the large-v3-turbo Whisper model. CPU+int8 init only
# downloads weights; runtime uses GPU+float16.
RUN /opt/karaoke-venv/bin/python - <<'PY'
from faster_whisper import WhisperModel
WhisperModel("large-v3-turbo", device="cpu", compute_type="int8")
PY

# Clean caches and pyc bloat.
RUN find /opt/karaoke-venv -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true \
    && find /opt/karaoke-venv -name "*.pyc" -delete 2>/dev/null || true \
    && rm -rf /root/.cache/uv /root/.cache/pip /tmp/*

# Smoke-test that the surviving libs resolve and core imports work.
RUN ffmpeg -version >/dev/null \
    && uv --version >/dev/null \
    && /opt/karaoke-venv/bin/python - <<'PY'
import os
import torch
from demucs.pretrained import get_model
from faster_whisper import WhisperModel
print("torch version:", torch.__version__)
print("torch cuda built:", torch.version.cuda)
print("demucs htdemucs loadable:", type(get_model("htdemucs")).__name__)
print("faster-whisper import ok")
hf = os.environ.get("HF_HOME", "/root/.cache/huggingface")
assert os.path.isdir(hf + "/hub"), "whisper model cache missing"
th = os.environ.get("TORCH_HOME", "/root/.cache/torch")
assert os.path.isdir(th), "torch model cache missing"
print("model caches present:", hf, th)
PY

# Install the HTTP server entrypoint. The coordinator (devbox) SSH-tunnels
# 8000 to this container and POSTs work to /demucs and /whisper.
WORKDIR /work
COPY server.py /work/server.py

EXPOSE 8000

# Sanity-check the entrypoint imports cleanly (no model load).
RUN /opt/karaoke-venv/bin/python -c "import py_compile; py_compile.compile('/work/server.py', doraise=True)"

LABEL org.opencontainers.image.title="karaoke Vast worker (Demucs + Whisper)" \
      org.opencontainers.image.description="CUDA 12.4 GPU worker for karaoke: pre-cached htdemucs (separation) + large-v3-turbo (lyrics). Coordinator owns yt-dlp/ffmpeg; this image runs GPU stages only." \
      org.opencontainers.image.source="https://github.com/BeFeast/karaoke"

ENTRYPOINT ["/opt/karaoke-venv/bin/python", "/work/server.py"]
