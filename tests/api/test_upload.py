"""Tests for ``POST /jobs/upload`` — audio-file upload instead of a URL (#172).

The endpoint streams the multipart body to ``{artifact_root}/.uploads/*.part``,
moves it to ``{artifact_root}/{job_token}/work/source.audio`` and only then
creates the Job row, so every failure mode below asserts BOTH "no Job row" and
"no leftover files". ``schedule_job`` is monkeypatched throughout — the worker
pipeline half lives in ``tests/worker/test_pipeline_upload.py``.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from karaoke.config import reset_settings_for_tests
from karaoke.db.models import JobStatus

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "upload_tone.mp3"

UPLOAD_URL = "/jobs/upload"


@pytest.fixture(autouse=True)
def artifact_root(monkeypatch: pytest.MonkeyPatch, tmp_path) -> Iterator[Path]:
    """Point the artifact root at a writable tmp dir (the conftest leaves the
    default ``/srv/artifacts``, which is absent/unwritable in CI)."""
    root = tmp_path / "artifacts"
    monkeypatch.setenv("KARAOKE_ARTIFACT_ROOT", str(root))
    reset_settings_for_tests()
    yield root


@pytest.fixture
def schedule_calls(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Stub the worker dispatch; records the job ids handed to it."""
    calls: list[int] = []
    monkeypatch.setattr(
        "karaoke.api.routes.schedule_job",
        lambda factory, job_id, settings: calls.append(job_id),
    )
    return calls


def _files_under(root: Path) -> set[str]:
    if not root.exists():
        return set()
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


