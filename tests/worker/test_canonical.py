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


def test_live_and_remix_variants_are_skipped_unless_the_upload_says_so():
    search = _Search([
        _song("כשהלב בוכה (מנורה LIVE)", ["Sarit Hadad"], 328),
        _song("כשהלב בוכה", ["Sarit Hadad"], 286),
    ])
    meta = CanonicalResolver(search=search).resolve("שרית חדד", "כשהלב בוכה", 285)
    assert meta is not None and meta.duration == 286
    live = CanonicalResolver(search=_Search([_song("ממעמקים - Live", ["Idan Raichel"], 312)]))
    assert live.resolve("Idan Raichel", "ממעמקים", 288) is None
    assert live.resolve("Idan Raichel", "ממעמקים (Live)", 300) is not None


def test_equal_title_overlap_prefers_the_closest_duration():
    search = _Search([
        _song("לאב סונג", ["Noa Kirel"], 240),
        _song("לאב סונג", ["Noa Kirel"], 179),
    ])
    meta = CanonicalResolver(search=search).resolve("נועה קירל", "לאב סונג", 227)
    assert meta is not None and meta.duration == 240  # 13 s off beats 48 s off


def test_bilingual_artist_name_is_split_latin_first():
    search = _Search([_song("הפרח בגני", ["Zohar Argov-זוהר ארגוב"], 221)])
    meta = CanonicalResolver(search=search).resolve(None, "זוהר ארגוב הפרח בגני", 222)
    assert meta is not None
    assert meta.artist == "Zohar Argov"
    assert meta.artists == ("Zohar Argov", "זוהר ארגוב")
    hebrew_only = CanonicalResolver(search=_Search([_song("שירת הסטיקר", ["הדג נחש"], 261)]))
    assert hebrew_only.resolve("Hadag Nahash", "שירת הסטיקר", 253).artist == "הדג נחש"
    assert CanonicalResolver(search=_Search([_song("Song", ["Jay-Z"], 200)])).resolve("Jay-Z", "Song", 200).artist == "Jay-Z"
