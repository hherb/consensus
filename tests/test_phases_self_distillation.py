"""Tests for Recursive Self-Distillation phase handlers and helpers."""

import pytest

from consensus.methods.phases._distillation_helpers import (
    classify_inference,
    compute_average_validity,
    extract_overall_score,
    extract_validity_scores,
    format_skeleton_display,
    format_validity_table,
    validate_skeleton,
)
from consensus.methods.base import Phase, ProcessedResponse
from consensus.methods.phases.deliberate_distillation import DistillationDeliberateHandler
from consensus.methods.phases.distill_skeleton import DistillSkeletonHandler
from consensus.methods.phases.blind_evaluate import BlindEvaluateHandler
from consensus.methods.phases.synthesize_distillation import SynthesizeDistillationHandler
from consensus.methods.self_distillation import RecursiveSelfDistillation
from consensus.models import Discussion, Entity, EntityType
import consensus.methods as _methods_module
from consensus.methods import get_method, list_methods


# =====================================================================
# Fixtures
# =====================================================================

VALID_SKELETON = {
    "premises": [
        {"id": "P1", "text": "Economic growth correlates with energy consumption"},
        {"id": "P2", "text": "Renewable energy costs have declined 90% since 2010"},
    ],
    "inferences": [
        {"id": "I1", "from": ["P1", "P2"],
         "text": "Renewable energy can sustain economic growth at lower cost"},
        {"id": "I2", "from": ["I1"],
         "text": "Transitioning to renewables need not reduce GDP"},
    ],
    "conclusions": [
        {"id": "C1", "from": ["I1", "I2"],
         "text": "The economic argument against renewable transition is unsound"},
    ],
}


def _make_discussion(n_participants=3):
    """Create a self-distillation discussion with participants."""
    entities = []
    mod = Entity(name="Moderator", entity_type=EntityType.AI, id=100)
    entities.append(mod)
    for i in range(n_participants):
        e = Entity(name=f"Analyst_{i+1}", entity_type=EntityType.AI, id=i + 1)
        entities.append(e)

    disc = Discussion(
        id=1,
        topic="Should we transition to 100% renewable energy?",
        entities=entities,
        moderator_id=100,
        turn_order=[e.id for e in entities if e.id != 100],
        discussion_method="self_distillation",
    )
    return disc, mod


@pytest.fixture
def entity():
    return Entity(name="Analyst_1", entity_type=EntityType.AI, id=1)


@pytest.fixture
def moderator():
    return Entity(name="Moderator", entity_type=EntityType.AI, id=100)


@pytest.fixture
def sd_discussion():
    disc, _ = _make_discussion()
    method = RecursiveSelfDistillation()
    disc.method_state = method.init_state(disc)
    return disc


# =====================================================================
# Helper tests: validate_skeleton
# =====================================================================

