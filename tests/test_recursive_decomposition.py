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

    def test_turn_prompt_handles_empty_sub_questions(self, ai_entity):
        """After a MAX_DECOMPOSE_ROUNDS give-up (PR #39 review) the
        prompt must not ask participants to address 'the 0
        sub-questions' — it should fall back to the main question."""
        from consensus.methods.phases.analyze_subquestions import AnalyzeSubquestionsHandler
        handler = AnalyzeSubquestionsHandler()
        disc = Discussion(topic="Why is the sky blue?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {"sub_questions": []}
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "0 sub-questions" not in prompt
        assert "TestAI" in prompt
        assert "main question" in prompt.lower()

    def test_system_prompt_handles_empty_sub_questions(self, ai_entity):
        """Same give-up path: the system prompt must not render an
        empty sub-question list with the **Sub-question N:** format
        instruction (PR #39 review)."""
        from consensus.methods.phases.analyze_subquestions import AnalyzeSubquestionsHandler
        handler = AnalyzeSubquestionsHandler()
        disc = Discussion(topic="Why is the sky blue?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {"sub_questions": []}
        prompt = handler.get_system_prompt(ai_entity, disc)
        assert "identified the following sub-questions" not in prompt
        assert "**Sub-question 1:**" not in prompt
        assert "main question" in prompt.lower()

    def test_conclusion_prompt_handles_empty_sub_questions(self):
        """Same give-up path: the conclusion prompt must not claim the
        group decomposed the question when it never did (PR #39
        review)."""
        from consensus.methods.recursive_decomposition import RecursiveDecomposition
        method = RecursiveDecomposition()
        disc = Discussion(topic="Why is the sky blue?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {"sub_questions": []}
        prompt = method.get_conclusion_prompt(disc)
        assert "decomposed this into the following sub-questions" \
            not in prompt
        assert "Why is the sky blue?" in prompt

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


class TestIntegrateHandler:
    def test_system_prompt_mentions_reinforcements_and_conflicts(self, ai_entity):
        from consensus.methods.phases.integrate_subquestions import IntegrateSubquestionsHandler
        handler = IntegrateSubquestionsHandler()
        disc = Discussion(topic="Big question?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {"current_phase": "integrate"}
        prompt = handler.get_system_prompt(ai_entity, disc)
        assert "Big question?" in prompt
        assert "Reinforcements" in prompt
        assert "Conflicts" in prompt
        assert "Gaps" in prompt

    def test_turn_prompt(self, ai_entity):
        from consensus.methods.phases.integrate_subquestions import IntegrateSubquestionsHandler
        handler = IntegrateSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt
        assert "patterns" in prompt.lower() or "contradictions" in prompt.lower()

    def test_no_structured_extraction(self, ai_entity):
        from consensus.methods.phases.integrate_subquestions import IntegrateSubquestionsHandler
        handler = IntegrateSubquestionsHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {}
        result = handler.process_response("Prose integration.", ai_entity, disc)
        assert result.display_content == "Prose integration."
        assert disc.method_state == {}


class TestRecomposeHandler:
    def test_system_prompt_mentions_synthesis(self, ai_entity):
        from consensus.methods.phases.recompose import RecomposeHandler
        handler = RecomposeHandler()
        disc = Discussion(topic="Original question?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {"current_phase": "recompose"}
        prompt = handler.get_system_prompt(ai_entity, disc)
        assert "Original question?" in prompt
        assert "synthesize" in prompt.lower() or "synthesis" in prompt.lower()

    def test_turn_prompt(self, ai_entity):
        from consensus.methods.phases.recompose import RecomposeHandler
        handler = RecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        prompt = handler.get_turn_prompt(ai_entity, disc)
        assert "TestAI" in prompt
        assert "coherent" in prompt.lower() or "synthesize" in prompt.lower()

    def test_no_structured_extraction(self, ai_entity):
        from consensus.methods.phases.recompose import RecomposeHandler
        handler = RecomposeHandler()
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = {}
        result = handler.process_response("My synthesis.", ai_entity, disc)
        assert result.display_content == "My synthesis."
        assert disc.method_state == {}


class TestRecursiveDecompositionMethod:
    def test_registered_in_registry(self):
        from consensus.methods import get_method, list_methods
        method = get_method("recursive_decomposition")
        assert method.name == "recursive_decomposition"
        assert method.display_name == "Recursive Decomposition"

        names = [m["name"] for m in list_methods()]
        assert "recursive_decomposition" in names

    def test_has_four_phases(self):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        assert len(method.default_phases) == 4
        assert [p.name for p in method.default_phases] == [
            "decompose", "analyze", "integrate", "recompose"
        ]

    def test_init_state(self):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        state = method.init_state(disc)
        assert state["current_phase"] == "decompose"
        assert state["sub_questions"] == []
        assert state["sub_question_analyses"] == {}

    def test_phase_transitions(self):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        disc = Discussion(topic="test", discussion_method="recursive_decomposition")
        disc.method_state = method.init_state(disc)

        disc.method_state["sub_questions"] = ["Q1", "Q2"]
        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True
        new_phase = method.advance_phase(disc)
        assert new_phase.name == "analyze"

        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True
        new_phase = method.advance_phase(disc)
        assert new_phase.name == "integrate"

        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True
        new_phase = method.advance_phase(disc)
        assert new_phase.name == "recompose"

        disc.method_state["phase_round"] = 2
        assert method.should_advance_phase(disc) is True
        result = method.advance_phase(disc)
        assert result is None

    def test_conclusion_prompt_includes_subquestions(self):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        disc = Discussion(topic="Why is the sky blue?",
                          discussion_method="recursive_decomposition")
        disc.method_state = {
            "sub_questions": [
                "What is Rayleigh scattering?",
                "How does wavelength affect scattering?",
            ],
        }
        prompt = method.get_conclusion_prompt(disc)
        assert "Why is the sky blue?" in prompt
        assert "Rayleigh scattering" in prompt
        assert "wavelength" in prompt
        assert "Sub-question findings" in prompt

    def test_system_prompts_delegate_to_handlers(self, ai_entity):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        disc = Discussion(topic="Test topic?",
                          discussion_method="recursive_decomposition")
        disc.method_state = method.init_state(disc)

        prompt = method.get_system_prompt(ai_entity, disc)
        assert "DECOMPOSITION" in prompt

        disc.method_state["current_phase"] = "analyze"
        disc.method_state["sub_questions"] = ["Q1?"]
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "SUB-QUESTION ANALYSIS" in prompt

        disc.method_state["current_phase"] = "integrate"
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "INTEGRATION" in prompt

        disc.method_state["current_phase"] = "recompose"
        prompt = method.get_system_prompt(ai_entity, disc)
        assert "RECOMPOSITION" in prompt

    def test_to_dict(self):
        from consensus.methods import get_method
        method = get_method("recursive_decomposition")
        d = method.to_dict()
        assert d["name"] == "recursive_decomposition"
        assert d["display_name"] == "Recursive Decomposition"
        assert len(d["phases"]) == 4
