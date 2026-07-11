# syntax=docker/dockerfile:1.6
# karaoke RunPod Serverless worker — GPU stages ONLY (BS-Roformer separation + Whisper lyrics).
#
# Mirrors docker/vast/Dockerfile but targets RunPod Serverless:
#   - drops openssh-client (no SSH-tunneled HTTP path on Serverless)
#   - drops the HTTP server / EXPOSE 8000 (Serverless calls a Python handler)
#   - adds the `runpod` SDK and a JSON-in/JSON-out handler entrypoint
#
# yt-dlp + ffmpeg-decode stays on the coordinator (devbox); this image
# only runs stem separation (BS-Roformer via audio-separator) +
# faster-whisper (lyrics). The handler JSON I/O contract is unchanged from
# the previous Demucs-based image — the "demucs" mode name is kept verbatim.
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    PATH="/opt/karaoke-venv/bin:/usr/local/bin:/root/.local/bin:${PATH}" \
    HF_HOME="/root/.cache/huggingface" \
    HF_HUB_DISABLE_XET=1 \
    HF_HUB_DOWNLOAD_TIMEOUT=120 \
    TORCH_HOME="/root/.cache/torch" \
    KARAOKE_SEP_MODEL_DIR="/opt/audio-separator-models" \
    KARAOKE_SEP_MODEL="model_bs_roformer_ep_317_sdr_12.9755.ckpt" \
    PIP_NO_CACHE_DIR=1 \
    UV_NO_CACHE=1

# ffmpeg stays: audio-separator and faster-whisper both decode through it.
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
      build-essential \
      git \
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

# runpod PINNED to 1.9.0: 1.9.1 workers go ready on the platform but never
# consume the queue (#98 r5 wedge). Never float the SDK again.
# Build the Python venv with what the worker imports at runtime:
#   - torch / torchaudio / torchvision CUDA 12.4 wheels (PyTorch cu124 index)
#   - audio-separator[gpu] (BS-Roformer separation; replaces demucs htdemucs)
#   - faster-whisper (large-v3-turbo lyrics transcription)
#   - runpod (Serverless SDK; entrypoint calls runpod.serverless.start)
#
# Dependency-conflict resolution (#98):
#   * audio-separator>=0.32 requires numpy>=2 and torch>=2.3. torch==2.4.* is
#     the first torch release with numpy-2 ABI support (2.3 is NOT numpy-2
#     safe), so numpy 2 is safe across torch/torchaudio/torchvision. numpy 2 +
#     a numpy-2-built ctranslate2 (4.8, faster-whisper's backend) co-resolve.
#   * audio-separator pulls onnx2torch-py313, which pulls torchvision. The
#     latest torchvision pins torch==2.12, which would silently drag torch off
#     the cu124 pin (uv re-resolves installed deps, unlike pip). We therefore
#     PIN torch/torchaudio/torchvision to the 2.4 / 0.19 cu124 line in EVERY
#     install that can touch them, and resolve with the cu124 index as primary
#     + PyPI as extra under `unsafe-best-match` so the cu124 local wheels win
#     while everything else comes from PyPI. Verified torch==2.4.1+cu124,
#     torchaudio==2.4.1+cu124, torchvision==0.19.1+cu124, numpy==2.4.6.
# Demucs is intentionally REMOVED: nothing else in this image imports it.
#
# Reproducibility: audio-separator and the ctc-forced-aligner git ref are
# pinned to the exact versions that resolved in the verified build. NOTE: the
# pushed r5 image (manifest
# sha256:330e7d7926c47f49deeeccaa9f5ad8273c80447cda16b1426328b08058b1d65e) was
# built from the then-unpinned specs just BEFORE these pins were written down;
# the pins record exactly what that image contains -- audio-separator 0.44.2,
# ctc-forced-aligner @ 264e7a1 (upstream default-branch HEAD as of the
# 2026-06-09 build), torch 2.4.1+cu124 / torchaudio 2.4.1+cu124 /
# torchvision 0.19.1+cu124, numpy 2.4.6, ctranslate2 4.8.0.
RUN uv venv --python python3.12 /opt/karaoke-venv \
    && /opt/karaoke-venv/bin/python -m ensurepip --upgrade \
    && uv pip install --python /opt/karaoke-venv/bin/python \
        --index-url https://download.pytorch.org/whl/cu124 \
        --extra-index-url https://pypi.org/simple \
        --index-strategy unsafe-best-match \
        "torch==2.4.*" "torchaudio==2.4.*" "torchvision==0.19.*" \
        "audio-separator[gpu]==0.44.2" \
        "faster-whisper>=1.1.1" \
        "runpod==1.9.0" \
    && uv pip install --python /opt/karaoke-venv/bin/python \
        --index-url https://download.pytorch.org/whl/cu124 \
        --extra-index-url https://pypi.org/simple \
        --index-strategy unsafe-best-match \
        "torch==2.4.*" "torchaudio==2.4.*" "torchvision==0.19.*" \
        "transformers>=4.44,<5" \
        "ctc-forced-aligner @ git+https://github.com/MahmoudAshraf97/ctc-forced-aligner.git@264e7a1f81bff9ff5e787a5537020c2ad0b0df02" \
    && /opt/karaoke-venv/bin/pip uninstall -y hf_xet 2>/dev/null || true