class TestValidateSkeleton:
    def test_valid_skeleton(self):
        assert validate_skeleton(VALID_SKELETON) is True

    def test_not_a_dict(self):
        assert validate_skeleton([]) is False
        assert validate_skeleton("string") is False

    def test_missing_key(self):
        data = {
            "premises": [{"id": "P1", "text": "X"}],
            "inferences": [{"id": "I1", "from": ["P1"], "text": "Y"}],
        }
        assert validate_skeleton(data) is False

    def test_empty_lists(self):
        data = {"premises": [], "inferences": [], "conclusions": []}
        assert validate_skeleton(data) is False

    def test_missing_id(self):
        data = {
            "premises": [{"text": "X"}],
            "inferences": [{"id": "I1", "from": ["P1"], "text": "Y"}],
            "conclusions": [{"id": "C1", "from": ["I1"], "text": "Z"}],
        }
        assert validate_skeleton(data) is False

    def test_missing_text(self):
        data = {
            "premises": [{"id": "P1"}],
            "inferences": [{"id": "I1", "from": ["P1"], "text": "Y"}],
            "conclusions": [{"id": "C1", "from": ["I1"], "text": "Z"}],
        }
        assert validate_skeleton(data) is False

    def test_missing_from_on_inference(self):
        data = {
            "premises": [{"id": "P1", "text": "X"}],
            "inferences": [{"id": "I1", "text": "Y"}],
            "conclusions": [{"id": "C1", "from": ["I1"], "text": "Z"}],
        }
        assert validate_skeleton(data) is False

    def test_empty_from_list(self):
        data = {
            "premises": [{"id": "P1", "text": "X"}],
            "inferences": [{"id": "I1", "from": [], "text": "Y"}],
            "conclusions": [{"id": "C1", "from": ["I1"], "text": "Z"}],
        }
        assert validate_skeleton(data) is False

    def test_invalid_from_reference(self):
        """References to non-existent IDs should fail."""
        data = {
            "premises": [{"id": "P1", "text": "X"}],
            "inferences": [{"id": "I1", "from": ["P99"], "text": "Y"}],
            "conclusions": [{"id": "C1", "from": ["I1"], "text": "Z"}],
        }
        assert validate_skeleton(data) is False

    def test_inference_can_reference_prior_inference(self):
        """Inference I2 referencing I1 should be valid."""
        data = {
            "premises": [{"id": "P1", "text": "X"}],
            "inferences": [
                {"id": "I1", "from": ["P1"], "text": "Y"},
                {"id": "I2", "from": ["I1"], "text": "Z"},
            ],
            "conclusions": [{"id": "C1", "from": ["I2"], "text": "W"}],
        }
        assert validate_skeleton(data) is True

    def test_inference_cannot_reference_later_inference(self):
        """Inference I1 referencing I2 (defined after it) should fail."""
        data = {
            "premises": [{"id": "P1", "text": "X"}],
            "inferences": [
                {"id": "I1", "from": ["I2"], "text": "Y"},
                {"id": "I2", "from": ["P1"], "text": "Z"},
            ],
            "conclusions": [{"id": "C1", "from": ["I2"], "text": "W"}],
        }
        assert validate_skeleton(data) is False

    def test_conclusion_can_reference_inference(self):
        data = {
            "premises": [{"id": "P1", "text": "X"}],
            "inferences": [{"id": "I1", "from": ["P1"], "text": "Y"}],
            "conclusions": [{"id": "C1", "from": ["I1"], "text": "Z"}],
        }
        assert validate_skeleton(data) is True

    def test_conclusion_invalid_reference(self):
        data = {
            "premises": [{"id": "P1", "text": "X"}],
            "inferences": [{"id": "I1", "from": ["P1"], "text": "Y"}],
            "conclusions": [{"id": "C1", "from": ["NOPE"], "text": "Z"}],
        }
        assert validate_skeleton(data) is False


# =====================================================================
# Helper tests: format_skeleton_display
# =====================================================================

class TestFormatSkeletonDisplay:
    def test_includes_all_sections(self):
        display = format_skeleton_display(VALID_SKELETON)
        assert "**Premises:**" in display
        assert "**Inferences:**" in display
        assert "**Conclusions:**" in display

    def test_includes_ids_and_text(self):
        display = format_skeleton_display(VALID_SKELETON)
        assert "P1" in display
        assert "P2" in display
        assert "I1" in display
        assert "C1" in display
        assert "Economic growth" in display

    def test_includes_dependencies(self):
        display = format_skeleton_display(VALID_SKELETON)
        assert "from P1, P2" in display
        assert "from I1, I2" in display


# =====================================================================
# Helper tests: score extraction
# =====================================================================

class TestExtractValidityScores:
    def test_standard_tags(self):
        content = "Analysis: [VALIDITY I1: 4] and [VALIDITY I2: 2]"
        scores = extract_validity_scores(content)
        assert scores == {"I1": 4, "I2": 2}

    def test_conclusion_tag(self):
        content = "[VALIDITY C1: 5]"
        assert extract_validity_scores(content) == {"C1": 5}

    def test_case_insensitive(self):
        content = "[validity i1: 3]"
        assert extract_validity_scores(content) == {"I1": 3}

    def test_no_tags(self):
        content = "I think the logic is sound."
        assert extract_validity_scores(content) == {}

    def test_clamps_to_range(self):
        content = "[VALIDITY I1: 0] [VALIDITY I2: 9]"
        scores = extract_validity_scores(content)
        assert scores["I1"] == 1
        assert scores["I2"] == 5

    def test_whitespace_variations(self):
        content = "[VALIDITY  I1:  4 ]"
        assert extract_validity_scores(content) == {"I1": 4}

    def test_multiple_mixed(self):
        content = "[VALIDITY P1: 5] [VALIDITY I1: 3] [VALIDITY C1: 4]"
        scores = extract_validity_scores(content)
        assert len(scores) == 3
        assert scores["P1"] == 5


