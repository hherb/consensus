"""Tests for Recursive Decomposition discussion method."""

import pytest
from consensus.methods.base import ProcessedResponse
from consensus.models import Discussion, Entity, EntityType


@pytest.fixture
def ai_entity():
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def ai_entity_2():
    return Entity(name="TestAI2", entity_type=EntityType.AI, id=2)


class TestDecomposeHandler:
    def test_init_state(self):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        state = handler.init_state(disc)
        assert state["sub_questions"] == []
        assert state["sub_question_analyses"] == {}

    def test_system_prompt_contains_topic(self, ai_entity):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="Why is the sky blue?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {"current_phase": "decompose"}
        prompt = handler.get_system_prompt(ai_entity, disc)
        assert "Why is the sky blue?" in prompt
        assert "3-7" in prompt
        assert "sub-question" in prompt.lower()

    def test_turn_prompt_contains_name(self, ai_entity):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt

    def test_extracts_sub_questions(self, ai_entity):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {"sub_questions": []}

        content = (
            "1. What are the physical mechanisms causing the sky to appear blue?\n"
            "2. How does atmospheric composition affect sky colour?\n"
            "3. Why does the sky change colour at sunset?"
        )
        result = handler.process_response(content, ai_entity, disc)
        assert len(disc.method_state["sub_questions"]) == 3
        assert "physical mechanisms" in disc.method_state["sub_questions"][0].lower()

    def test_deduplicates_sub_questions(self, ai_entity, ai_entity_2):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {"sub_questions": [
            "What are the physical mechanisms causing the sky to appear blue"
        ]}

        content = "1. What are the physical mechanisms that make the sky appear blue"
        handler.process_response(content, ai_entity_2, disc)
        assert len(disc.method_state["sub_questions"]) == 1

        content = "1. How does altitude affect the perceived colour of the sky"
        handler.process_response(content, ai_entity_2, disc)
        assert len(disc.method_state["sub_questions"]) == 2

    def test_should_advance_needs_subquestions_and_round(self):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")

        disc.method_state = {"sub_questions": [], "phase_round": 2}
        assert handler.should_advance(disc) is False

        disc.method_state = {"sub_questions": ["Q1"], "phase_round": 1}
        assert handler.should_advance(disc) is False

        disc.method_state = {"sub_questions": ["Q1"], "phase_round": 2}
        assert handler.should_advance(disc) is True

    def test_summary_prompt(self):
        from consensus.methods.phases.decompose import DecomposeHandler
        handler = DecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        prompt = handler.get_summary_prompt(disc, "Alice", "Bob")
        assert "Alice" in prompt
        assert "Bob" in prompt


class TestAnalyzeHandler:
    def test_system_prompt_lists_subquestions(self, ai_entity):
        from consensus.methods.phases.analyze_subquestions import AnalyzeSubquestionsHandler
        handler = AnalyzeSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {
            "current_phase": "analyze",
            "sub_questions": [
                "What causes X?",
                "How does Y affect Z?",
            ],
        }
        prompt = handler.get_system_prompt(ai_entity, disc)
        assert "What causes X?" in prompt
        assert "How does Y affect Z?" in prompt
        assert "Sub-question 1" in prompt or "1." in prompt

    def test_turn_prompt_contains_count(self, ai_entity):
        from consensus.methods.phases.analyze_subquestions import AnalyzeSubquestionsHandler
        handler = AnalyzeSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {"sub_questions": ["Q1", "Q2", "Q3"]}
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "3" in prompt
        assert "TestAI" in prompt

    def test_process_response_accumulates_analyses(self, ai_entity, ai_entity_2):
        from consensus.methods.phases.analyze_subquestions import AnalyzeSubquestionsHandler
        handler = AnalyzeSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {
            "sub_questions": ["Q1?", "Q2?"],
            "sub_question_analyses": {},
        }

        content1 = "**Sub-question 1:** Analysis from AI1.\n\n**Sub-question 2:** More from AI1."
        handler.process_response(content1, ai_entity, disc)
        assert len(disc.method_state["sub_question_analyses"]["0"]) == 1
        assert disc.method_state["sub_question_analyses"]["0"][0]["entity"] == "TestAI"

        content2 = "**Sub-question 1:** Analysis from AI2.\n\n**Sub-question 2:** More from AI2."
        handler.process_response(content2, ai_entity_2, disc)
        assert len(disc.method_state["sub_question_analyses"]["0"]) == 2

    def test_standard_round_advancement(self):
        from consensus.methods.phases.analyze_subquestions import AnalyzeSubquestionsHandler
        handler = AnalyzeSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {"phase_round": 1}
        assert handler.should_advance(disc) is False
        disc.method_state["phase_round"] = 2
        assert handler.should_advance(disc) is True
