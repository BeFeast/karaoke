# syntax=docker/dockerfile:1.6
# karaoke RunPod Serverless worker — GPU stages ONLY (Demucs separation + Whisper lyrics).
#
# Mirrors docker/vast/Dockerfile but targets RunPod Serverless:
#   - drops openssh-client (no SSH-tunneled HTTP path on Serverless)
#   - drops the HTTP server / EXPOSE 8000 (Serverless calls a Python handler)
#   - adds the `runpod` SDK and a JSON-in/JSON-out handler entrypoint
#
# yt-dlp + ffmpeg-decode stays on the coordinator (devbox); this image
# only runs Demucs (vocal/instrumental) + faster-whisper (lyrics).
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/karaoke-venv/bin:/usr/local/bin:/root/.local/bin:${PATH}" \
    HF_HOME="/root/.cache/huggingface" \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_DOWNLOAD_TIMEOUT=120 \
    TORCH_HOME="/root/.cache/torch" \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1

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
      python3.12 \
      python3.12-venv \
      python3.12-dev \
    && apt-get purge -y software-properties-common \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* /var/log/* /var/cache/apt/*

# uv installed to /usr/local/bin so it survives PATH resets.
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh \
    && (ln -sf /usr/local/bin/uv /root/.local/bin/uv 2>/dev/null \
        || (mkdir -p /root/.local/bin && ln -sf /usr/local/bin/uv /root/.local/bin/uv)) \
    && uv --version

# Build the Python venv with what the worker imports at runtime:
#   - torch / torchaudio CUDA 12.4 wheels (PyTorch official cu124 index)
#   - demucs (htdemucs separation)
#   - faster-whisper (large-v3-turbo lyrics transcription)
#   - runpod (Serverless SDK; entrypoint calls runpod.serverless.start)
RUN uv venv --python python3.12 /opt/karaoke-venv \
    && /opt/karaoke-venv/bin/python -m ensurepip --upgrade \
    && uv pip install --python /opt/karaoke-venv/bin/python \
        --index-url https://download.pytorch.org/whl/cu124 \
        "torch==2.4.*" "torchaudio==2.4.*" \
    && uv pip install --python /opt/karaoke-venv/bin/python \
        "demucs>=4.0.1" \
        "faster-whisper>=1.1.1" \
        "runpod>=1.7.0" \
        "ctc-forced-aligner" \
    && /opt/karaoke-venv/bin/pip uninstall -y hf_xet 2>/dev/null || true

# Pre-cache Demucs htdemucs model weights at build time so cold-start
# does not pay a model-download cost.
RUN /opt/karaoke-venv/bin/python - <<'PY'
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

# Pre-cache the ctc-forced-aligner MMS-300m model + tokenizer (#55) so
# cold-start does not pay a model-download cost. CPU load only downloads
# weights; runtime uses GPU+float16 when available.
RUN /opt/karaoke-venv/bin/python - <<'PY'
import torch
from ctc_forced_aligner import load_alignment_model
load_alignment_model(
    "cpu",
    model_path="MahmoudAshraf/mms-300m-1130-forced-aligner",
    dtype=torch.float32,
)
print("ctc-forced-aligner mms-300m cached")
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
import runpod  # noqa: F401
import ctc_forced_aligner  # noqa: F401
print("torch version:", torch.__version__)
print("torch cuda built:", torch.version.cuda)
print("demucs htdemucs loadable:", type(get_model("htdemucs")).__name__)
print("faster-whisper import ok")
print("runpod sdk import ok")
print("ctc-forced-aligner import ok")
hf = os.environ.get("HF_HOME", "/root/.cache/huggingface")
assert os.path.isdir(hf + "/hub"), "whisper model cache missing"
th = os.environ.get("TORCH_HOME", "/root/.cache/torch")
assert os.path.isdir(th), "torch model cache missing"
print("model caches present:", hf, th)
PY

# Install the Serverless handler entrypoint. RunPod Serverless invokes
# the handler by running this script; runpod.serverless.start() polls
# the platform's job queue. No HTTP server, no exposed ports.
WORKDIR /work
COPY handler.py /work/handler.py

# Sanity-check the entrypoint imports cleanly (no model load).
RUN /opt/karaoke-venv/bin/python -c "import py_compile; py_compile.compile('/work/handler.py', doraise=True)"

LABEL org.opencontainers.image.title="karaoke RunPod Serverless worker" \
      org.opencontainers.image.description="CUDA 12.4 GPU worker for karaoke on RunPod Serverless: pre-cached htdemucs (separation) + large-v3-turbo (lyrics). Coordinator owns yt-dlp/ffmpeg; this image runs GPU stages only via a JSON-in/JSON-out handler." \
      org.opencontainers.image.source="https://github.com/BeFeast/karaoke"

ENTRYPOINT ["/opt/karaoke-venv/bin/python", "/work/handler.py"]
