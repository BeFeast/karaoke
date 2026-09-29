"""LLM correction of the ASR floor (#279, decision 10). Placeholder text only."""
import json

import httpx
import pytest

from karaoke.worker.llm_corrector import (
    LlmCorrector,
    correct_transcript,
    edits_to_corrections,
    parse_edits,
)

LRC = (
    "[00:01.00]<00:01.00>כל <00:01.40>יום <00:01.80>בתשה <00:02.20>אתה\n"
    "[00:04.00][speech] זה סבבה לך המקום\n"
    "[00:06.00]מפחיד להתהב אוהב\n"
)


def _fixed(edits):
    return lambda lines, song: edits


def test_sung_lines_are_numbered_without_speech_and_edits_keep_tags():
    seen = {}

    def propose(lines, song):
        seen["lines"], seen["song"] = lines, song
        return [
            {"line": 1, "from": "בתשה", "to": "בתשע"},
            {"line": 2, "from": "להתהב", "to": "להתאהב"},
        ]

    result = correct_transcript(LRC, "Artist — Song", "he", propose)
    assert seen == {"lines": ["כל יום בתשה אתה", "מפחיד להתהב אוהב"], "song": "Artist — Song"}
    assert result.proposed == 2 and len(result.applied) == 2
    out = result.text.splitlines()
    assert out[0] == "[00:01.00]<00:01.00>כל <00:01.40>יום <00:01.80>בתשע <00:02.20>אתה"
    assert out[1] == "[00:04.00][speech] זה סבבה לך המקום"
    assert out[2] == "[00:06.00]מפחיד להתאהב אוהב"


def test_edits_that_do_not_match_their_line_are_dropped():
    edits = [
        {"line": 1, "from": "להתהב", "to": "להתאהב"},  # wrong line
        {"line": 9, "from": "כל", "to": "כול"},  # no such line
        {"line": 2, "from": "מפחיד", "to": ""},  # deletion
        {"line": 2, "from": "מפחיד להתהב אוהב", "to": "מילה אחת שתיים שלוש ארבע"},  # too long
        {"line": 2, "from": "אוהב", "to": "<00:09.00>אוהב"},  # tag injection
        {"line": "x", "from": "כל", "to": "כול"},
    ]
    result = correct_transcript(LRC, None, "he", _fixed(edits))
    assert result.applied == [] and result.text == LRC


def test_punctuation_and_marks_do_not_block_a_match():
    text = "[00:01.00]שלום, עולם!\n"
    result = correct_transcript(text, None, "he", _fixed([{"line": 1, "from": "עולם", "to": "עולמי"}]))
    # the replaced word takes the model's surface, trailing punctuation included
    assert result.text == "[00:01.00]שלום, עולמי\n"


def test_a_rewrite_of_most_words_is_discarded():
    text = "[00:01.00]אחת שתיים שלוש\n[00:03.00]ארבע חמש שש\n"
    edits = [{"line": 1, "from": "אחת שתיים", "to": "א ב"}, {"line": 2, "from": "ארבע חמש", "to": "ג ד"}]
    corrections, applied = edits_to_corrections(
        text, [(0, "אחת שתיים שלוש"), (1, "ארבע חמש שש")], edits, "he"
    )
    assert corrections == [] and applied == []


def test_two_edits_on_the_same_words_keep_the_first():
    text = "[00:01.00]כל יום בתשה\n"
    edits = [{"line": 1, "from": "בתשה", "to": "בתשע"}, {"line": 1, "from": "יום בתשה", "to": "יום בשעה"}]
    result = correct_transcript(text, None, "he", _fixed(edits))
    assert result.text == "[00:01.00]כל יום בתשע\n" and len(result.applied) == 1


def test_plain_text_is_corrected_too():
    result = correct_transcript("כל יום בתשה\n", None, "he", _fixed([{"line": 1, "from": "בתשה", "to": "בתשע"}]))
    assert result.text == "כל יום בתשע\n"


def test_parse_edits_reads_json_inside_prose():
    assert parse_edits('Sure:\n```json\n{"edits": [{"line": 1, "from": "a", "to": "b"}]}\n```') == [
        {"line": 1, "from": "a", "to": "b"}
    ]
    with pytest.raises(ValueError):
        parse_edits("no edits today")


def test_client_sends_numbered_lines_and_song():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers["authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"edits": []}'}}]})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = LlmCorrector("http://gw/v1/", "k", "gpt-6.1-sol", http=http)
    assert client.propose(["שורה אחת", "שורה שתיים"], "A — B") == []
    assert captured["auth"] == "Bearer k"
    assert captured["body"]["model"] == "gpt-6.1-sol" and captured["body"]["temperature"] == 0
    user = captured["body"]["messages"][1]["content"]
    assert user == "Song: A — B\n\nTranscript:\n1: שורה אחת\n2: שורה שתיים"


def test_client_raises_on_gateway_errors():
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, json={"error": "cooling"})))
    with pytest.raises(httpx.HTTPStatusError):
        LlmCorrector("http://gw/v1", "k", "m", http=http).propose(["x"], None)
