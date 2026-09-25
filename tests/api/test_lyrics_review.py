"""Quality disclosure and owner corrections, including races and file rollback."""

import json
from pathlib import Path

import pytest

from karaoke.api import lyrics_review

from .test_routes import _clerk_jwt, _seed_job


def _seed(client, settings, tmp_path, monkeypatch, *, state="completed", owner="alice"):
    monkeypatch.setattr(settings, "artifact_root", tmp_path)
    job_id, token = _seed_job(state, owner_subject=owner)
    export = tmp_path / token / "exports"
    export.mkdir(parents=True)
    (export / "lyrics.lrc").write_text("[00:01.00]first words\n[00:04.00]last words\n")
    (export / "lyrics.txt").write_text("first words\nlast words")
    (export / "metadata.json").write_text(
        json.dumps(
            {
                "title": "Test",
                "lyrics_source": "forced_aligned",
                "synced": True,
                "lyrics_quality": {
                    "schema_version": 1,
                    "status": "needs_review",
                    "issues": [{"code": "missing_line", "text": "omitted words", "start": 2.0}],
                },
            }
        )
    )
    headers = {"Authorization": f"Bearer {_clerk_jwt(owner)}"}
    return job_id, token, export, headers


def _body(revision):
    return {
        "lrc": "[00:01.00]first words\n[00:02.00]omitted words\n[00:04.00]last words",
        "expected_revision": revision,
        "confirm": True,
    }


def test_quality_disclosed_and_owner_review_roundtrip(client, settings, tmp_path, monkeypatch):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch)
    initial = client.get(f"/share/{token}/lyrics", headers=auth)
    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "private, no-store"
    data = initial.json()
    assert data["quality"]["status"] == "needs_review"
    assert data["review_job_id"] == job
    result = client.put(f"/jobs/{job}/lyrics-review", json=_body(data["revision"]), headers=auth)
    assert result.status_code == 200, result.text
    updated = result.json()
    assert updated["source"] == "human_reviewed"
    assert updated["quality"]["status"] == "reviewed"
    assert updated["quality"]["issues"] == []
    assert updated["quality"]["automated_before_review"]["status"] == "needs_review"
    assert updated["revision"] != data["revision"]
    assert [x["text"] for x in updated["lines"]] == ["first words", "omitted words", "last words"]
    assert list((export.parent / "work" / "lyrics-reviews").glob("*/metadata.json"))
    assert (export / "lyrics.txt").read_text() == "first words\nomitted words\nlast words"
    assert json.loads((export / "lyrics.quality.json").read_text())["status"] == "reviewed"


def test_share_reader_cannot_edit_someone_elses_lyrics(client, settings, tmp_path, monkeypatch):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch)
    other = {"Authorization": f"Bearer {_clerk_jwt('bob')}"}
    initial = client.get(f"/share/{token}/lyrics", headers=other).json()
    assert initial["quality"]["status"] == "needs_review"
    assert initial["review_job_id"] is None
    original = lyrics_review.snapshot(export)
    result = client.put(
        f"/jobs/{job}/lyrics-review", json=_body(initial["revision"]), headers=other
    )
    assert result.status_code == 404
    assert lyrics_review.snapshot(export) == original


def test_stale_revision_preserves_newer_edit(client, settings, tmp_path, monkeypatch):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch)
    data = client.get(f"/share/{token}/lyrics", headers=auth).json()
    first = client.put(f"/jobs/{job}/lyrics-review", json=_body(data["revision"]), headers=auth)
    assert first.status_code == 200
    original = lyrics_review.snapshot(export)
    second = client.put(f"/jobs/{job}/lyrics-review", json=_body(data["revision"]), headers=auth)
    assert second.status_code == 409
    assert lyrics_review.snapshot(export) == original


@pytest.mark.parametrize(
    "text",
    [
        "untimed text",
        "[00:90.00]bad seconds",
        "[00:01.00]",
        "[00:02.00]later\n[00:01.00]earlier",
        "[00:02.00]<00:02.00>first <00:01.00>second",
        "[00:01.00]text <bad>",
    ],
)
def test_invalid_correction_never_changes_files(client, settings, tmp_path, monkeypatch, text):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch)
    original = lyrics_review.snapshot(export)
    body = _body(lyrics_review.revision(original))
    body["lrc"] = text
    result = client.put(f"/jobs/{job}/lyrics-review", json=body, headers=auth)
    assert result.status_code == 422
    assert lyrics_review.snapshot(export) == original


def test_confirmation_is_required(client, settings, tmp_path, monkeypatch):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch)
    body = _body(lyrics_review.revision(lyrics_review.snapshot(export)))
    body["confirm"] = False
    assert client.put(f"/jobs/{job}/lyrics-review", json=body, headers=auth).status_code == 422


def test_in_progress_cannot_be_reviewed(client, settings, tmp_path, monkeypatch):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch, state="transcribing")
    data = client.get(f"/share/{token}/lyrics", headers=auth).json()
    assert data["review_job_id"] is None
    assert (
        client.put(
            f"/jobs/{job}/lyrics-review", json=_body(data["revision"]), headers=auth
        ).status_code
        == 409
    )