class TestExtractOverallScore:
    def test_standard_tag(self):
        assert extract_overall_score("[OVERALL: 4]") == 4

    def test_case_insensitive(self):
        assert extract_overall_score("[overall: 3]") == 3

    def test_no_tag(self):
        assert extract_overall_score("No overall score here.") is None

    def test_clamps_to_range(self):
        assert extract_overall_score("[OVERALL: 0]") == 1
        assert extract_overall_score("[OVERALL: 9]") == 5

    def test_in_context(self):
        content = "The argument is strong. [OVERALL: 4] That's my view."
        assert extract_overall_score(content) == 4


# =====================================================================
# Helper tests: averages and classification
# =====================================================================

class TestComputeAverageValidity:
    def test_basic_averages(self):
        scores = {"I1": {"Alice": 4, "Bob": 5}, "I2": {"Alice": 2, "Bob": 1}}
        avgs = compute_average_validity(scores)
        assert avgs["I1"] == 4.5
        assert avgs["I2"] == 1.5

    def test_empty_scores_skipped(self):
        scores = {"I1": {"Alice": 3}, "I2": {}}
        avgs = compute_average_validity(scores)
        assert "I1" in avgs
        assert "I2" not in avgs

    def test_empty_input(self):
        assert compute_average_validity({}) == {}


class TestClassifyInference:
    def test_sound(self):
        assert classify_inference(4.0) == "SOUND"
        assert classify_inference(5.0) == "SOUND"

    def test_questionable(self):
        assert classify_inference(2.5) == "QUESTIONABLE"
        assert classify_inference(3.9) == "QUESTIONABLE"

    def test_weak(self):
        assert classify_inference(2.4) == "WEAK"
        assert classify_inference(1.0) == "WEAK"


# =====================================================================
# Helper tests: format_validity_table
# =====================================================================

class TestFormatValidityTable:
    def test_basic_table(self):
        scores = {"I1": {"Alice": 5, "Bob": 4}, "C1": {"Alice": 3, "Bob": 2}}
        table = format_validity_table(VALID_SKELETON, scores)
        assert "I1" in table
        assert "I2" in table  # present even without scores
        assert "C1" in table
        assert "SOUND" in table  # I1 avg=4.5
        assert "QUESTIONABLE" in table  # C1 avg=2.5

    def test_no_scores(self):
        table = format_validity_table(VALID_SKELETON, {})
        assert "I1" in table
        assert "—" in table  # no scores = dash

    def test_truncates_long_text(self):
        skeleton = {
            "premises": [{"id": "P1", "text": "X"}],
            "inferences": [
                {"id": "I1", "from": ["P1"], "text": "A" * 100},
            ],
            "conclusions": [{"id": "C1", "from": ["I1"], "text": "Z"}],
        }
        table = format_validity_table(skeleton, {})
        assert "..." in table


# =====================================================================
# Phase 1: DistillationDeliberateHandler
# =====================================================================

