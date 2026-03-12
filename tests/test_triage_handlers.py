"""Tests for Guided Triage phase handlers."""

import pytest
from consensus.methods.base import ProcessedResponse
from consensus.models import Discussion, Entity, EntityType


@pytest.fixture
def human_entity():
    return Entity(name="Alice", entity_type=EntityType.HUMAN, id=1)


@pytest.fixture
def ai_entity():
    return Entity(name="Bot", entity_type=EntityType.AI, id=2)


@pytest.fixture
def moderator_entity():
    return Entity(name="Moderator", entity_type=EntityType.AI, id=3)


class TestTriageIntakeHandler:
    def test_system_prompt_contains_topic(self, human_entity):
        from consensus.methods.phases.triage_intake import TriageIntakeHandler
        handler = TriageIntakeHandler()
        disc = Discussion(topic="Should we expand into Asia?",
                          discussion_method="triage")
        disc.method_state = {"current_phase": "intake"}
        prompt = handler.get_system_prompt(human_entity, disc)
        assert "Should we expand into Asia?" in prompt

    def test_turn_prompt_asks_structured_questions(self, human_entity):
        from consensus.methods.phases.triage_intake import TriageIntakeHandler
        handler = TriageIntakeHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        prompt = handler.get_turn_prompt(human_entity, disc)
        assert "type of question" in prompt.lower() or "kind of question" in prompt.lower()
        assert "decision context" in prompt.lower() or "context" in prompt.lower()
        assert "uncertainty" in prompt.lower()

    def test_turn_order_excludes_ai_entities(self, human_entity, ai_entity, moderator_entity):
        from consensus.methods.phases.triage_intake import TriageIntakeHandler
        handler = TriageIntakeHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.entities = [human_entity, ai_entity, moderator_entity]
        disc.moderator_id = 3
        order = handler.get_turn_order([1, 2], disc)
        assert 1 in order
        assert 2 not in order

    def test_turn_order_empty_when_no_humans(self, ai_entity, moderator_entity):
        from consensus.methods.phases.triage_intake import TriageIntakeHandler
        handler = TriageIntakeHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.entities = [ai_entity, moderator_entity]
        disc.moderator_id = 3
        order = handler.get_turn_order([2], disc)
        assert order == []

    def test_should_advance_skips_when_no_humans(self, ai_entity, moderator_entity):
        from consensus.methods.phases.triage_intake import TriageIntakeHandler
        handler = TriageIntakeHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.entities = [ai_entity, moderator_entity]
        disc.moderator_id = 3
        disc.method_state = {"current_phase": "intake", "phase_round": 1}
        assert handler.should_advance(disc) is True


class TestTriageRecommendHandler:
    def test_turn_order_is_moderator_only(self, moderator_entity):
        from consensus.methods.phases.triage_recommend import TriageRecommendHandler
        handler = TriageRecommendHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.moderator_id = 3
        order = handler.get_turn_order([1, 2, 3], disc)
        assert order == [3]

    def test_system_prompt_references_methodology(self, moderator_entity):
        from consensus.methods.phases.triage_recommend import TriageRecommendHandler
        handler = TriageRecommendHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.method_state = {"current_phase": "recommend"}
        prompt = handler.get_system_prompt(moderator_entity, disc)
        assert "method" in prompt.lower()

    def test_turn_prompt_instructs_synthesis(self, moderator_entity):
        from consensus.methods.phases.triage_recommend import TriageRecommendHandler
        handler = TriageRecommendHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        prompt = handler.get_turn_prompt(moderator_entity, disc)
        assert "synthesize" in prompt.lower() or "characteriz" in prompt.lower()

    def test_init_state_keys(self):
        from consensus.methods.phases.triage_recommend import TriageRecommendHandler
        handler = TriageRecommendHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        state = handler.init_state(disc)
        assert "recommendations" in state
        assert "recommended_method" in state
        assert "chosen_method" in state


class TestTriageConfirmHandler:
    def test_turn_prompt_shows_recommendations(self, ai_entity):
        from consensus.methods.phases.triage_confirm import TriageConfirmHandler
        handler = TriageConfirmHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.method_state = {
            "current_phase": "confirm",
            "recommendations": [
                {"method_name": "ach", "display_name": "ACH",
                 "confidence": 0.9, "reasoning": "Good fit.",
                 "fit_factors": ["hypothesis"]},
            ],
            "recommended_method": "ach",
        }
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "ACH" in prompt
        assert "ach" in prompt.lower()

    def test_process_response_extracts_chosen_method(self, moderator_entity):
        from consensus.methods.phases.triage_confirm import TriageConfirmHandler
        handler = TriageConfirmHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.moderator_id = 3
        disc.method_state = {
            "current_phase": "confirm",
            "recommendations": [
                {"method_name": "ach", "display_name": "ACH",
                 "confidence": 0.9, "reasoning": "r", "fit_factors": []},
            ],
            "recommended_method": "ach",
            "chosen_method": None,
        }
        content = "Based on the group's agreement, I recommend we proceed with `ach` — Analysis of Competing Hypotheses."
        handler.process_response(content, moderator_entity, disc)
        assert disc.method_state["chosen_method"] == "ach"

    def test_process_response_falls_back_to_recommended(self, moderator_entity):
        from consensus.methods.phases.triage_confirm import TriageConfirmHandler
        handler = TriageConfirmHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.moderator_id = 3
        disc.method_state = {
            "current_phase": "confirm",
            "recommendations": [],
            "recommended_method": "delphi",
            "chosen_method": None,
        }
        content = "Let's proceed with the recommended method."
        handler.process_response(content, moderator_entity, disc)
        assert disc.method_state["chosen_method"] == "delphi"

    def test_process_response_ignores_non_moderator(self, ai_entity):
        from consensus.methods.phases.triage_confirm import TriageConfirmHandler
        handler = TriageConfirmHandler()
        disc = Discussion(topic="test", discussion_method="triage")
        disc.moderator_id = 3
        disc.method_state = {
            "current_phase": "confirm",
            "recommendations": [
                {"method_name": "ach", "display_name": "ACH",
                 "confidence": 0.9, "reasoning": "r", "fit_factors": []},
            ],
            "recommended_method": "ach",
            "chosen_method": None,
        }
        handler.process_response("I agree with ach", ai_entity, disc)
        assert disc.method_state["chosen_method"] is None
