import pytest

from src.workers.stages._json import LLMResponseParseError, parse_json_response


def test_parses_plain_json():
    assert parse_json_response('{"a": 1}') == {"a": 1}


def test_strips_markdown_code_fence():
    raw = '```json\n{"a": 1}\n```'
    assert parse_json_response(raw) == {"a": 1}


def test_strips_bare_code_fence():
    raw = "```\n[1, 2, 3]\n```"
    assert parse_json_response(raw) == [1, 2, 3]


def test_raises_on_invalid_json():
    with pytest.raises(LLMResponseParseError):
        parse_json_response("not json at all")
