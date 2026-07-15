"""Tests for consensus.methods.panel_diversity — same-model panel detection (#29)."""

import pytest

from consensus.methods.panel_diversity import (
    DIVERSITY_WARN_FRACTION,
    PanelDiversityReport,
    analyze_panel_diversity,
    format_setup_warning,
    format_conclusion_disclosure,
)


class TestAnalyzePanelDiversity:
    def test_unanimous_two(self):
        r = analyze_panel_diversity(["gpt-4o", "gpt-4o"])
        assert r.panel_size == 2
        assert r.dominant_model == "gpt-4o"
        assert r.dominant_count == 2
        assert r.distinct_models == 1
        assert r.is_concerning is True
        assert r.is_unanimous is True

    def test_majority_two_of_three(self):
        r = analyze_panel_diversity(["gpt-4o", "gpt-4o", "claude"])
        assert r.dominant_count == 2
        assert r.is_concerning is True   # 2 > 0.5 * 3
        assert r.is_unanimous is False

    def test_half_of_four_not_concerning(self):
        r = analyze_panel_diversity(["a", "a", "b", "b"])
        assert r.dominant_count == 2
        assert r.is_concerning is False  # 2 > 0.5 * 4 is False
        assert r.is_unanimous is False

    def test_three_of_four_concerning(self):
        r = analyze_panel_diversity(["a", "a", "a", "b"])
        assert r.dominant_count == 3
        assert r.is_concerning is True   # 3 > 0.5 * 4
        assert r.is_unanimous is False

    def test_all_distinct_not_concerning(self):
        r = analyze_panel_diversity(["a", "b", "c"])
        assert r.dominant_count == 1
        assert r.distinct_models == 3
        assert r.is_concerning is False

    def test_single_estimator(self):
        r = analyze_panel_diversity(["a"])
        assert r.panel_size == 1
        assert r.is_concerning is False
        assert r.is_unanimous is False

    def test_empty(self):
        r = analyze_panel_diversity([])
        assert r.panel_size == 0
        assert r.dominant_model == ""
        assert r.dominant_count == 0
        assert r.is_concerning is False
        assert r.model_counts == ()

    def test_model_counts_sorted_desc_then_name(self):
        r = analyze_panel_diversity(["b", "a", "a"])
        assert r.model_counts == (("a", 2), ("b", 1))
        assert r.dominant_model == "a"

    def test_report_is_frozen(self):
        r = analyze_panel_diversity(["a"])
        with pytest.raises(Exception):
            r.panel_size = 5  # frozen dataclass

    def test_fraction_constant(self):
        assert DIVERSITY_WARN_FRACTION == 0.5


from consensus.methods.panel_diversity import estimator_models
from consensus.models import (
    AIConfig, Discussion, Entity, EntityType,
)


def _ai(name, eid, model):
    return Entity(name=name, entity_type=EntityType.AI, id=eid,
                  ai_config=AIConfig(model=model))


class TestEstimatorModels:
    def test_excludes_moderator(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("Mod", 100, "gpt-4o"),
                      _ai("A", 1, "claude"),
                      _ai("B", 2, "claude")],
            moderator_id=100,
        )
        assert estimator_models(disc) == ["claude", "claude"]

    def test_excludes_humans(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"),
                      Entity(name="Human", entity_type=EntityType.HUMAN, id=2)],
            moderator_id=None,
        )
        assert estimator_models(disc) == ["gpt-4o"]

    def test_excludes_experts(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"),
                      Entity(name="Exp", entity_type=EntityType.EXPERT, id=2,
                             ai_config=AIConfig(model="gpt-4o"))],
            moderator_id=None,
        )
        assert estimator_models(disc) == ["gpt-4o"]

    def test_skips_ai_without_config(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"),
                      Entity(name="B", entity_type=EntityType.AI, id=2)],
            moderator_id=None,
        )
        assert estimator_models(disc) == ["gpt-4o"]

    def test_empty_when_no_ai(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[Entity(name="H", entity_type=EntityType.HUMAN, id=1)],
            moderator_id=None,
        )
        assert estimator_models(disc) == []


