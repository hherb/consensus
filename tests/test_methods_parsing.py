"""Tests for shared method parsing utilities."""

import pytest
from consensus.methods.parsing import (
    extract_json_block,
    parse_numbered_list,
    word_overlap_similar,
)


class TestExtractJsonBlock:
    def test_extracts_fenced_json(self):
        content = 'Some text\n```json\n{"key": "value"}\n```\nMore text'
        result = extract_json_block(content)
        assert result == {"key": "value"}

    def test_extracts_unfenced_json(self):
        content = 'Some text\n```\n{"key": "value"}\n```\nMore text'
        result = extract_json_block(content)
        assert result == {"key": "value"}

    def test_returns_none_for_no_json(self):
        assert extract_json_block("no json here") is None

    def test_returns_none_for_invalid_json(self):
        content = '```json\n{invalid json}\n```'
        assert extract_json_block(content) is None

    def test_extracts_nested_json(self):
        content = '```json\n{"beliefs": {"h1": 0.6, "h2": 0.4}}\n```'
        result = extract_json_block(content)
        assert result["beliefs"]["h1"] == 0.6

    def test_extracts_json_array(self):
        content = '```json\n[{"a": 1}, {"b": 2}]\n```'
        result = extract_json_block(content)
        assert isinstance(result, list)
        assert len(result) == 2


class TestParseNumberedList:
    def test_parses_dot_numbered(self):
        content = "1. First assumption here\n2. Second assumption here"
        result = parse_numbered_list(content)
        assert len(result) == 2
        assert "First assumption here" in result[0]

    def test_parses_paren_numbered(self):
        content = "1) First assumption here\n2) Second assumption here"
        result = parse_numbered_list(content)
        assert len(result) == 2

    def test_parses_prefixed(self):
        content = "A1: First assumption here\nA2: Second assumption here"
        result = parse_numbered_list(content)
        assert len(result) == 2

    def test_parses_hypothesis_prefixed(self):
        content = "H1: First hypothesis here\nH2: Second hypothesis here"
        result = parse_numbered_list(content)
        assert len(result) == 2

    def test_parses_bullet_list(self):
        content = "- First assumption here\n- Second assumption here"
        result = parse_numbered_list(content)
        assert len(result) == 2

    def test_filters_short_items(self):
        content = "1. Short\n2. This is a sufficiently long item"
        result = parse_numbered_list(content, min_length=10)
        assert len(result) == 1

    def test_returns_empty_for_no_list(self):
        assert parse_numbered_list("Just a paragraph of text.") == []

    def test_strips_trailing_period(self):
        content = "1. An assumption with period."
        result = parse_numbered_list(content)
        assert not result[0].endswith(".")


class TestWordOverlapSimilar:
    def test_identical_strings(self):
        assert word_overlap_similar("hello world", "hello world")

    def test_similar_strings(self):
        assert word_overlap_similar(
            "the economy will grow steadily",
            "the economy will continue to grow steadily",
        )

    def test_dissimilar_strings(self):
        assert not word_overlap_similar(
            "the sun is hot",
            "fish swim in water",
        )

    def test_custom_threshold(self):
        assert word_overlap_similar("a b c d", "a b c e", threshold=0.5)
        assert not word_overlap_similar("a b c d", "a b c e", threshold=0.9)

    def test_empty_strings(self):
        assert not word_overlap_similar("", "hello")
        assert not word_overlap_similar("hello", "")
