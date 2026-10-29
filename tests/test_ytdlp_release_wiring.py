from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _deps() -> set[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return set(pyproject["project"]["dependencies"])


def test_ytdlp_stack_is_exactly_pinned():
    deps = _deps()
    assert "yt-dlp==2026.10.26" in deps
    assert "yt-dlp-ejs==0.8.0" in deps
    assert not any(dep.startswith("yt-dlp>=") for dep in deps)


def test_pot_plugin_major_matches_documented_sidecar():
    # The bgutil plugin rejects a provider server of another major version, so
    # the plugin's upper bound has to track the sidecar image tag (#330).
    readme = (ROOT / "docker/api/README.md").read_text()
    tags = set(re.findall(r"brainicism/bgutil-ytdlp-pot-provider:(\d+)\.\d+\.\d+", readme))
    assert len(tags) == 1, tags
    sidecar_major = int(tags.pop())
    (plugin,) = [dep for dep in _deps() if dep.startswith("bgutil-ytdlp-pot-provider")]
    assert f"<{sidecar_major + 1}" in plugin.split(","), plugin


def test_coordinator_image_caches_js_runtime_before_python_dependencies():
    dockerfile = (ROOT / "docker/api/Dockerfile").read_text()
    apt_node = dockerfile.index("nodejs")
    deno = dockerfile.index("ARG DENO_VERSION")
    py_deps = dockerfile.index("RUN uv sync --frozen --no-dev --no-install-project")
    assert apt_node < py_deps
    assert deno < py_deps
    assert "node --version" in dockerfile
    assert "deno --version" in dockerfile


def test_gpu_images_do_not_ship_ytdlp_or_ejs_solver():
    for rel in ("docker/runpod/Dockerfile", "docker/vast/Dockerfile"):
        dockerfile = (ROOT / rel).read_text().lower()
        instructions = [
            line.strip()
            for line in dockerfile.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        install_or_copy = "\n".join(
            line
            for line in instructions
            if line.startswith(("run ", "copy ", "add "))
        )
        assert "yt-dlp" not in install_or_copy
        assert "yt_dlp" not in install_or_copy
        assert "yt-dlp-ejs" not in install_or_copy
        assert "deno" not in install_or_copy
        assert "nodejs" not in install_or_copy
        assert "npm" not in install_or_copy
