"""Regression tests for assorted method-level defects (GitHub issue #17).

Covers: Delphi panelist-label consistency and anonymisation robustness,
zero-median convergence, triage method-name word-boundary matching,
no-motion vote phase skipping, the ACH inline-ratings fallback parser,
and sub-question analysis attribution by header number.  Also the
``ProcessedResponse`` contract slimming (issue #21).
"""

import dataclasses

import pytest

from consensus.methods.base import ProcessedResponse
from consensus.methods.phases._decomposition_helpers import (
    extract_subquestion_analyses,
)
from consensus.methods.phases._delphi_helpers import (
    anonymise_content,
    build_distribution_summary,
    build_panelist_map,
    check_convergence,
)
from consensus.methods.phases.evaluate_matrix import EvaluateMatrixHandler
from consensus.methods.phases.triage_confirm import TriageConfirmHandler
from consensus.methods.phases.vote import VoteHandler
from consensus.models import Discussion, Entity, EntityType


def _entity(eid: int, name: str) -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.HUMAN)


@pytest.fixture
def delphi_discussion():
    mod = _entity(1, "Mod")
    alice = _entity(2, "Ann")
    bob = _entity(3, "Anna")
    return Discussion(
        topic="Forecast",
        entities=[mod, alice, bob],
        moderator_id=mod.id,
        turn_order=[alice.id, bob.id],
        method_state={},
    )


class TestDelphiPanelistLabels:
    """Distribution summary labels must match the anonymised transcript."""

    def test_summary_uses_stable_panelist_aliases(self, delphi_discussion):
        disc = delphi_discussion
        # Ann (entity order 1 -> "Panelist 1") gave the HIGH estimate;
        # Anna ("Panelist 2") gave the LOW one.  Sorted-by-value display
        # must keep their stable aliases, not renumber positionally.
        disc.method_state["estimates"] = [
            {"round": 1, "entity_id": 2, "entity_name": "Ann",
             "value": 0.9, "confidence": "HIGH", "unit": ""},
            {"round": 1, "entity_id": 3, "entity_name": "Anna",
             "value": 0.2, "confidence": "LOW", "unit": ""},
        ]
        panelist_map = build_panelist_map(disc)
        summary = build_distribution_summary(disc)

        ann_alias = panelist_map["Ann"]      # "Panelist 1"
        anna_alias = panelist_map["Anna"]    # "Panelist 2"
        assert f"{ann_alias}: 0.9" in summary
        assert f"{anna_alias}: 0.2" in summary


class TestAnonymiseContent:
    """Name replacement must respect word boundaries, longest name first."""

    def test_substring_names_do_not_corrupt(self, delphi_discussion):
        disc = delphi_discussion
        panelist_map = build_panelist_map(disc)
        result = anonymise_content("Anna said that Ann agrees.", disc)
        assert result == (
            f"{panelist_map['Anna']} said that {panelist_map['Ann']} agrees."
        )

    def test_name_inside_word_untouched(self, delphi_discussion):
        disc = delphi_discussion
        disc.entities.append(_entity(4, "Mark"))
        disc.method_state.pop("_panelist_map", None)
        result = anonymise_content("Use Markdown formatting.", disc)
        assert "Markdown" in result

    def test_name_with_non_word_edges_still_anonymised(self, delphi_discussion):
        """Names starting/ending in non-word chars must still be replaced.

        ``\\b`` fails at a non-word edge (e.g. a trailing ``)``), which
        would leak the panelist's real name into the anonymised text.
        """
        disc = delphi_discussion
        disc.entities.append(_entity(4, "Claude (Opus)"))
        disc.method_state.pop("_panelist_map", None)
        panelist_map = build_panelist_map(disc)
        result = anonymise_content("Claude (Opus) estimates 5 units.", disc)
        assert "Claude (Opus)" not in result
        assert f"{panelist_map['Claude (Opus)']} estimates" in result


