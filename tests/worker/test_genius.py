"""Genius second-source client (#281) — scripted HTTP, placeholder text only."""
from __future__ import annotations

import json

import pytest

from karaoke.worker.genius import GeniusSource, clean_text, extract_lyrics_html

PAGE = """
<html><body>
<div class="header">Not lyrics</div>
<div data-lyrics-container="true" class="Lyrics__Container">
  <div data-exclude-from-selection="true"><span>12 Contributors</span><div>Translation note</div></div>
  [Verse 1]<br/>first line<br>second &amp; line<br/>
  <a href="/x"><span>third line</span></a><br/>
</div>
<div data-lyrics-container="true">
  [Chorus]<br/>fourth line<br/><br/>fifth line
</div>
<div>footer</div>
</body></html>
"""


def test_extract_skips_excluded_and_keeps_line_breaks():
    raw = extract_lyrics_html(PAGE)
    assert "Contributors" not in raw
    assert "Translation note" not in raw
    assert "Not lyrics" not in raw and "footer" not in raw
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    assert lines == [
        "[Verse 1]", "first line", "second & line", "third line",
        "[Chorus]", "fourth line", "fifth line",
    ]


def test_clean_text_drops_headers_and_rtl_comma_artefacts():
    raw = "[פתיח]\nמילה ,מילה\n\n\n[בית א]\nעוד‏ שורה  כאן\n\n"
    assert clean_text(raw) == "מילה, מילה\n\nעוד שורה כאן"


def _search_body(*hits):
    return json.dumps({
        "response": {
            "sections": [
                {"type": "top_hit", "hits": [
                    {"type": "artist", "result": {"url": "https://genius.com/artists/x"}},
                ]},
                {"type": "song", "hits": [
                    {"type": "song", "result": {"url": u, "title": t, "artist_names": a}}
                    for u, t, a in hits
                ]},
            ]
        }
    })


class _Http:
    def __init__(self, script):
        self.script = script
        self.calls = []

    def __call__(self, method, url, params):
        self.calls.append((method, url, params))
        if not self.script:
            raise AssertionError(f"unscripted call {url}")
        return self.script.pop(0)


def test_search_ranks_by_title_overlap_and_ignores_artist_hits():
    http = _Http([(200, _search_body(
        ("https://genius.com/other", "Other Thing", "Someone"),
        ("https://genius.com/hit", "Test Song - שיר בדיקה", "Tester - בודק"),
    ))])
    hit = GeniusSource(http=http).search("בודק", "שיר בדיקה", lang="he")
    assert hit is not None and hit.url == "https://genius.com/hit"
    assert http.calls[0][2] == {"q": "בודק שיר בדיקה"}


def test_search_returns_none_without_title_overlap():
    http = _Http([(200, _search_body(("https://genius.com/x", "Unrelated", "Nobody")))])
    assert GeniusSource(http=http).search("a", "שיר בדיקה") is None


@pytest.mark.parametrize("status", [0, 403, 500])
def test_search_tolerates_http_failures(status):
    assert GeniusSource(http=_Http([(status, "")])).search("a", "b") is None


def test_fetch_caches_success_only():
    http = _Http([
        (503, ""),
        (200, _search_body(("https://genius.com/hit", "Test Song", "Tester"))),
        (200, PAGE),
    ])
    src = GeniusSource(http=http)
    assert src.fetch("Tester", "Test Song") is None  # search failed → not cached
    result = src.fetch("Tester", "Test Song")
    assert result is not None
    assert result.text.splitlines()[0] == "first line"
    assert result.url == "https://genius.com/hit"
    assert src.fetch("tester", "test song") is result  # cached, no HTTP
    assert len(http.calls) == 3