class TestFormatSetupWarning:
    def test_none_when_not_concerning(self):
        r = analyze_panel_diversity(["a", "b", "c"])
        assert format_setup_warning(r) is None

    def test_unanimous_wording(self):
        r = analyze_panel_diversity(["gpt-4o", "gpt-4o"])
        msg = format_setup_warning(r)
        assert msg is not None
        assert "All 2" in msg
        assert "gpt-4o" in msg
        assert "independent estimators" in msg

    def test_majority_wording(self):
        r = analyze_panel_diversity(["gpt-4o", "gpt-4o", "claude"])
        msg = format_setup_warning(r)
        assert msg is not None
        assert "2 of 3" in msg
        assert "gpt-4o" in msg


class TestFormatConclusionDisclosure:
    def test_empty_below_two(self):
        assert format_conclusion_disclosure(analyze_panel_diversity(["a"])) == ""
        assert format_conclusion_disclosure(analyze_panel_diversity([])) == ""

    def test_diverse_composition_no_caveat(self):
        r = analyze_panel_diversity(["a", "b", "c"])
        text = format_conclusion_disclosure(r)
        assert "Panel composition" in text
        assert "caveat" not in text.lower()

    def test_concerning_includes_caveat(self):
        r = analyze_panel_diversity(["gpt-4o", "gpt-4o", "claude"])
        text = format_conclusion_disclosure(r)
        assert "Panel composition" in text
        assert "2 of 3" in text
        assert "caveat" in text.lower()


from consensus.methods.base import DiscussionMethod


class _IndepMethod(DiscussionMethod):
    name = "indep_test"
    display_name = "Independent Test"
    assumes_independent_panel = True
    phase_handlers = ()


class _PlainMethod(DiscussionMethod):
    name = "plain_test"
    display_name = "Plain Test"
    phase_handlers = ()


class TestPanelCompositionDisclosure:
    def test_default_flag_false(self):
        assert DiscussionMethod.assumes_independent_panel is False
        assert _PlainMethod().assumes_independent_panel is False

    def test_plain_method_returns_empty(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"), _ai("B", 2, "gpt-4o")],
            moderator_id=None,
        )
        assert _PlainMethod().panel_composition_disclosure(disc) == ""

    def test_indep_method_discloses_concerning(self):
        disc = Discussion(
            id=1, topic="t",
            entities=[_ai("A", 1, "gpt-4o"), _ai("B", 2, "gpt-4o")],
            moderator_id=None,
        )
        text = _IndepMethod().panel_composition_disclosure(disc)
        assert "Panel composition" in text
        assert "caveat" in text.lower()


from consensus.methods.delphi import DelphiMethod
from consensus.methods.belief_diffusion import BeliefDiffusion


def _delphi_disc(models):
    entities = [_ai("Mod", 100, "gpt-4o")]
    for i, m in enumerate(models):
        entities.append(_ai(f"E{i}", i + 1, m))
    disc = Discussion(
        id=1, topic="p?", entities=entities, moderator_id=100,
        discussion_method="delphi",
    )
    disc.method_state = {"estimates": []}
    return disc


class TestMethodOptIn:
    def test_flags_set(self):
        assert DelphiMethod().assumes_independent_panel is True
        assert BeliefDiffusion().assumes_independent_panel is True

    def test_delphi_conclusion_discloses_same_model(self):
        disc = _delphi_disc(["claude", "claude", "claude"])
        prompt = DelphiMethod().get_conclusion_prompt(disc)
        assert "Panel composition" in prompt
        assert "caveat" in prompt.lower()
        # Original body preserved:
        assert "Delphi Method process is complete" in prompt

    def test_delphi_conclusion_diverse_no_caveat(self):
        disc = _delphi_disc(["a", "b", "c"])
        prompt = DelphiMethod().get_conclusion_prompt(disc)
        assert "caveat" not in prompt.lower()
        assert "Delphi Method process is complete" in prompt

    def test_belief_conclusion_discloses_same_model(self):
        entities = [_ai("Mod", 100, "gpt-4o"),
                    _ai("A", 1, "claude"), _ai("B", 2, "claude")]
        disc = Discussion(
            id=1, topic="p?", entities=entities, moderator_id=100,
            discussion_method="belief_diffusion",
        )
        disc.method_state = {
            "hypotheses": ["H1", "H2"], "beliefs": [], "diffuse_round": 0,
        }
        prompt = BeliefDiffusion().get_conclusion_prompt(disc)
        assert "Panel composition" in prompt
        assert "Belief State Diffusion process is complete" in prompt
