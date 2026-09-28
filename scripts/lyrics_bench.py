"""Lyrics accuracy benchmark for non-Latin (Hebrew-first) tracks (#280).

Two subcommands:

* ``fetch-refs`` — pull reference texts for every song in ``bench/songs.json``
  (LRCLIB by id or by artist/title/duration, Genius by URL or search) into the
  git-ignored ``bench/refs/`` directory. Reference texts are never committed.
* ``score`` — compare a hypothesis per song against its reference and print a
  markdown table (source, CER, WER, CER without ו/י, missing sung regions)
  plus per-group medians. Hypotheses come from a live job's share URL
  (``<share>/lyrics``), a local file (``.lrc`` / faster-whisper
  ``lyrics.json`` / plain ``.txt``), or a git-ignored ``bench/jobs.json``
  mapping ``song id -> share URL`` for batch runs.

Run with ``uv run python scripts/lyrics_bench.py <subcommand> …``.
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from karaoke.textnorm import align, cer, normalize, skeleton, wer
from karaoke.worker.genius import GeniusSource
from karaoke.worker.lyrics import LyricsSource

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SONGS = ROOT / "bench" / "songs.json"
DEFAULT_REFS = ROOT / "bench" / "refs"
DEFAULT_JOBS = ROOT / "bench" / "jobs.json"
DEFAULT_OUT = ROOT / "bench" / "out"

_LRC_TAG_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")
_LRC_STRIP_RE = re.compile(r"\[\d{1,2}:\d{2}(?:[.:]\d{1,3})?\]|<\d{1,2}:\d{2}(?:[.:]\d{1,3})?>")
_LRC_META_RE = re.compile(r"^\[[a-zA-Z]+:[^\]]*\]$")
# A reference line counts as "sung and present" when at least this share of
# its tokens aligned to hypothesis tokens. A substituted token still counts
# when it is the same word in another spelling (equal ו/י skeleton or a
# close string match) — that is a word error, not a missing region.
_LINE_PRESENT_MIN = 0.3
_TOKEN_PRESENT_RATIO = 0.75


@dataclass
class Line:
    text: str
    start: float | None = None


@dataclass
class Text:
    lines: list[Line]
    source: str = ""

    @property
    def plain(self) -> str:
        return "\n".join(ln.text for ln in self.lines)

    @property
    def timed(self) -> bool:
        return any(ln.start is not None for ln in self.lines)


# -- parsing ---------------------------------------------------------------

def _tag_seconds(match: re.Match) -> float:
    frac = match[3] or "0"
    return int(match[1]) * 60 + int(match[2]) + int(frac) / (10 ** len(frac))


def parse_lrc(body: str) -> Text:
    lines: list[Line] = []
    for raw in body.splitlines():
        raw = raw.strip()
        if not raw or _LRC_META_RE.match(raw):
            continue
        stamps = [_tag_seconds(m) for m in _LRC_TAG_RE.finditer(raw)]
        text = _LRC_STRIP_RE.sub(" ", raw).strip()
        if not text:
            continue
        lines.append(Line(text=re.sub(r"\s{2,}", " ", text), start=min(stamps) if stamps else None))
    lines.sort(key=lambda ln: (ln.start is None, ln.start or 0.0))
    return Text(lines=lines)


def parse_plain(body: str) -> Text:
    return Text(lines=[Line(text=ln.strip()) for ln in body.splitlines() if ln.strip()])


def parse_whisper_json(body: str) -> Text:
    data = json.loads(body)
    segments = data.get("segments") if isinstance(data, dict) else data
    lines = [
        Line(text=str(seg.get("text") or "").strip(), start=seg.get("start"))
        for seg in segments or []
        if str(seg.get("text") or "").strip()
    ]
    return Text(lines=lines, source="whisper_asr")


def load_text_file(path: Path) -> Text:
    body = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        return parse_whisper_json(body)
    if path.suffix == ".lrc" or _LRC_TAG_RE.search(body):
        return parse_lrc(body)
    return parse_plain(body)


def load_share(share_url: str, *, http=httpx.get) -> Text:
    """Hypothesis from a live job: ``GET <share>/lyrics`` (LyricsPayload)."""
    resp = http(share_url.rstrip("/") + "/lyrics", timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("synced") and payload.get("lrc"):
        text = parse_lrc(payload["lrc"])
    else:
        text = parse_plain(payload.get("plain") or "")
    text.source = str(payload.get("source") or "")
    return text


# -- references --------------------------------------------------------------

def load_songs(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    songs = data["songs"]
    ids = [s["id"] for s in songs]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate song ids in " + str(path))
    return songs


def ref_paths(refs_dir: Path, song_id: str) -> dict[str, Path]:
    return {
        "lrclib_lrc": refs_dir / f"{song_id}.lrclib.lrc",
        "lrclib_txt": refs_dir / f"{song_id}.lrclib.txt",
        "genius_txt": refs_dir / f"{song_id}.genius.txt",
        "sources": refs_dir / f"{song_id}.sources.json",
    }


def load_reference(refs_dir: Path, song: dict, prefer: str) -> tuple[Text | None, str, Text | None]:
    """``(text_reference, name, timed_reference)``.

    ``prefer`` is ``auto`` (a synced LRCLIB record — curated against the
    recording, repeats included — else Genius, else plain LRCLIB),
    ``genius`` or ``lrclib``. Genius pages collapse repeated choruses and
    add intro ad-libs, so they serve as the *independent* second reference
    rather than the default. The timed reference (LRCLIB synced) drives the
    missing sung regions report regardless of which text is scored.
    """
    paths = ref_paths(refs_dir, song["id"])
    timed = parse_lrc(paths["lrclib_lrc"].read_text(encoding="utf-8")) if paths["lrclib_lrc"].exists() else None
    lrclib = timed or (parse_plain(paths["lrclib_txt"].read_text(encoding="utf-8")) if paths["lrclib_txt"].exists() else None)
    genius = parse_plain(paths["genius_txt"].read_text(encoding="utf-8")) if paths["genius_txt"].exists() else None
    order = {"auto": [("lrclib", timed), ("genius", genius), ("lrclib", lrclib)],
             "genius": [("genius", genius)],
             "lrclib": [("lrclib", lrclib)]}[prefer]
    for name, text in order:
        if text is not None and text.lines:
            return text, name, timed
    return None, "", timed


def fetch_refs(songs: list[dict], refs_dir: Path, *, force: bool, only: set[str],
               lrclib: LyricsSource, genius: GeniusSource) -> list[dict]:
    refs_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for song in songs:
        if only and song["id"] not in only:
            continue
        paths = ref_paths(refs_dir, song["id"])
        row = {"id": song["id"], "lrclib": "-", "genius": "-"}
        sources: dict = {}
        if force or not (paths["lrclib_lrc"].exists() or paths["lrclib_txt"].exists()):
            if song.get("lrclib_id"):
                result = lrclib.get_by_id(int(song["lrclib_id"]))
            else:
                result = lrclib.fetch(
                    artist=song.get("artist_latin") or song.get("artist"),
                    track=song["title"],
                    duration=song.get("audio_duration_s"),
                )
            if result.synced_lrc:
                paths["lrclib_lrc"].write_text(result.synced_lrc, encoding="utf-8")
            if result.plain:
                paths["lrclib_txt"].write_text(result.plain, encoding="utf-8")
            row["lrclib"] = "synced" if result.synced_lrc else ("plain" if result.plain else "miss")
            sources["lrclib"] = {"source": result.source, "match_variant": result.match_variant}
            time.sleep(0.5)
        else:
            row["lrclib"] = "cached"
        if force or not paths["genius_txt"].exists():
            text = url = None
            if song.get("genius_url"):
                url = song["genius_url"]
                text = genius.fetch_url(url)
            else:
                hit = genius.fetch(song.get("artist_latin") or song.get("artist"), song["title"],
                                   lang=song.get("language"))
                if hit is not None:
                    text, url = hit.text, hit.url
            if text:
                paths["genius_txt"].write_text(text, encoding="utf-8")
            row["genius"] = "ok" if text else "miss"
            sources["genius"] = {"url": url}
            time.sleep(0.5)
        else:
            row["genius"] = "cached"
        if sources:
            paths["sources"].write_text(json.dumps(sources, ensure_ascii=False, indent=1), encoding="utf-8")
        rows.append(row)
    return rows


# -- scoring -----------------------------------------------------------------

@dataclass
class Score:
    id: str
    group: str
    source: str
    reference: str
    cer: float | None = None
    wer: float | None = None
    cer_skel: float | None = None
    ref_lines: int = 0
    missing_lines: int = 0
    missing_regions: list[str] = field(default_factory=list)
    note: str = ""


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    return f"{int(seconds) // 60:02d}:{int(seconds) % 60:02d}"


def _same_word(a: str, b: str) -> bool:
    if skeleton(a) and skeleton(a) == skeleton(b):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= _TOKEN_PRESENT_RATIO


def missing_regions(reference: Text, hypothesis: Text, lang: str | None) -> tuple[int, list[str]]:
    """Runs of reference lines with < 30 % of their tokens present in the
    hypothesis, as ``L<a>-L<b> [mm:ss-mm:ss]`` labels (times when the
    reference is synced)."""
    ref_tokens: list[str] = []
    owner: list[int] = []
    for idx, line in enumerate(reference.lines):
        toks = normalize(line.text, lang, strip_parenthetical=True).split()
        ref_tokens.extend(toks)
        owner.extend([idx] * len(toks))
    hyp_tokens = normalize(hypothesis.plain, lang, strip_parenthetical=True).split()
    matched = [0] * len(reference.lines)
    total = [0] * len(reference.lines)
    for idx in owner:
        total[idx] += 1
    for op, ref_idx, hyp_idx in align(ref_tokens, hyp_tokens):
        if op == "match" or (op == "sub" and _same_word(ref_tokens[ref_idx], hyp_tokens[hyp_idx])):
            matched[owner[ref_idx]] += 1
    missing = [i for i in range(len(reference.lines)) if total[i] and matched[i] / total[i] < _LINE_PRESENT_MIN]
    regions: list[str] = []
    start = prev = None
    for i in missing + [None]:
        if start is not None and (i is None or i != prev + 1):
            first = reference.lines[start]
            label = f"L{start + 1}-L{prev + 1}" if start != prev else f"L{start + 1}"
            if reference.timed:
                end = reference.lines[prev + 1].start if prev + 1 < len(reference.lines) else None
                label += f" [{_fmt_time(first.start)}-{_fmt_time(end)}]"
            regions.append(label)
            start = None
        if i is not None:
            if start is None:
                start = i
            prev = i
    return len(missing), regions


def score_song(song: dict, reference: Text, ref_name: str, timed: Text | None, hypothesis: Text) -> Score:
    lang = song.get("language")
    ref_norm = normalize(reference.plain, lang, strip_parenthetical=True)
    hyp_norm = normalize(hypothesis.plain, lang, strip_parenthetical=True)
    score = Score(id=song["id"], group=song.get("group", ""), source=hypothesis.source or "?", reference=ref_name)
    score.cer = round(cer(ref_norm, hyp_norm), 3)
    score.wer = round(wer(ref_norm, hyp_norm), 3)
    score.cer_skel = round(cer(skeleton(ref_norm), skeleton(hyp_norm)), 3)
    region_ref = timed or reference
    score.ref_lines = len(region_ref.lines)
    score.missing_lines, score.missing_regions = missing_regions(region_ref, hypothesis, lang)
    return score


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def render_table(scores: list[Score]) -> str:
    out = ["| song | group | source | ref | CER | WER | CER-skel | missing lines | missing regions |",
           "|---|---|---|---|---|---|---|---|---|"]
    for s in scores:
        if s.cer is None:
            out.append(f"| {s.id} | {s.group} | {s.source} | {s.reference or '-'} | - | - | - | - | {s.note} |")
            continue
        out.append(
            f"| {s.id} | {s.group} | {s.source} | {s.reference} | {s.cer:.3f} | {s.wer:.3f} | "
            f"{s.cer_skel:.3f} | {s.missing_lines}/{s.ref_lines} | {', '.join(s.missing_regions) or '-'} |"
        )
    out.append("")
    out.append("| group | n | median CER | median WER | median CER-skel |")
    out.append("|---|---|---|---|---|")
    for group in sorted({s.group for s in scores if s.cer is not None}):
        rows = [s for s in scores if s.group == group and s.cer is not None]
        out.append(
            f"| {group} | {len(rows)} | {median([s.cer for s in rows])} | "
            f"{median([s.wer for s in rows])} | {median([s.cer_skel for s in rows])} |"
        )
    return "\n".join(out)


def run_score(songs: list[dict], refs_dir: Path, hypotheses: dict[str, Text], prefer: str) -> list[Score]:
    scores: list[Score] = []
    for song in songs:
        hyp = hypotheses.get(song["id"])
        if hyp is None:
            continue
        reference, ref_name, timed = load_reference(refs_dir, song, prefer)
        if reference is None:
            scores.append(Score(id=song["id"], group=song.get("group", ""), source=hyp.source or "?",
                                reference="", note="no reference — run fetch-refs"))
            continue
        scores.append(score_song(song, reference, ref_name, timed, hyp))
    return scores


# -- CLI ---------------------------------------------------------------------

def _load_hypotheses(args, songs: list[dict]) -> dict[str, Text]:
    known = {s["id"] for s in songs}
    hyps: dict[str, Text] = {}
    if args.song:
        if args.song not in known:
            raise SystemExit(f"unknown song id {args.song!r}")
        if args.share:
            hyps[args.song] = load_share(args.share)
        elif args.file:
            hyps[args.song] = load_text_file(Path(args.file))
        else:
            raise SystemExit("--song needs --share or --file")
        return hyps
    jobs_path = Path(args.jobs)
    if not jobs_path.exists():
        raise SystemExit(f"{jobs_path} missing — pass --song/--share or create the jobs map")
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    for song_id, spec in jobs.items():
        if song_id not in known:
            print(f"warning: {song_id} not in songs.json, skipped", file=sys.stderr)
            continue
        share = spec["share"] if isinstance(spec, dict) else spec
        if not share:
            continue
        try:
            hyps[song_id] = load_share(share) if share.startswith("http") else load_text_file(Path(share))
        except Exception as exc:  # noqa: BLE001 — one bad job must not kill the run
            print(f"warning: {song_id}: {exc}", file=sys.stderr)
    return hyps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--songs", default=str(DEFAULT_SONGS))
    parser.add_argument("--refs", default=str(DEFAULT_REFS))
    sub = parser.add_subparsers(dest="cmd", required=True)

    fetch = sub.add_parser("fetch-refs", help="download reference texts into the git-ignored refs dir")
    fetch.add_argument("--force", action="store_true", help="re-download existing references")
    fetch.add_argument("--only", nargs="*", default=[], help="song ids to restrict to")

    score = sub.add_parser("score", help="score hypotheses against references")
    score.add_argument("--jobs", default=str(DEFAULT_JOBS), help="song id -> share URL / file map")
    score.add_argument("--song", help="single song id (with --share or --file)")
    score.add_argument("--share", help="share URL of a live job (…/share/<token>)")
    score.add_argument("--file", help="local .lrc / lyrics.json / .txt hypothesis")
    score.add_argument("--ref", choices=["auto", "genius", "lrclib"], default="auto")
    score.add_argument("--label", default="run", help="name for the JSON written under bench/out/")
    score.add_argument("--no-save", action="store_true")

    args = parser.parse_args(argv)
    songs = load_songs(Path(args.songs))
    refs_dir = Path(args.refs)

    if args.cmd == "fetch-refs":
        rows = fetch_refs(songs, refs_dir, force=args.force, only=set(args.only),
                          lrclib=LyricsSource(), genius=GeniusSource())
        print("| song | lrclib | genius |\n|---|---|---|")
        for row in rows:
            print(f"| {row['id']} | {row['lrclib']} | {row['genius']} |")
        return 0

    hyps = _load_hypotheses(args, songs)
    scores = run_score(songs, refs_dir, hyps, args.ref)
    print(render_table(scores))
    if not args.no_save:
        DEFAULT_OUT.mkdir(parents=True, exist_ok=True)
        out = DEFAULT_OUT / f"{time.strftime('%Y%m%d-%H%M%S')}-{args.label}.json"
        out.write_text(json.dumps([s.__dict__ for s in scores], ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\nsaved {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
