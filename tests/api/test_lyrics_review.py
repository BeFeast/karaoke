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
