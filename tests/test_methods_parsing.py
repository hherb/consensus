"""Tests for shared method parsing utilities."""

import itertools

import pytest
from consensus.methods.parsing import (
    canonical_index,
    cluster_by_similarity,
    cluster_text_contributions,
    extract_json_block,
    parse_numbered_list,
    word_overlap_ratio,
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


class TestWordOverlapRatio:
    def test_identical_is_one(self):
        assert word_overlap_ratio("alpha beta", "alpha beta") == 1.0

    def test_disjoint_is_zero(self):
        assert word_overlap_ratio("alpha beta", "gamma delta") == 0.0

    def test_empty_is_zero(self):
        assert word_overlap_ratio("", "alpha") == 0.0

    def test_partial_overlap(self):
        # {a, b, c} vs {a, b} -> 2 / max(3, 2) = 2/3
        assert word_overlap_ratio("a b c", "a b") == pytest.approx(2 / 3)

    def test_similar_still_thresholds_on_ratio(self):
        assert word_overlap_similar("a b c d e", "a b c d f")  # 4/5 > 0.7
        assert not word_overlap_similar("a b c d e", "a b c x y")  # 3/5


class TestClusterBySimilarity:
    def test_all_distinct_are_singletons(self):
        items = ["alpha beta gamma", "delta epsilon zeta", "eta theta iota"]
        clusters = cluster_by_similarity(items, text_of=lambda s: s)
        assert [len(c) for c in clusters] == [1, 1, 1]

    def test_merges_similar_pair(self):
        items = ["build a plugin marketplace for third parties",
                 "build a plugin marketplace for the third parties"]
        clusters = cluster_by_similarity(items, text_of=lambda s: s)
        assert len(clusters) == 1 and len(clusters[0]) == 2

    def test_transitive_chain_is_one_cluster(self):
        a = "one two three four five"
        b = "one two three four nine"   # shares 4/5 with a
        c = "one two three eight nine"  # shares 4/5 with b, 3/5 with a
        assert word_overlap_similar(a, b)
        assert word_overlap_similar(b, c)
        assert not word_overlap_similar(a, c)
        clusters = cluster_by_similarity([a, b, c], text_of=lambda s: s)
        assert len(clusters) == 1

    def test_grouping_is_order_independent(self):
        items = ["alpha beta gamma delta", "alpha beta gamma epsilon",
                 "totally different words here"]
        seen = []
        for perm in itertools.permutations(items):
            clusters = cluster_by_similarity(list(perm), text_of=lambda s: s)
            seen.append({frozenset(c) for c in clusters})
        assert all(s == seen[0] for s in seen)

    def test_clusters_ordered_by_min_index(self):
        items = ["zzz distinct singleton", "aaa shared cluster text",
                 "aaa shared cluster text too"]
        clusters = cluster_by_similarity(items, text_of=lambda s: s)
        assert clusters[0] == ["zzz distinct singleton"]

    def test_empty_members_returns_empty_list(self):
        assert cluster_by_similarity([], text_of=lambda s: s) == []


class TestCanonicalIndex:
    def test_single_member(self):
        assert canonical_index(["only one here"], text_of=lambda s: s) == 0

    def test_empty_members_raises(self):
        with pytest.raises(ValueError):
            canonical_index([], text_of=lambda s: s)

    def test_medoid_is_most_central(self):
        members = ["cost total ownership money", "cost total ownership",
                   "cost total spend"]
        assert canonical_index(members, text_of=lambda s: s) == 1

    def test_tie_breaks_to_longest(self):
        members = ["total cost of ownership", "total cost of ownership now"]
        assert canonical_index(members, text_of=lambda s: s) == 1

    def test_tie_breaks_lexicographically_when_same_length(self):
        members = ["bbbb cost", "aaaa cost"]
        assert canonical_index(members, text_of=lambda s: s) == 1

    def test_medoid_text_is_permutation_independent(self):
        # Centrality is summed in exact rational arithmetic, so the
        # chosen text must not vary with member order — including on
        # genuine centrality ties, which float summation could break
        # differently per order.
        members = ["cost total ownership money", "cost total ownership",
                   "cost total spend", "money spend budget quarterly"]
        picks = {perm[canonical_index(list(perm), text_of=lambda s: s)]
                 for perm in itertools.permutations(members)}
        assert len(picks) == 1


class TestClusterTextContributions:
    def test_view_labels_and_touched(self):
        raw = [
            {"entity_id": 1, "entity_name": "Alice",
             "text": "shared idea alpha beta"},
            {"entity_id": 2, "entity_name": "Bob",
             "text": "distinct thing entirely here"},
            {"entity_id": 2, "entity_name": "Bob",
             "text": "shared idea alpha beta too"},
        ]
        view, touched = cluster_text_contributions(raw, since=1)
        assert [v["id"] for v in view] == [1, 2]
        assert view[0]["entity_name"] == "Alice"      # founder = min index
        assert view[0]["text"] == "shared idea alpha beta too"  # medoid
        assert len(touched) == 2                       # both clusters have idx>=1

    def test_empty_raw_returns_empty_view_and_touched(self):
        assert cluster_text_contributions([]) == ([], [])