class TestDistillationDeliberateHandler:
    @pytest.fixture
    def handler(self):
        return DistillationDeliberateHandler()

    def test_phase_metadata(self, handler):
        assert handler.phase.name == "sd_deliberate"
        assert handler.phase.rounds == 2
        assert handler.phase.allow_tools is True

    def test_init_state(self, handler, sd_discussion):
        state = handler.init_state(sd_discussion)
        assert state["rich_reasoning_summary"] is None

    def test_system_prompt_includes_topic(self, handler, entity, sd_discussion):
        prompt = handler.get_system_prompt(entity, sd_discussion)
        assert entity.name in prompt
        assert sd_discussion.topic in prompt
        assert "compelling" in prompt.lower()

    def test_turn_prompt(self, handler, entity, sd_discussion):
        prompt = handler.get_turn_prompt(entity, sd_discussion)
        assert entity.name in prompt
        assert "persuasive" in prompt.lower()

    def test_moderator_final_round_captures_summary(self, handler, moderator, sd_discussion):
        sd_discussion.method_state["phase_round"] = 2
        result = handler.process_response(
            "After rich discussion, the consensus is X.", moderator, sd_discussion
        )
        assert sd_discussion.method_state["rich_reasoning_summary"] == \
            "After rich discussion, the consensus is X."

    def test_non_moderator_does_not_capture(self, handler, entity, sd_discussion):
        sd_discussion.method_state["phase_round"] = 2
        handler.process_response("My view.", entity, sd_discussion)
        assert sd_discussion.method_state["rich_reasoning_summary"] is None

    def test_early_round_does_not_capture(self, handler, moderator, sd_discussion):
        sd_discussion.method_state["phase_round"] = 1
        handler.process_response("Early summary.", moderator, sd_discussion)
        assert sd_discussion.method_state["rich_reasoning_summary"] is None

    def test_should_advance(self, handler, sd_discussion):
        sd_discussion.method_state["phase_round"] = 1
        assert handler.should_advance(sd_discussion) is False
        sd_discussion.method_state["phase_round"] = 3
        assert handler.should_advance(sd_discussion) is True


# =====================================================================
# Phase 2: DistillSkeletonHandler
# =====================================================================

class TestDistillSkeletonHandler:
    @pytest.fixture
    def handler(self):
        return DistillSkeletonHandler()

    def test_phase_metadata(self, handler):
        assert handler.phase.name == "distill"
        assert handler.phase.rounds == 0
        assert handler.phase.allow_tools is False

    def test_init_state(self, handler, sd_discussion):
        state = handler.init_state(sd_discussion)
        assert state["skeleton"] is None
        assert state["skeleton_display"] == ""
        assert state["extraction_attempts"] == 0
        assert state["extraction_failed"] is False

    def test_turn_order_moderator_only(self, handler, sd_discussion):
        result = handler.get_turn_order([1, 2, 3], sd_discussion)
        assert result == [sd_discussion.moderator_id]

    def test_system_prompt(self, handler, entity, sd_discussion):
        prompt = handler.get_system_prompt(entity, sd_discussion)
        assert "skeleton" in prompt.lower()
        assert "strip" in prompt.lower()

    def test_turn_prompt_initial(self, handler, entity, sd_discussion):
        prompt = handler.get_turn_prompt(entity, sd_discussion)
        assert "premises" in prompt.lower()
        assert "inferences" in prompt.lower()
        assert "json" in prompt.lower()

    def test_turn_prompt_retry(self, handler, entity, sd_discussion):
        sd_discussion.method_state["extraction_failed"] = True
        sd_discussion.method_state["extraction_attempts"] = 1
        prompt = handler.get_turn_prompt(entity, sd_discussion)
        assert "try again" in prompt.lower() or "did not produce" in prompt.lower()
        assert "json" in prompt.lower()

    def test_process_response_valid_skeleton(self, handler, moderator, sd_discussion):
        import json
        content = f"Here is the skeleton:\n```json\n{json.dumps(VALID_SKELETON)}\n```"
        result = handler.process_response(content, moderator, sd_discussion)
        assert sd_discussion.method_state["skeleton"] == VALID_SKELETON
        assert sd_discussion.method_state["skeleton_display"] != ""
        assert sd_discussion.method_state["extraction_failed"] is False
        assert result.extracted_data["skeleton"] == VALID_SKELETON

    def test_process_response_invalid_json(self, handler, moderator, sd_discussion):
        content = "I couldn't extract a proper structure."
        handler.process_response(content, moderator, sd_discussion)
        assert sd_discussion.method_state["skeleton"] is None
        assert sd_discussion.method_state["extraction_failed"] is True
        assert sd_discussion.method_state["extraction_attempts"] == 1

    def test_process_response_invalid_structure(self, handler, moderator, sd_discussion):
        import json
        bad = {"premises": [{"id": "P1", "text": "X"}]}  # missing inferences/conclusions
        content = f"```json\n{json.dumps(bad)}\n```"
        handler.process_response(content, moderator, sd_discussion)
        assert sd_discussion.method_state["skeleton"] is None
        assert sd_discussion.method_state["extraction_failed"] is True

    def test_process_response_invalid_references(self, handler, moderator, sd_discussion):
        import json
        bad = {
            "premises": [{"id": "P1", "text": "X"}],
            "inferences": [{"id": "I1", "from": ["NONEXISTENT"], "text": "Y"}],
            "conclusions": [{"id": "C1", "from": ["I1"], "text": "Z"}],
        }
        content = f"```json\n{json.dumps(bad)}\n```"
        handler.process_response(content, moderator, sd_discussion)
        assert sd_discussion.method_state["skeleton"] is None
        assert sd_discussion.method_state["extraction_failed"] is True

    def test_should_advance_with_skeleton(self, handler, sd_discussion):
        sd_discussion.method_state["skeleton"] = VALID_SKELETON
        assert handler.should_advance(sd_discussion) is True

    def test_should_advance_no_skeleton_no_advance(self, handler, sd_discussion):
        sd_discussion.method_state["skeleton"] = None
        sd_discussion.method_state["extraction_attempts"] = 1
        assert handler.should_advance(sd_discussion) is False

    def test_should_advance_gives_up_after_3(self, handler, sd_discussion):
        sd_discussion.method_state["skeleton"] = None
        sd_discussion.method_state["extraction_attempts"] = 3
        assert handler.should_advance(sd_discussion) is True