# Pre-cache the BS-Roformer checkpoint (+ its arch config) at build time so
# cold-start does not pay a model-download cost. load_model on CPU only
# downloads/loads weights; runtime uses GPU when torch.cuda is available.
RUN /opt/karaoke-venv/bin/python - <<'PY'
import os
from audio_separator.separator import Separator

model_dir = os.environ["KARAOKE_SEP_MODEL_DIR"]
model = os.environ["KARAOKE_SEP_MODEL"]
sep = Separator(model_file_dir=model_dir, output_format="WAV")
sep.load_model(model_filename=model)
print("BS-Roformer cached:", model, "->", model_dir)
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
from audio_separator.separator import Separator
from faster_whisper import WhisperModel  # noqa: F401
import runpod  # noqa: F401
import ctc_forced_aligner  # noqa: F401
print("torch version:", torch.__version__)
print("torch cuda built:", torch.version.cuda)
print("audio-separator import ok; Separator:", Separator.__name__)
print("faster-whisper import ok")
print("runpod sdk import ok")
print("ctc-forced-aligner import ok")
hf = os.environ.get("HF_HOME", "/root/.cache/huggingface")
assert os.path.isdir(hf + "/hub"), "whisper/aligner model cache missing"
sep_dir = os.environ["KARAOKE_SEP_MODEL_DIR"]
sep_model = os.environ["KARAOKE_SEP_MODEL"]
assert os.path.isfile(os.path.join(sep_dir, sep_model)), "BS-Roformer ckpt cache missing"
print("model caches present:", hf, sep_dir)
PY

# Install the Serverless handler entrypoint. RunPod Serverless invokes
# the handler by running this script; runpod.serverless.start() polls
# the platform's job queue. No HTTP server, no exposed ports.
WORKDIR /work
COPY handler.py /work/handler.py

# Sanity-check the entrypoint imports cleanly (no model load).
RUN /opt/karaoke-venv/bin/python -c "import py_compile; py_compile.compile('/work/handler.py', doraise=True)"

LABEL org.opencontainers.image.title="karaoke RunPod Serverless worker" \
      org.opencontainers.image.description="CUDA 12.4 GPU worker for karaoke on RunPod Serverless: pre-cached BS-Roformer (model_bs_roformer_ep_317_sdr_12.9755, stem separation via audio-separator) + large-v3-turbo (lyrics). Coordinator owns yt-dlp/ffmpeg; this image runs GPU stages only via a JSON-in/JSON-out handler." \
      org.opencontainers.image.version="cuda12.4-r7" \
      org.opencontainers.image.source="https://github.com/BeFeast/karaoke"

ENTRYPOINT ["/opt/karaoke-venv/bin/python", "/work/handler.py"]
