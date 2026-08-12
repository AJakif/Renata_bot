"""Unit tests for parse_generation_result (fast, no network/embedding calls)."""

from app.generate import parse_generation_result


def test_parse_valid_result() -> None:
    """Valid JSON with all required fields parses to a GenerationResult."""
    raw = '{"answered": true, "answer": "Take with food.", "chunk_ids": [1, 3]}'
    result = parse_generation_result(raw)
    assert result is not None
    assert result.answered is True
    assert result.answer == "Take with food."
    assert result.chunk_ids == [1, 3]


def test_parse_invalid_json_returns_none() -> None:
    """Malformed JSON returns None — never raises."""
    assert parse_generation_result("not valid json {{{") is None


def test_parse_missing_required_field_returns_none() -> None:
    """JSON that omits a required field (here: 'answered') returns None."""
    assert parse_generation_result('{"answer": "hello", "chunk_ids": []}') is None