# =====================================================================
# Phase 3: BlindEvaluateHandler
# =====================================================================

class TestBlindEvaluateHandler:
    @pytest.fixture
    def handler(self):
        return BlindEvaluateHandler()

    @pytest.fixture
    def eval_discussion(self, sd_discussion):
        sd_discussion.method_state["current_phase"] = "blind_evaluate"
        sd_discussion.method_state["skeleton"] = VALID_SKELETON
        sd_discussion.method_state["skeleton_display"] = format_skeleton_display(
            VALID_SKELETON
        )
        return sd_discussion

    def test_phase_metadata(self, handler):
        assert handler.phase.name == "blind_evaluate"
        assert handler.phase.rounds == 1
        assert handler.phase.allow_tools is False

    def test_init_state(self, handler, sd_discussion):
        state = handler.init_state(sd_discussion)
        assert state["validity_scores"] == {}
        assert state["overall_scores"] == {}

    def test_turn_order_excludes_moderator(self, handler, eval_discussion):
        result = handler.get_turn_order([100, 1, 2, 3], eval_discussion)
        assert 100 not in result
        assert result == [1, 2, 3]

    def test_filter_blanks_old_messages(self, handler, eval_discussion):
        """Pre-evaluation messages should be blanked."""
        result = handler.filter_context_message(
            "Analyst_1", "Here is my rich argument with examples...",
            "user", eval_discussion,
        )
        assert result == ""

    def test_filter_keeps_validity_messages(self, handler, eval_discussion):
        """Phase 3 messages with validity tags should be preserved."""
        content = "I1 is strong. [VALIDITY I1: 4] [OVERALL: 3]"
        result = handler.filter_context_message(
            "Analyst_1", content, "user", eval_discussion,
        )
        assert result == content

    def test_filter_keeps_transition_message(self, handler, eval_discussion):
        """The phase transition message should be preserved."""
        content = f"**Phase: {handler.phase.display_name}**\n\nSkeleton..."
        result = handler.filter_context_message(
            "System", content, "user", eval_discussion,
        )
        assert result == content

    def test_system_prompt_includes_skeleton(self, handler, entity, eval_discussion):
        prompt = handler.get_system_prompt(entity, eval_discussion)
        assert "P1" in prompt
        assert "I1" in prompt
        assert "VALIDITY" in prompt
        assert "OVERALL" in prompt
        assert entity.name in prompt

    def test_system_prompt_failed_extraction(self, handler, entity, sd_discussion):
        sd_discussion.method_state["skeleton_display"] = ""
        prompt = handler.get_system_prompt(entity, sd_discussion)
        assert "failed" in prompt.lower()

    def test_turn_prompt_lists_tags(self, handler, entity, eval_discussion):
        prompt = handler.get_turn_prompt(entity, eval_discussion)
        assert "[VALIDITY I1:" in prompt
        assert "[VALIDITY I2:" in prompt
        assert "[VALIDITY C1:" in prompt
        assert "[OVERALL:" in prompt

    def test_process_response_extracts_scores(self, handler, entity, eval_discussion):
        content = (
            "I1 follows well. [VALIDITY I1: 4]\n"
            "I2 is a leap. [VALIDITY I2: 2]\n"
            "C1 is okay. [VALIDITY C1: 3]\n"
            "[OVERALL: 3]"
        )
        result = handler.process_response(content, entity, eval_discussion)
        vs = eval_discussion.method_state["validity_scores"]
        assert vs["I1"]["Analyst_1"] == 4
        assert vs["I2"]["Analyst_1"] == 2
        assert vs["C1"]["Analyst_1"] == 3
        assert eval_discussion.method_state["overall_scores"]["Analyst_1"] == 3
        assert "I1: 4/5" in result.display_content

    def test_process_response_no_scores(self, handler, entity, eval_discussion):
        content = "I think the logic is generally sound."
        result = handler.process_response(content, entity, eval_discussion)
        assert eval_discussion.method_state["validity_scores"] == {}
        assert result.display_content == content  # no bar appended

    def test_process_response_multiple_entities(self, handler, eval_discussion):
        e1 = Entity(name="Alice", entity_type=EntityType.AI, id=1)
        e2 = Entity(name="Bob", entity_type=EntityType.AI, id=2)

        handler.process_response("[VALIDITY I1: 5] [OVERALL: 4]", e1, eval_discussion)
        handler.process_response("[VALIDITY I1: 3] [OVERALL: 2]", e2, eval_discussion)

        vs = eval_discussion.method_state["validity_scores"]
        assert vs["I1"]["Alice"] == 5
        assert vs["I1"]["Bob"] == 3
        os = eval_discussion.method_state["overall_scores"]
        assert os["Alice"] == 4
        assert os["Bob"] == 2

    def test_eval_item_ids(self, handler):
        ids = handler._eval_item_ids(VALID_SKELETON)
        assert ids == ["I1", "I2", "C1"]

    def test_eval_item_ids_empty(self, handler):
        assert handler._eval_item_ids({}) == []