def _post_upload(client, *, filename: str = "upload_tone.mp3", data: dict | None = None):
    with FIXTURE.open("rb") as fh:
        return client.post(
            UPLOAD_URL,
            files={"file": (filename, fh, "audio/mpeg")},
            data=data or {},
        )


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_upload_creates_queued_job_and_stages_source(client, artifact_root, schedule_calls):
    resp = _post_upload(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == JobStatus.queued.value
    assert body["source_url"] == "upload://upload_tone.mp3"
    assert body["title"] is None
    assert body["artifacts"] == []
    assert schedule_calls == [body["id"]]

    staged = artifact_root / body["job_token"] / "work" / "source.audio"
    assert staged.read_bytes() == FIXTURE.read_bytes()
    # No .part left behind; nothing outside the job dir.
    assert _files_under(artifact_root) == {
        str(Path(body["job_token"]) / "work" / "source.audio")
    }


def test_upload_title_field_lands_on_row(client, schedule_calls):
    resp = _post_upload(client, data={"title": "My Recording"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "My Recording"

    status = client.get(f"/jobs/{body['id']}/status")
    assert status.status_code == 200
    assert status.json()["title"] == "My Recording"


def test_upload_sanitizes_path_traversal_filename(client, artifact_root, schedule_calls):
    resp = _post_upload(client, filename="../../evil.mp3")
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source_url"] == "upload://evil.mp3"
    # Nothing written outside {artifact_root}/{job_token}/.
    assert _files_under(artifact_root) == {
        str(Path(body["job_token"]) / "work" / "source.audio")
    }


def test_upload_sanitizes_backslash_traversal(client, schedule_calls):
    resp = _post_upload(client, filename="..\\..\\windows evil.mp3")
    assert resp.status_code == 201, resp.text
    assert resp.json()["source_url"] == "upload://windows evil.mp3"


# ---------------------------------------------------------------------------
# rejects: extension / missing file / size cap
# ---------------------------------------------------------------------------


def _assert_no_job_and_no_files(client, artifact_root: Path) -> None:
    assert client.get("/jobs").json() == []
    # Not even an empty .uploads/ directory may be left behind.
    assert not (artifact_root / ".uploads").exists()
    assert _files_under(artifact_root) == set()


def test_upload_rejects_disallowed_extension(client, artifact_root, schedule_calls):
    resp = _post_upload(client, filename="notes.txt")
    assert resp.status_code == 415, resp.text
    assert schedule_calls == []
    _assert_no_job_and_no_files(client, artifact_root)


def test_upload_rejects_missing_file_field(client, artifact_root, schedule_calls):
    resp = client.post(UPLOAD_URL, data={"title": "no file"})
    assert resp.status_code == 422, resp.text
    assert schedule_calls == []
    _assert_no_job_and_no_files(client, artifact_root)


def test_upload_413_via_content_length_fast_path(
    monkeypatch, client, artifact_root, schedule_calls
):
    monkeypatch.setenv("KARAOKE_MAX_UPLOAD_BYTES", "1024")
    reset_settings_for_tests()
    resp = _post_upload(client)  # fixture is ~4.4 KB > 1 KB cap
    assert resp.status_code == 413, resp.text
    # Value-free error: no filename, no sizes echoed back.
    assert resp.json() == {"detail": "upload too large"}
    assert schedule_calls == []
    _assert_no_job_and_no_files(client, artifact_root)


def test_upload_413_via_streamed_overrun(monkeypatch, client, artifact_root, schedule_calls):
    """No Content-Length (chunked body) → the byte-counting copy loop is the
    authoritative cap enforcement; the .part is discarded."""
    monkeypatch.setenv("KARAOKE_MAX_UPLOAD_BYTES", "1024")
    reset_settings_for_tests()

    boundary = "karaoke-test-boundary"
    body = (
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="big.mp3"\r\n'
            "Content-Type: audio/mpeg\r\n\r\n"
        ).encode()
        + b"x" * 4096
        + f"\r\n--{boundary}--\r\n".encode()
    )

    def chunks() -> Iterator[bytes]:
        yield body

    resp = client.post(
        UPLOAD_URL,
        content=chunks(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert resp.status_code == 413, resp.text
    assert resp.json() == {"detail": "upload too large"}
    assert schedule_calls == []
    assert client.get("/jobs").json() == []
    # The streamed .part was discarded; no job dir was created.
    assert _files_under(artifact_root) == set()


# ---------------------------------------------------------------------------
# ordering: file move precedes row creation
# ---------------------------------------------------------------------------


def test_upload_row_insert_failure_leaves_no_files(
    monkeypatch, client, artifact_root, schedule_calls
):
    """A forced row-insert failure after the file move leaves nothing under
    the artifact root (the staged job dir is rolled back)."""
    from sqlalchemy.ext.asyncio import AsyncSession

    async def boom(self):
        raise RuntimeError("forced commit failure")

    monkeypatch.setattr(AsyncSession, "commit", boom)
    with pytest.raises(RuntimeError, match="forced commit failure"):
        _post_upload(client)
    assert schedule_calls == []
    assert _files_under(artifact_root) == set()


# ---------------------------------------------------------------------------
# auth + sentinel guard
# ---------------------------------------------------------------------------


def test_upload_unauthenticated_non_lan_is_rejected(monkeypatch, client, artifact_root):
    monkeypatch.setenv("KARAOKE_TRUSTED_CIDRS", "")
    reset_settings_for_tests()
    resp = _post_upload(client)
    assert resp.status_code == 401
    assert _files_under(artifact_root) == set()


def test_post_jobs_rejects_upload_scheme(client):
    resp = client.post("/jobs", json={"url": "upload://x"})
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# share page display fallback
# ---------------------------------------------------------------------------


def test_share_html_renders_filename_not_sentinel(client, schedule_calls):
    """An upload job without a title falls back to the filename in the share
    page ``<title>``/heading — never the raw ``upload://`` sentinel."""
    resp = _post_upload(client)
    assert resp.status_code == 201, resp.text
    token = resp.json()["job_token"]

    page = client.get(f"/share/{token}", headers={"Accept": "text/html"})
    assert page.status_code == 200, page.text
    assert "upload_tone.mp3" in page.text
    assert "upload://" not in page.text
