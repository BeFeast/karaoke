"""YouTube Music canonical metadata (#281) — scripted search, no network."""
from __future__ import annotations

from karaoke.worker.canonical import CanonicalResolver


def _song(title, artists, seconds, video_id="vid"):
    return {
        "title": title,
        "videoId": video_id,
        "duration_seconds": seconds,
        "artists": [{"name": a} for a in artists],
        "resultType": "song",
    }


class _Search:
    def __init__(self, results):
        self.results = results
        self.queries = []

    def __call__(self, query):
        self.queries.append(query)
        return self.results


def test_resolves_latin_artist_and_release_duration_for_hebrew_upload():
    search = _Search([
        _song("לאב סונג", ["Noa Kirel"], 179, "7EvxHCSv7eA"),
        _song("לאב סונג", ["The Voice Israel", "נועה קירל"], 176),
    ])
    meta = CanonicalResolver(search=search).resolve("נועה קירל", "לאב סונג", 227)
    assert meta is not None
    assert (meta.artist, meta.duration, meta.video_id) == ("Noa Kirel", 179, "7EvxHCSv7eA")
    assert search.queries == ["נועה קירל לאב סונג"]


def test_accepts_latin_release_title_on_artist_or_duration_evidence():
    search = _Search([_song("Tudo bom", ["Static", "Ben El"], 190)])
    meta = CanonicalResolver(search=search).resolve("Static and Ben El", "טודו בום", 200)
    assert meta is not None and meta.title == "Tudo bom" and meta.artists == ("Static", "Ben El")


def test_rejects_when_nothing_corroborates():
    search = _Search([_song("Other", ["Nobody"], 400)])
    assert CanonicalResolver(search=search).resolve("נועה קירל", "לאב סונג", 227) is None


def test_rejects_far_duration_even_with_title_match():
    search = _Search([_song("לאב סונג", ["Noa Kirel"], 500)])
    assert CanonicalResolver(search=search).resolve("נועה קירל", "לאב סונג", 227) is None


def test_prefers_title_match_over_earlier_duration_only_hit():
    search = _Search([
        _song("Unrelated", ["Noa Kirel"], 227),
        _song("לאב סונג", ["Noa Kirel"], 179),
    ])
    meta = CanonicalResolver(search=search).resolve("נועה קירל", "לאב סונג", 227)
    assert meta is not None and meta.title == "לאב סונג"


def test_tolerates_malformed_items_and_empty_results():
    assert CanonicalResolver(search=_Search([{"title": None}, "junk", {}])).resolve("a", "b", 1) is None
    assert CanonicalResolver(search=_Search([])).resolve("a", "b", 1) is None
    assert CanonicalResolver(search=_Search([])).resolve("a", "", 1) is None


def test_caches_hits_only():
    search = _Search([])
    resolver = CanonicalResolver(search=search)
    assert resolver.resolve("a", "b", 1) is None
    assert resolver.resolve("a", "b", 1) is None
    assert len(search.queries) == 2
    search.results = [_song("b", ["a"], 1)]
    assert resolver.resolve("A", "B", 1) is not None
    assert resolver.resolve("a", "b", 1) is not None
    assert len(search.queries) == 3