class TestZeroMedianConvergence:
    """Estimates converging on zero must still be able to converge."""

    def test_all_zero_estimates_converge(self, delphi_discussion):
        disc = delphi_discussion
        disc.method_state["revise_round"] = 1
        disc.method_state["estimates"] = [
            {"round": 1, "entity_id": 2, "entity_name": "Ann", "value": 0.0},
            {"round": 1, "entity_id": 3, "entity_name": "Anna", "value": 0.0},
        ]
        assert check_convergence(disc) is True

    def test_spread_around_zero_does_not_converge(self, delphi_discussion):
        disc = delphi_discussion
        disc.method_state["revise_round"] = 1
        disc.method_state["estimates"] = [
            {"round": 1, "entity_id": 2, "entity_name": "Ann", "value": -5.0},
            {"round": 1, "entity_id": 3, "entity_name": "Anna", "value": 0.0},
            {"round": 1, "entity_id": 1, "entity_name": "Mod", "value": 5.0},
        ]
        assert check_convergence(disc) is False


class TestTriageMethodNameMatching:
    """'ach' must not match inside 'approach' when parsing the choice."""

    def test_substring_does_not_select_wrong_method(self):
        handler = TriageConfirmHandler()
        mod = _entity(1, "Mod")
        disc = Discussion(
            topic="t", entities=[mod], moderator_id=mod.id,
            method_state={
                "recommendations": [
                    {"method_name": "ach", "display_name": "ACH",
                     "confidence": 0.8, "reasoning": "r"},
                    {"method_name": "delphi", "display_name": "Delphi",
                     "confidence": 0.7, "reasoning": "r"},
                ],
                "recommended_method": "delphi",
            },
        )
        handler.process_response(
            "The best approach for this question is the Delphi method.",
            mod, disc,
        )
        assert disc.method_state["chosen_method"] == "delphi"


class TestVoteNoMotions:
    """A deliberation with zero motions must not waste voting rounds."""

    def test_advances_immediately_without_motions(self):
        handler = VoteHandler()
        mod = _entity(1, "Mod")
        disc = Discussion(
            topic="t", entities=[mod], moderator_id=mod.id,
            turn_order=[2, 3],
            method_state={"motions": [], "votes": [], "phase_round": 1},
        )
        assert handler.should_advance(disc) is True


class TestAchInlineRatingsFallback:
    """The inline (unfenced) ratings fallback must parse nested JSON."""

    def test_parses_nested_inline_ratings(self):
        handler = EvaluateMatrixHandler()
        content = (
            'Here are my ratings: {"ratings": {"E1": {"H1": "+", '
            '"H2": "-"}, "E2": {"H1": "0", "H2": "+"}}} as requested.'
        )
        ratings = handler._parse_ratings(content)
        assert ratings == {
            "E1": {"H1": "+", "H2": "-"},
            "E2": {"H1": "0", "H2": "+"},
        }


class TestSubquestionAttribution:
    """Analyses must be attributed by header number, not position."""

    def test_skipped_subquestion_does_not_shift_attribution(self):
        content = (
            "**Sub-question 2:** Analysis of the second question only."
        )
        result = extract_subquestion_analyses(content, 3)
        assert "second question" in result[1]
        assert result[0] == ""
        assert result[2] == ""

    def test_reordered_subquestions_map_correctly(self):
        content = (
            "**Sub-question 2:** Second analysis here.\n\n"
            "**Sub-question 1:** First analysis here."
        )
        result = extract_subquestion_analyses(content, 2)
        assert "First analysis" in result[0]
        assert "Second analysis" in result[1]


class TestProcessedResponseContract:
    """ProcessedResponse must not offer a decoy data channel (issue #21).

    The flow layer only ever consumed ``display_content``; handlers own
    their state writes via ``discussion.method_state``.  An
    ``extracted_data`` field would invite new handlers to return data
    that is silently dropped.
    """

    def test_no_extracted_data_field(self):
        field_names = {f.name for f in dataclasses.fields(ProcessedResponse)}
        assert field_names == {"display_content"}