# =====================================================================
# Phase 4: SynthesizeDistillationHandler
# =====================================================================

class TestSynthesizeDistillationHandler:
    @pytest.fixture
    def handler(self):
        return SynthesizeDistillationHandler()

    def test_phase_metadata(self, handler):
        assert handler.phase.name == "sd_synthesize"
        assert handler.phase.rounds == 1
        assert handler.phase.allow_tools is False

    def test_turn_order_moderator_only(self, handler, sd_discussion):
        result = handler.get_turn_order([1, 2, 3], sd_discussion)
        assert result == [sd_discussion.moderator_id]

    def test_system_prompt_empty(self, handler, entity, sd_discussion):
        assert handler.get_system_prompt(entity, sd_discussion) == ""

    def test_turn_prompt_empty(self, handler, entity, sd_discussion):
        assert handler.get_turn_prompt(entity, sd_discussion) == ""


# =====================================================================
# Main method class: RecursiveSelfDistillation
# =====================================================================

class TestRecursiveSelfDistillationIntegration:
    @pytest.fixture
    def method(self):
        return RecursiveSelfDistillation()

    @pytest.fixture
    def discussion(self, method):
        disc, _ = _make_discussion()
        disc.method_state = method.init_state(disc)
        return disc

    # -- Phase auto-derivation --

    def test_phases_auto_derived(self, method):
        assert len(method.default_phases) == 4
        assert method.default_phases[0].name == "sd_deliberate"
        assert method.default_phases[1].name == "distill"
        assert method.default_phases[2].name == "blind_evaluate"
        assert method.default_phases[3].name == "sd_synthesize"

    # -- init_state --

    def test_init_state_default(self, method, discussion):
        state = discussion.method_state
        assert state["current_phase"] == "sd_deliberate"
        assert state["phase_round"] == 1
        assert state["rich_reasoning_summary"] is None
        assert state["skeleton"] is None
        assert state["skeleton_display"] == ""
        assert state["extraction_attempts"] == 0
        assert state["extraction_failed"] is False
        assert state["validity_scores"] == {}
        assert state["overall_scores"] == {}

    # -- get_conclusion_prompt --

    def test_conclusion_prompt_with_skeleton(self, method, discussion):
        discussion.method_state["skeleton"] = VALID_SKELETON
        discussion.method_state["skeleton_display"] = format_skeleton_display(
            VALID_SKELETON
        )
        discussion.method_state["rich_reasoning_summary"] = "Rich argument about energy."
        discussion.method_state["validity_scores"] = {
            "I1": {"Alice": 5, "Bob": 4},
            "I2": {"Alice": 2, "Bob": 1},
        }
        discussion.method_state["overall_scores"] = {"Alice": 4, "Bob": 3}

        prompt = method.get_conclusion_prompt(discussion)
        assert "Rich argument about energy" in prompt
        assert "SOUND" in prompt  # I1 avg=4.5
        assert "WEAK" in prompt   # I2 avg=1.5
        assert "Alice: 4/5" in prompt
        assert "validity" in prompt.lower()
        assert "persuasiveness" in prompt.lower()

    def test_conclusion_prompt_no_skeleton(self, method, discussion):
        discussion.method_state["skeleton"] = None
        prompt = method.get_conclusion_prompt(discussion)
        assert "could not" in prompt.lower()

    # -- Phase advancement --

    def test_advance_deliberate_to_distill(self, method, discussion):
        discussion.method_state["phase_round"] = 3
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "distill"

    def test_advance_distill_to_blind_evaluate(self, method, discussion):
        discussion.method_state["current_phase"] = "distill"
        discussion.method_state["skeleton"] = VALID_SKELETON
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "blind_evaluate"

    def test_advance_blind_evaluate_to_synthesize(self, method, discussion):
        discussion.method_state["current_phase"] = "blind_evaluate"
        discussion.method_state["phase_round"] = 2
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "sd_synthesize"

    def test_advance_synthesize_to_none(self, method, discussion):
        discussion.method_state["current_phase"] = "sd_synthesize"
        discussion.method_state["phase_round"] = 2
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new is None

    def test_advance_chain_with_failed_extraction(self, method, discussion):
        """3 failed extractions -> blind_evaluate -> synthesize."""
        discussion.method_state["current_phase"] = "distill"
        discussion.method_state["skeleton"] = None
        discussion.method_state["extraction_attempts"] = 3
        assert method.should_advance_phase(discussion) is True
        new = method.advance_phase(discussion)
        assert new.name == "blind_evaluate"

    # -- Method delegation --

    def test_system_prompt_deliberate(self, method, discussion):
        entity = Entity(name="Analyst_1", entity_type=EntityType.AI, id=1)
        prompt = method.get_system_prompt(entity, discussion)
        assert "compelling" in prompt.lower()
        assert discussion.topic in prompt

    def test_system_prompt_blind_evaluate(self, method, discussion):
        entity = Entity(name="Analyst_1", entity_type=EntityType.AI, id=1)
        discussion.method_state["current_phase"] = "blind_evaluate"
        discussion.method_state["skeleton"] = VALID_SKELETON
        discussion.method_state["skeleton_display"] = format_skeleton_display(
            VALID_SKELETON
        )
        prompt = method.get_system_prompt(entity, discussion)
        assert "VALIDITY" in prompt
        assert "skeleton" in prompt.lower()


# =====================================================================
# Registration
# =====================================================================

class TestSelfDistillationRegistration:
    def setup_method(self):
        _methods_module._METHODS_METADATA = None
        _methods_module._INSTANCES.pop("self_distillation", None)

    def test_get_method(self):
        method = get_method("self_distillation")
        assert isinstance(method, RecursiveSelfDistillation)
        assert method.name == "self_distillation"

    def test_list_methods_includes_self_distillation(self):
        methods = list_methods()
        names = [m["name"] for m in methods]
        assert "self_distillation" in names

    def test_method_to_dict(self):
        method = RecursiveSelfDistillation()
        d = method.to_dict()
        assert d["name"] == "self_distillation"
        assert len(d["phases"]) == 4
        assert d["phases"][0]["name"] == "sd_deliberate"
