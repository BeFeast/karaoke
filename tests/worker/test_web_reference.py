"""Web reference cascade (#283) — scripted transports, placeholder text only."""
from __future__ import annotations

import json

from karaoke.worker.genius import GeniusSource
from karaoke.worker.web_reference import WebReferenceFinder, extract_tab4u

TAB4U_PAGE = """
<html><body><div id="other">menu</div>
<div id="songContentTPL" align="right">
 <table><tbody><tr><td class="song_only"> פתיחה: </td></tr></tbody></table><br />
 <table><tbody>
  <tr><td class="chords">Am</td></tr>
  <tr><td class="song_only"> שורה&nbsp;ראשונה&nbsp;כאן </td></tr>
  <tr><td class="song_only"> שורה&nbsp;שנייה&nbsp;כאן </td></tr>
 </tbody></table><br />
 <table><tbody>
  <tr><td class="song_only"> שורה שלישית כאן </td></tr>
  <tr><td class="song_only"> שורה רביעית כאן </td></tr>
 </tbody></table>
</div><div>footer text</div></body></html>
"""


def test_extract_tab4u_lines_and_stanzas():
    assert extract_tab4u(TAB4U_PAGE) == "שורה ראשונה כאן\nשורה שנייה כאן\n\nשורה שלישית כאן\nשורה רביעית כאן"


class _Http:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, params):
        self.calls.append((url, params))
        for prefix, response in self.routes:
            if url.startswith(prefix):
                return response
        return 404, ""


def test_ytmusic_first_when_the_canonical_release_has_lyrics():
    ytm = lambda vid: ("א\nב\nג\nד", "[00:01.00]א", "MPLY") if vid == "V1" else None  # noqa: E731
    finder = WebReferenceFinder(ytm_lyrics=ytm, http=_Http([]), searxng_url="http://sx")
    ref = finder.find(artist="x", track="y", video_id="V1", lang="he")
    assert ref.provider == "ytmusic" and ref.url == "ytmusic:MPLY" and ref.synced_lrc


def test_falls_back_to_genius_then_tab4u_via_searxng():
    search = json.dumps({"results": [
        {"url": "https://example.com/x", "title": "שיר בדיקה"},
        {"url": "https://www.tab4u.com/tabs/songs/1_x_-_y.html", "title": "בודק - שיר בדיקה"},
    ]})
    http = _Http([
        ("https://genius.com/api/search/multi", (200, json.dumps({"response": {"sections": []}}))),
        ("http://sx/search", (200, search)),
        ("https://www.tab4u.com/lyrics/songs/1_x_-_y.html", (200, TAB4U_PAGE)),
    ])
    finder = WebReferenceFinder(ytm_lyrics=lambda v: None, genius=GeniusSource(http=http), http=http,
                                searxng_url="http://sx")
    ref = finder.find(artist="בודק", track="שיר בדיקה", video_id="V1", lang="he")
    assert ref.provider == "tab4u" and ref.url == "https://www.tab4u.com/lyrics/songs/1_x_-_y.html"
    assert ref.text.splitlines()[0] == "שורה ראשונה כאן"
    assert any(p == {"q": "בודק שיר בדיקה site:tab4u.com", "format": "json"} for _, p in http.calls)


def test_find_by_text_requires_the_line_on_the_page():
    search = json.dumps({"results": [{"url": "https://www.tab4u.com/lyrics/songs/2_a.html", "title": "t"}]})
    http = _Http([("http://sx/search", (200, search)),
                  ("https://www.tab4u.com/lyrics/songs/2_a.html", (200, TAB4U_PAGE))])
    finder = WebReferenceFinder(ytm_lyrics=lambda v: None, http=http, searxng_url="http://sx")
    assert finder.find_by_text("שורה שנייה כאן היום", lang="he") is None  # not on the page
    ref = finder.find_by_text("שורה שלישית כאן שורה רביעית", lang="he")
    assert ref is not None and ref.provider == "tab4u"
    assert '"שורה שלישית כאן שורה רביעית" מילים' in [p["q"] for _, p in http.calls if p]


def test_no_searxng_and_nothing_found_is_a_quiet_miss():
    http = _Http([])
    finder = WebReferenceFinder(ytm_lyrics=lambda v: None, genius=GeniusSource(http=http), http=http)
    assert finder.find(artist="a", track="b", video_id=None) is None
    assert finder.find_by_text("a b c d e") is None


def test_identify_by_text_reads_titles_off_result_urls():
    search = json.dumps({"results": [
        {"url": "https://www.tab4u.com/lyrics/songs/1463_%D7%96%D7%95%D7%94%D7%A8_%D7%90%D7%A8%D7%92%D7%95%D7%91_-_%D7%94%D7%A4%D7%A8%D7%97_%D7%91%D7%92%D7%A0%D7%99.html", "title": "t"},
        {"url": "https://www.musixmatch.com/lyrics/Eden-Ben-Zaken/%D7%9B%D7%95%D7%9C%D7%9D-%D7%91%D7%90%D7%99%D7%9C%D7%AA", "title": "t"},
        {"url": "https://genius.com/Hadag-nahash-shirat-hasticker-lyrics", "title": "Hadag Nahash – Shirat HaSticker Lyrics | Genius Lyrics"},
        {"url": "https://example.com/other", "title": "nothing"},
    ]})
    http = _Http([("http://sx/search", (200, search))])
    finder = WebReferenceFinder(ytm_lyrics=lambda v: None, http=http, searxng_url="http://sx")
    assert finder.identify_by_text("פול ווליום מזרח עם ידיים", lang="he") == [
        ("זוהר ארגוב", "הפרח בגני"), ("Eden Ben Zaken", "כולם באילת"), ("Hadag Nahash", "Shirat HaSticker"),
    ]
    assert finder.identify_by_text("too short", lang="he") == []


def test_tab4u_structure_lines_are_not_lyrics():
    page = ('<div id="songContentTPL"><table>'
            '<tr><td class="song_only">A47: x02030</td></tr><tr><td class="song_only">x5</td></tr>'
            '<tr><td class="song_only">המשך פתיחה:</td></tr><tr><td class="song_only">שורה אמיתית של שיר</td></tr>'
            '</table></div>')
    assert extract_tab4u(page) == "שורה אמיתית של שיר"
