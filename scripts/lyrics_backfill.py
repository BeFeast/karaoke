"""One-off lyrics backfill for non-Latin jobs stuck on the ASR floor (#284).

Finds completed jobs whose lyrics came from raw Whisper ASR
(``lyrics_source`` ``whisper_asr*``) and whose title or track is written in a
non-Latin script, then runs "Rebuild lyrics" on them one at a time —
the lyrics stage only, on each job's stored vocal stem — until the GPU
budget is spent. Prints (and saves) a markdown report: job, title, source
before → after, cost.

    uv run python scripts/lyrics_backfill.py --budget 1.00            # run
    uv run python scripts/lyrics_backfill.py --dry-run                # list only

Talks to the coordinator API (trusted LAN, or ``--token`` for a machine
bearer). Jobs already rebuilt are skipped; the server refuses a second
concurrent rebuild of the same job.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE = "http://10.10.0.13:13140"
_POLL_S = 10.0
_JOB_TIMEOUT_S = 15 * 60


def non_latin(text: str | None) -> bool:
    return any(ch.isalpha() and ord(ch) > 0x024F for ch in (text or ""))


class Api:
    def __init__(self, base: str, token: str | None, http=None) -> None:
        self.base = base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.http = http or httpx.Client(timeout=60)

    def get(self, path: str, **params):
        r = self.http.get(f"{self.base}{path}", params=params or None, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def post(self, path: str) -> httpx.Response:
        return self.http.post(f"{self.base}{path}", headers=self.headers)

    def jobs(self) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            page = self.get("/jobs", limit=100, offset=offset)
            if not page:
                return out
            out += page
            offset += len(page)

    def metadata(self, token: str) -> dict:
        return self.get(f"/share/{token}/metadata.json")


def candidates(api: Api) -> list[tuple[dict, dict]]:
    found = []
    for job in sorted(api.jobs(), key=lambda j: j["id"], reverse=True):
        if job.get("status") != "completed":
            continue
        if not (non_latin(job.get("title")) or non_latin(job.get("track")) or non_latin(job.get("artist"))):
            continue
        try:
            meta = api.metadata(job["job_token"])
        except httpx.HTTPError:
            continue
        if str(meta.get("lyrics_source") or "").startswith("whisper_asr") and not meta.get("lyrics_rebuild"):
            found.append((job, meta))
    return found


def rebuild_one(api: Api, job: dict, *, sleep=time.sleep, clock=time.monotonic) -> dict:
    token = job["job_token"]
    r = api.post(f"/jobs/{job['id']}/lyrics-rebuild")
    if r.status_code != 202:
        return {"error": f"HTTP {r.status_code}: {r.text[:120]}"}
    deadline = clock() + _JOB_TIMEOUT_S
    while clock() < deadline:
        sleep(_POLL_S)
        payload = api.get(f"/share/{token}/lyrics")
        if payload.get("rebuilding"):
            continue
        meta = api.metadata(token)
        if meta.get("lyrics_rebuild"):
            return {"source_after": meta.get("lyrics_source"), "cost": float(meta["lyrics_rebuild"].get("cost") or 0),
                    "reference": meta.get("lyrics_reference_provider") or meta.get("lyrics_match_variant")}
        if payload.get("rebuild_note"):
            return {"error": payload["rebuild_note"]}
    return {"error": "timed out waiting for the rebuild"}


def report(rows: list[dict], budget: float, spent: float) -> str:
    lines = [
        f"# Lyrics backfill — {dt.datetime.now(dt.UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        f"Budget ${budget:.2f}, spent ${spent:.4f}, jobs {len(rows)}.",
        "",
        "| job | title | source before → after | reference | cost | note |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['id']} | {row['title'][:48]} | `{row['before']}` → `{row.get('source_after', '-')}` | "
            f"{row.get('reference') or '-'} | {row.get('cost', 0):.4f} | {row.get('error', '')} |"
        )
    moved = sum(1 for r in rows if r.get("source_after") and not str(r["source_after"]).startswith("whisper_asr"))
    lines += ["", f"Off the ASR floor: {moved}/{len(rows)}."]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--token", default=None, help="machine bearer (default: trusted LAN)")
    ap.add_argument("--budget", type=float, default=1.0, help="stop once this much GPU spend (USD) is used")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    api = Api(args.base, args.token)
    todo = candidates(api)
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(todo)} candidate job(s)", file=sys.stderr)
    rows: list[dict] = []
    spent = 0.0
    for job, meta in todo:
        row = {"id": job["id"], "title": job.get("title") or "", "before": meta.get("lyrics_source")}
        if args.dry_run:
            rows.append(row)
            continue
        if spent >= args.budget:
            row["error"] = "skipped: budget spent"
            rows.append(row)
            continue
        row.update(rebuild_one(api, job))
        spent += float(row.get("cost") or 0)
        rows.append(row)
        print(f"job {job['id']}: {row['before']} -> {row.get('source_after')} {row.get('error', '')}", file=sys.stderr)
    text = report(rows, args.budget, spent)
    print(text)
    out = ROOT / "bench" / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"backfill-{dt.datetime.now(dt.UTC).strftime('%Y%m%dT%H%M%SZ')}.md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