def test_legacy_has_no_invented_quality(client, settings, tmp_path, monkeypatch):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch)
    (export / "metadata.json").write_text("{}")
    data = client.get(f"/share/{token}/lyrics", headers=auth).json()
    assert data["quality"] is None
    assert data["revision"]


def test_write_failure_restores_all_previous_files(client, settings, tmp_path, monkeypatch):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch)
    original = lyrics_review.snapshot(export)
    real = lyrics_review._replace
    failed = False

    def fail_once(path: Path, data: bytes):
        nonlocal failed
        if path.name == "metadata.json" and not failed:
            failed = True
            raise OSError("test disk failure")
        real(path, data)

    monkeypatch.setattr(lyrics_review, "_replace", fail_once)
    with pytest.raises(OSError, match="test disk failure"):
        client.put(
            f"/jobs/{job}/lyrics-review", json=_body(lyrics_review.revision(original)), headers=auth
        )
    assert lyrics_review.snapshot(export) == original


def test_duration_bound():
    with pytest.raises(Exception, match="outside the audio"):
        lyrics_review.validate_lrc("[02:00.00]after the song", 60)


@pytest.mark.parametrize(
    "text",
    [
        "[00:01.00]first line\n[00:01.00]unreachable line",
        "[00:01.00]untagged first <00:02.00>word",
        "[00:01.00]<00:01.00><00:02.00>word",
        "[00:01.00]<00:01.00>word <00:02.00> <00:03.00>",
        "[00:03.01]<00:01.00>too early <00:04.00>",
        "[100:00.00]unsupported timestamp",
    ],
)
def test_correction_rejects_timing_the_player_cannot_render(
    client, settings, tmp_path, monkeypatch, text
):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch)
    original = lyrics_review.snapshot(export)
    body = _body(lyrics_review.revision(original))
    body["lrc"] = text
    result = client.put(f"/jobs/{job}/lyrics-review", json=body, headers=auth)
    assert result.status_code == 422, result.text
    assert lyrics_review.snapshot(export) == original


def test_unchanged_enhanced_lyrics_with_early_first_word_can_be_reviewed(
    client, settings, tmp_path, monkeypatch
):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch)
    lrc = "[00:01.30]<00:01.00>first <00:01.80>words <00:02.50>\n[00:04.00]<00:03.70>last <00:04.20>words <00:05.00>\n"
    (export / "lyrics.lrc").write_text(lrc)
    original = lyrics_review.snapshot(export)
    body = _body(lyrics_review.revision(original))
    body["lrc"] = lrc
    result = client.put(f"/jobs/{job}/lyrics-review", json=body, headers=auth)
    assert result.status_code == 200, result.text
    assert result.json()["lrc"] == lrc
    assert result.json()["lines"][0]["words"][0]["t"] == 1.0
    assert result.json()["quality"]["status"] == "reviewed"


@pytest.mark.parametrize(
    "text",
    [
        "[00:01.00]<00:01.00>one <00:01.00>two <00:02.00>",
        "[00:01.00]<00:01.00>one <00:02.00>two <00:02.00>",
    ],
)
def test_collapsed_word_spans_cannot_be_marked_reviewed(
    client, settings, tmp_path, monkeypatch, text
):
    job, token, export, auth = _seed(client, settings, tmp_path, monkeypatch)
    original = lyrics_review.snapshot(export)
    body = _body(lyrics_review.revision(original))
    body["lrc"] = text
    result = client.put(f"/jobs/{job}/lyrics-review", json=body, headers=auth)
    assert result.status_code == 422, result.text
    assert lyrics_review.snapshot(export) == original


class _ReviewSession:
    """Model row-lock release with a waiting writer at transaction rollback."""

    def __init__(self, job, *, on_rollback=None, commit_failures=0):
        from types import SimpleNamespace
        self.job = job
        self.artifacts = [SimpleNamespace(id=1, kind="lyrics", size_bytes=1),
                          SimpleNamespace(id=2, kind="lyrics_lrc", size_bytes=1)]
        self.on_rollback = on_rollback
        self.commit_failures = commit_failures
        self.commits = 0
        self.relocks = 0

    async def scalars(self, query):
        from types import SimpleNamespace
        return SimpleNamespace(all=lambda: self.artifacts)

    async def scalar(self, query):
        assert "FOR UPDATE" in str(query)
        self.relocks += 1
        return self.job

    async def commit(self):
        self.commits += 1
        if self.commits <= self.commit_failures:
            raise ConnectionError("lost commit acknowledgement")

    async def rollback(self):
        if self.on_rollback:
            callback, self.on_rollback = self.on_rollback, None
            callback()

    def add(self, artifact):
        self.artifacts.append(artifact)


