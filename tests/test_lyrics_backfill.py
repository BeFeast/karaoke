"""Backfill driver (#284) against a scripted API — no network."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("lyrics_backfill", Path(__file__).parents[1] / "scripts" / "lyrics_backfill.py")
bf = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bf
SPEC.loader.exec_module(bf)


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body, self.text = code, body, str(body)

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise bf.httpx.HTTPStatusError("x", request=None, response=None)


class _Http:
    def __init__(self):
        self.jobs = [
            {"id": 3, "job_token": "c", "status": "completed", "title": "עדן בן זקן - שיר"},
            {"id": 2, "job_token": "b", "status": "completed", "title": "Latin Song"},
            {"id": 1, "job_token": "a", "status": "completed", "title": "נועה קירל - לאב סונג"},
        ]
        self.meta = {"a": {"lyrics_source": "whisper_asr_synced"}, "b": {"lyrics_source": "whisper_asr_synced"},
                     "c": {"lyrics_source": "lrclib_synced"}}
        self.posts = []

    def get(self, url, params=None, headers=None):
        if url.endswith("/jobs"):
            return _Resp(200, self.jobs if not params or params.get("offset", 0) == 0 else [])
        token = url.split("/share/")[1].split("/")[0]
        if url.endswith("/metadata.json"):
            return _Resp(200, self.meta[token])
        return _Resp(200, {"synced": True})

    def post(self, url, headers=None):
        job_id = int(url.split("/jobs/")[1].split("/")[0])
        self.posts.append(job_id)
        token = {1: "a", 2: "b", 3: "c"}[job_id]
        self.meta[token] = {"lyrics_source": "lrclib_synced", "lyrics_match_variant": "canonical:Noa Kirel",
                            "lyrics_rebuild": {"cost": 0.004}}
        return _Resp(202, {"status": "started"})


def test_only_non_latin_asr_floor_jobs_are_candidates():
    api = bf.Api("http://x", None, http=_Http())
    assert [j["id"] for j, _ in bf.candidates(api)] == [1]


def test_rebuild_one_polls_until_the_rebuild_lands():
    http = _Http()
    api = bf.Api("http://x", None, http=http)
    job = http.jobs[2]
    row = bf.rebuild_one(api, job, sleep=lambda s: None)
    assert http.posts == [1]
    assert row == {"source_after": "lrclib_synced", "cost": 0.004, "reference": "canonical:Noa Kirel"}


def test_report_counts_jobs_off_the_floor():
    text = bf.report([
        {"id": 1, "title": "t", "before": "whisper_asr_synced", "source_after": "lrclib_synced", "cost": 0.004},
        {"id": 2, "title": "u", "before": "whisper_asr_synced", "source_after": "whisper_asr_synced", "cost": 0.004},
    ], 1.0, 0.008)
    assert "Off the ASR floor: 1/2." in text and "`whisper_asr_synced` → `lrclib_synced`" in text
