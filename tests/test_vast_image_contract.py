from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "docker/vast/Dockerfile"
ENTRYPOINT = ROOT / "docker/vast/entrypoint.py"
README = ROOT / "docker/vast/README.md"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _entrypoint() -> str:
    return ENTRYPOINT.read_text(encoding="utf-8")


def test_vast_image_uses_cuda_12_4_and_uv_entrypoint():
    dockerfile = _dockerfile()
    assert "FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04" in dockerfile
    assert 'ENTRYPOINT ["uv", "run", "--no-project", "--active", "python", "/work/entrypoint.py"]' in dockerfile
    assert "COPY entrypoint.py /work/entrypoint.py" in dockerfile
    assert "COPY server.py" not in dockerfile
    assert "EXPOSE 8000" not in dockerfile


def test_vast_image_precaches_required_models_and_cuda_torch():
    dockerfile = _dockerfile()
    assert "--index-url https://download.pytorch.org/whl/cu124" in dockerfile
    assert '"torch==2.4.*" "torchaudio==2.4.*"' in dockerfile
    assert "get_model(\"htdemucs\")" in dockerfile
    assert "WhisperModel(\"large-v3-turbo\", device=\"cpu\", compute_type=\"int8\")" in dockerfile
    assert '"faster-whisper>=1.1.1"' in dockerfile
    assert '"demucs>=4.0.1"' in dockerfile


def test_vast_entrypoint_fails_fast_on_cpu_and_uses_float16_whisper():
    entrypoint = _entrypoint()
    assert "nvidia-smi" in entrypoint
    assert "torch.cuda.is_available()" in entrypoint
    assert "refusing CPU fallback" in entrypoint
    assert 'WhisperModel("large-v3-turbo", device="cuda", compute_type="float16")' in entrypoint


def test_vast_entrypoint_contract_writes_expected_artifacts():
    entrypoint = _entrypoint()
    for artifact in ("vocals.mp3", "no_vocals.mp3", "lyrics.txt", "lyrics.json"):
        assert artifact in entrypoint
    assert "demucs.separate" in entrypoint
    assert '"htdemucs"' in entrypoint
    assert '"--two-stems"' in entrypoint
    assert '"vocals"' in entrypoint
    assert "libmp3lame" in entrypoint


def test_vast_readme_documents_build_push_smoke_and_weight_contract():
    readme = README.read_text(encoding="utf-8")
    for needle in (
        "docker buildx build",
        "ghcr.io/befeast/karaoke-vast:cuda12.4",
        "docker push",
        "docker pull",
        "vocals.mp3",
        "no_vocals.mp3",
        "lyrics.txt",
        "nvidia-smi",
        "htdemucs",
        "large-v3-turbo",
        "Actual image size",
    ):
        assert needle in readme