def _direct_review(tmp_path):
    from types import SimpleNamespace
    export = tmp_path / "token" / "exports"
    export.mkdir(parents=True)
    (export / "lyrics.lrc").write_text("[00:01.00]original words\n")
    (export / "lyrics.txt").write_text("original words")
    (export / "metadata.json").write_text("{}")
    job = SimpleNamespace(id=42, job_token="token", duration=30,
                          status="completed", stage_note="Needs review")
    return job, export, lyrics_review.snapshot(export)


def test_file_failure_restores_before_releasing_lock_to_waiting_review(tmp_path, monkeypatch):
    import asyncio
    job, export, original = _direct_review(tmp_path)
    newer = b"[00:01.00]later human edit\n"
    def waiting_review():
        # A queued writer can only run once rollback releases the row lock.
        assert lyrics_review.snapshot(export) == original
        (export / "lyrics.lrc").write_bytes(newer)
    session = _ReviewSession(job, on_rollback=waiting_review)
    real = lyrics_review._replace
    failed = False
    def fail_once(path, data):
        nonlocal failed
        if path.name == "metadata.json" and not failed:
            failed = True
            raise OSError("disk failure before commit")
        real(path, data)
    monkeypatch.setattr(lyrics_review, "_replace", fail_once)
    with pytest.raises(OSError, match="disk failure before commit"):
        asyncio.run(lyrics_review.save_review(session, job, export,
            "[00:01.00]my correction", lyrics_review.revision(original), "alice"))
    assert (export / "lyrics.lrc").read_bytes() == newer
    assert session.commits == 0


@pytest.mark.parametrize("commit_failures", [1, 2])
def test_commit_failure_keeps_saved_files_and_reconciles_under_new_lock(tmp_path, commit_failures):
    import asyncio

    from fastapi import HTTPException
    job, export, original = _direct_review(tmp_path)
    session = _ReviewSession(job, commit_failures=commit_failures)
    with pytest.raises(HTTPException) as error:
        asyncio.run(lyrics_review.save_review(session, job, export,
            "[00:01.00]my correction", lyrics_review.revision(original), "alice"))
    assert error.value.status_code == 503 and "Reload" in error.value.detail
    assert session.relocks == 1 and session.commits == 2
    current = lyrics_review.snapshot(export)
    assert current["lyrics.lrc"] == b"[00:01.00]my correction\n"
    assert json.loads(current["metadata.json"])["lyrics_quality"]["status"] == "reviewed"
    assert session.artifacts[0].size_bytes == len(current["lyrics.txt"])
    assert session.artifacts[1].size_bytes == len(current["lyrics.lrc"])
    assert job.stage_note is None
    # Original files remain available even if the second commit is uncertain.
    backups = list((export.parent / "work" / "lyrics-reviews").glob("*/lyrics.lrc"))
    assert backups[0].read_bytes() == original["lyrics.lrc"]


def test_commit_failure_never_overwrites_waiting_human_review(tmp_path):
    import asyncio

    from fastapi import HTTPException
    job, export, original = _direct_review(tmp_path)
    newer = b"[00:01.00]later confirmed edit\n"
    def waiting_review():
        (export / "lyrics.lrc").write_bytes(newer)
        session.artifacts[1].size_bytes = len(newer)
        job.stage_note = "later writer state"
    session = _ReviewSession(job, on_rollback=waiting_review, commit_failures=1)
    with pytest.raises(HTTPException) as error:
        asyncio.run(lyrics_review.save_review(session, job, export,
            "[00:01.00]my correction", lyrics_review.revision(original), "alice"))
    assert error.value.status_code == 503
    assert (export / "lyrics.lrc").read_bytes() == newer
    assert session.commits == 1 and session.relocks == 1
    assert session.artifacts[1].size_bytes == len(newer)
    assert job.stage_note == "later writer state"


def test_commit_recovery_does_not_modify_job_that_started_processing(tmp_path):
    import asyncio

    from fastapi import HTTPException
    job, export, original = _direct_review(tmp_path)
    def resumed():
        job.status = "transcribing"
        job.stage_note = "new processing"
    session = _ReviewSession(job, on_rollback=resumed, commit_failures=1)
    with pytest.raises(HTTPException):
        asyncio.run(lyrics_review.save_review(session, job, export,
            "[00:01.00]my correction", lyrics_review.revision(original), "alice"))
    assert session.commits == 1
    assert job.stage_note == "new processing"



def test_cancelled_commit_does_not_start_recovery_or_overwrite_waiter(tmp_path):
    import asyncio
    job, export, original = _direct_review(tmp_path)
    newer = b"[00:01.00]later confirmed edit\n"
    session = _ReviewSession(job, on_rollback=lambda: (export / "lyrics.lrc").write_bytes(newer))
    async def cancelled():
        raise asyncio.CancelledError()
    session.commit = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(lyrics_review.save_review(session, job, export,
            "[00:01.00]my correction", lyrics_review.revision(original), "alice"))
    assert session.relocks == 0
    assert (export / "lyrics.lrc").read_bytes() == newer
