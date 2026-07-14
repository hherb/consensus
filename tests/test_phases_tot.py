"""Tests for the Tree of Thoughts phase handlers (issue #26).

Handler-level coverage: prompts, free-text fallback parsing,
anonymisation, advancement/give-up caps, the propose-phase abort,
deterministic prune routing (loop / converged / depth budget /
degenerate), turn orders, and method-level assembly.
"""

import pytest

from consensus.methods import get_method, list_methods
from consensus.methods.base import LINEAR_NEXT
from consensus.methods.phases._tot_analysis import compute_beam
from consensus.methods.phases._tot_helpers import (
    MAX_PROPOSE_ROUNDS,
    MAX_TOT_DEPTH,
    STOP_CONVERGED,
    STOP_DEGENERATE,
    STOP_DEPTH,
    record_thought_scores,
    record_thoughts,
)
from consensus.methods.phases.expand_thoughts import ExpandThoughtsHandler
from consensus.methods.phases.propose_thoughts import ProposeThoughtsHandler
from consensus.methods.phases.prune_thoughts import PruneThoughtsHandler
from consensus.methods.phases.score_thoughts import ScoreThoughtsHandler
from consensus.methods.phases.synthesise_thoughts import (
    SynthesiseThoughtsHandler,
)
from consensus.models import Discussion, Entity, EntityType


@pytest.fixture
def ai_entity() -> Entity:
    return Entity(name="TestAI", entity_type=EntityType.AI, id=1)


@pytest.fixture
def moderator() -> Entity:
    return Entity(name="Mod", entity_type=EntityType.AI, id=99)


def _base_state() -> dict:
    """The union of all ToT handlers' init_state keys, built by hand so
    handler tests do not depend on the (later-task) method registration."""
    return {"current_phase": "propose", "phase_round": 1, "thoughts": [],
            "thought_scores": {}, "beam_history": [], "tot_artifact": {},
            "expansions": []}


def make_disc(**state) -> Discussion:
    """A discussion in the ToT method with a moderator and two panelists."""
    mod = Entity(name="Mod", entity_type=EntityType.AI, id=99)
    alice = Entity(name="TestAI", entity_type=EntityType.AI, id=1)
    bob = Entity(name="Bob", entity_type=EntityType.HUMAN, id=2)
    disc = Discussion(topic="How should we grow the developer community?",
                      discussion_method="tree_of_thoughts",
                      entities=[mod, alice, bob],
                      moderator_id=99,
                      turn_order=[1, 2])
    disc.method_state = _base_state()
    disc.method_state.update(state)
    return disc


THOUGHT_LINES = (
    "1. Launch a public plugin marketplace with revenue sharing\n"
    "2. Run quarterly community hackathons with sponsored prizes"
)

DISTINCT_TEXTS = [
    "Launch a public plugin marketplace with revenue sharing",
    "Run quarterly community hackathons with sponsored prizes",
    "Publish a certification program for advanced practitioners",
    "Fund an ambassador program in regional user groups",
]


def _seed_thoughts(disc: Discussion, n: int = 4) -> None:
    record_thoughts(disc.method_state,
                    Entity(name="Seed", entity_type=EntityType.AI, id=50),
                    DISTINCT_TEXTS[:n])
    assert len(disc.method_state["thoughts"]) == n


class TestProposeThoughtsHandler:
    def test_phase_metadata(self):
        handler = ProposeThoughtsHandler()
        assert handler.phase.name == "propose"
        assert handler.phase.rounds == 1

    def test_init_state(self):
        assert ProposeThoughtsHandler().init_state(make_disc()) == {
            "thoughts": []}

    def test_system_prompt_names_tool_and_topic(self, ai_entity):
        prompt = ProposeThoughtsHandler().get_system_prompt(
            ai_entity, make_disc())
        assert "submit_thoughts" in prompt
        assert "TestAI" in prompt
        assert "developer community" in prompt
        assert "distinct" in prompt.lower()

    def test_context_is_anonymised(self, ai_entity):
        disc = make_disc()
        filtered = ProposeThoughtsHandler().filter_context_message(
            "TestAI", "TestAI: my proposal", "assistant", disc,
            current_entity_id=2)
        assert "TestAI" not in filtered

    def test_free_text_fallback_parses_numbered_list(self, ai_entity):
        disc = make_disc()
        result = ProposeThoughtsHandler().process_response(
            THOUGHT_LINES, ai_entity, disc)
        assert len(disc.method_state["thoughts"]) == 2
        assert result.display_content == THOUGHT_LINES

    def test_unparseable_free_text_records_nothing(self, ai_entity):
        disc = make_disc()
        ProposeThoughtsHandler().process_response(
            "I have no list for you.", ai_entity, disc)
        assert disc.method_state["thoughts"] == []

    def test_advances_after_round_with_thoughts(self):
        disc = make_disc(phase_round=2)
        _seed_thoughts(disc, 2)
        assert ProposeThoughtsHandler().should_advance(disc) is True

    def test_waits_when_no_thoughts_within_budget(self):
        disc = make_disc(phase_round=2)
        assert ProposeThoughtsHandler().should_advance(disc) is False

    def test_gives_up_after_max_rounds(self):
        disc = make_disc(phase_round=MAX_PROPOSE_ROUNDS + 1)
        assert ProposeThoughtsHandler().should_advance(disc) is True

    def test_next_phase_aborts_on_giveup_without_thoughts(self):
        disc = make_disc(phase_round=MAX_PROPOSE_ROUNDS + 1)
        assert ProposeThoughtsHandler().next_phase(disc) is None
        message = ProposeThoughtsHandler().get_method_complete_message(disc)
        assert "ended early" in message.lower()

    def test_next_phase_linear_with_thoughts(self):
        disc = make_disc(phase_round=2)
        _seed_thoughts(disc, 2)
        assert ProposeThoughtsHandler().next_phase(disc) == LINEAR_NEXT
        assert ProposeThoughtsHandler().get_method_complete_message(
            disc) == ""


SCORES_BLOCK = (
    'Here are my scores:\n```json\n'
    '{"scores": {"T1": {"feasibility": 4, "impact": 5, "risk": 2},'
    ' "T2": {"feasibility": 2, "impact": 3, "risk": 4}}}\n```'
)


class TestScoreThoughtsHandler:
    def test_phase_metadata(self):
        handler = ScoreThoughtsHandler()
        assert handler.phase.name == "score"
        assert handler.phase.rounds == 1

    def test_init_state(self):
        assert ScoreThoughtsHandler().init_state(make_disc()) == {
            "thought_scores": {}}

    def test_system_prompt_lists_thoughts_and_dimensions(self, ai_entity):
        disc = make_disc(current_phase="score")
        _seed_thoughts(disc, 2)
        prompt = ScoreThoughtsHandler().get_system_prompt(ai_entity, disc)
        assert "submit_thought_scores" in prompt
        assert "T1" in prompt and "T2" in prompt
        assert "feasibility" in prompt.lower()
        assert "risk" in prompt.lower()

    def test_rescore_prompt_includes_expansions(self, ai_entity):
        disc = make_disc(current_phase="score")
        _seed_thoughts(disc, 3)
        disc.method_state["beam_history"] = [
            {"depth": 1, "beam_ids": [1, 2], "ranking": []}]
        disc.method_state["expansions"] = [
            {"depth": 1, "entity_id": 1, "entity_name": "TestAI",
             "thought_id": 1,
             "refinement": "Stage the marketplace rollout regionally",
             "obstacles": ["Payment-provider integration"]}]
        prompt = ScoreThoughtsHandler().get_system_prompt(ai_entity, disc)
        assert "Stage the marketplace rollout" in prompt
        assert "re-score" in prompt.lower() or "rescore" in prompt.lower()
        assert "T3" not in prompt  # pruned thought is gone

    def test_free_text_fallback_records_scores(self, ai_entity):
        disc = make_disc(current_phase="score")
        _seed_thoughts(disc, 2)
        result = ScoreThoughtsHandler().process_response(
            SCORES_BLOCK, ai_entity, disc)
        assert disc.method_state["thought_scores"]["1"]["T1"] == {
            "feasibility": 4, "impact": 5, "risk": 2}
        assert "T1" in result.display_content

    def test_unparseable_free_text_records_nothing(self, ai_entity):
        disc = make_disc(current_phase="score")
        _seed_thoughts(disc, 2)
        ScoreThoughtsHandler().process_response(
            "They all seem fine to me.", ai_entity, disc)
        assert disc.method_state["thought_scores"] == {}

    def test_advances_after_one_round(self):
        disc = make_disc(current_phase="score", phase_round=2)
        assert ScoreThoughtsHandler().should_advance(disc) is True

    def test_context_is_anonymised(self, ai_entity):
        """Scoring must stay blind to authorship: the propose-phase
        turns replay in context and would otherwise leak names."""
        disc = make_disc(current_phase="score")
        filtered = ScoreThoughtsHandler().filter_context_message(
            "TestAI", "TestAI: 1. my idea", "assistant", disc,
            current_entity_id=2)
        assert "TestAI" not in filtered


def _score_all(disc: Discussion, entity_id: int = 1,
               feasibility_by_id: dict[int, int] | None = None) -> None:
    """Record one entity's scores for every recorded thought."""
    scores = {}
    for t in disc.method_state["thoughts"]:
        f = (feasibility_by_id or {}).get(t["id"], 3)
        scores[f"T{t['id']}"] = {"feasibility": f, "impact": 3, "risk": 3}
    record_thought_scores(
        disc.method_state,
        Entity(name="Scorer", entity_type=EntityType.AI, id=entity_id),
        scores)


class TestPruneThoughtsHandler:
    def test_phase_metadata_and_not_structured(self):
        handler = PruneThoughtsHandler()
        assert handler.phase.name == "prune"
        assert handler.phase.rounds == 1
        assert handler.requires_structured_output is False

    def test_init_state(self):
        assert PruneThoughtsHandler().init_state(make_disc()) == {
            "beam_history": [], "tot_artifact": {}}

    def test_moderator_only_turn_order(self):
        disc = make_disc(current_phase="prune")
        assert PruneThoughtsHandler().get_turn_order([1, 2], disc) == [99]

    def test_system_prompt_shows_ranking_without_mutating(self, moderator):
        disc = make_disc(current_phase="prune")
        _seed_thoughts(disc, 4)
        _score_all(disc, feasibility_by_id={1: 5, 2: 4, 3: 2, 4: 1})
        prompt = PruneThoughtsHandler().get_system_prompt(moderator, disc)
        assert "T1" in prompt and "T4" in prompt
        assert disc.method_state["beam_history"] == []  # pure read

    def test_first_prune_continues_linearly_and_records_beam(self):
        disc = make_disc(current_phase="prune")
        _seed_thoughts(disc, 4)
        _score_all(disc, feasibility_by_id={1: 5, 2: 4, 3: 2, 4: 1})
        assert PruneThoughtsHandler().next_phase(disc) == LINEAR_NEXT
        history = disc.method_state["beam_history"]
        assert len(history) == 1
        assert history[0]["depth"] == 1
        assert history[0]["beam_ids"] == [1, 2, 3]
        assert disc.method_state["tot_artifact"] == {}

    def test_identical_beam_with_fresh_scores_converges(self):
        disc = make_disc(current_phase="prune")
        _seed_thoughts(disc, 4)
        _score_all(disc, feasibility_by_id={1: 5, 2: 4, 3: 2, 4: 1})
        beam_ids, ranking = compute_beam(disc.method_state)
        disc.method_state["beam_history"] = [
            {"depth": 1, "beam_ids": beam_ids, "ranking": ranking}]
        # A genuine re-score pass lands (same values → same order).
        _score_all(disc, entity_id=2,
                   feasibility_by_id={1: 5, 2: 4, 3: 2, 4: 1})
        assert PruneThoughtsHandler().next_phase(disc) == "synthesise"
        artifact = disc.method_state["tot_artifact"]
        assert artifact["stop_reason"] == STOP_CONVERGED
        assert artifact["converged"] is True
        assert len(disc.method_state["beam_history"]) == 2

    def test_identical_beam_without_fresh_scores_keeps_looping(self):
        disc = make_disc(current_phase="prune")
        _seed_thoughts(disc, 4)
        _score_all(disc, feasibility_by_id={1: 5, 2: 4, 3: 2, 4: 1})
        beam_ids, ranking = compute_beam(disc.method_state)
        disc.method_state["beam_history"] = [
            {"depth": 1, "beam_ids": beam_ids, "ranking": ranking}]
        # No score landed during the re-score pass (all extractions
        # failed): stability under zero new data is not convergence.
        assert PruneThoughtsHandler().next_phase(disc) == LINEAR_NEXT
        assert disc.method_state["tot_artifact"] == {}

    def test_reordered_beam_is_not_converged(self):
        disc = make_disc(current_phase="prune")
        _seed_thoughts(disc, 4)
        _score_all(disc, feasibility_by_id={1: 5, 2: 4, 3: 2, 4: 1})
        # Same survivors, different order → still moving, keep looping.
        disc.method_state["beam_history"] = [
            {"depth": 1, "beam_ids": [2, 1, 3], "ranking": []}]
        assert PruneThoughtsHandler().next_phase(disc) == LINEAR_NEXT
        assert disc.method_state["tot_artifact"] == {}

    def test_depth_budget_forces_synthesise(self):
        disc = make_disc(current_phase="prune")
        _seed_thoughts(disc, 4)
        # The latest prior beam holds the same survivors in a different
        # order, so convergence never triggers; the budget must.
        disc.method_state["beam_history"] = [
            {"depth": 1, "beam_ids": [1, 2, 3], "ranking": []},
            {"depth": 2, "beam_ids": [2, 1, 3], "ranking": []},
        ][:MAX_TOT_DEPTH - 1]
        _score_all(disc, feasibility_by_id={1: 5, 2: 4, 3: 2, 4: 1})
        assert PruneThoughtsHandler().next_phase(disc) == "synthesise"
        artifact = disc.method_state["tot_artifact"]
        assert artifact["stop_reason"] == STOP_DEPTH
        assert artifact["depth"] == MAX_TOT_DEPTH

    def test_single_thought_is_degenerate(self):
        disc = make_disc(current_phase="prune")
        _seed_thoughts(disc, 1)
        _score_all(disc)
        assert PruneThoughtsHandler().next_phase(disc) == "synthesise"
        assert (disc.method_state["tot_artifact"]["stop_reason"]
                == STOP_DEGENERATE)

    def test_transition_message_embeds_ranking(self):
        """A human moderator must see the computed ranking in the
        transcript — the system prompt is only rendered for AI."""
        disc = make_disc(current_phase="prune")
        _seed_thoughts(disc, 2)
        _score_all(disc, feasibility_by_id={1: 5, 2: 2})
        message = PruneThoughtsHandler().get_transition_message(disc)
        assert "T1" in message and "T2" in message
        assert "prune" in message.lower()

    def test_context_is_anonymised(self, ai_entity):
        disc = make_disc(current_phase="prune")
        filtered = PruneThoughtsHandler().filter_context_message(
            "TestAI", "TestAI: my scores", "assistant", disc,
            current_entity_id=99)
        assert "TestAI" not in filtered


def _disc_with_beam(**state) -> Discussion:
    """A discussion after the first prune: beam = thoughts 1 and 2."""
    disc = make_disc(current_phase="expand", **state)
    _seed_thoughts(disc, 3)
    disc.method_state["beam_history"] = [
        {"depth": 1, "beam_ids": [1, 2],
         "ranking": [{"id": 1, "composite": 12.0, "scorer_count": 1},
                     {"id": 2, "composite": 10.0, "scorer_count": 1}]}]
    return disc


EXPANSIONS_BLOCK = (
    'My deep-dive:\n```json\n'
    '{"expansions": [{"thought_id": 1, "refinement": '
    '"Pilot the marketplace with ten hand-picked partners first", '
    '"obstacles": ["Payment integration"]}]}\n```'
)


class TestExpandThoughtsHandler:
    def test_phase_metadata(self):
        handler = ExpandThoughtsHandler()
        assert handler.phase.name == "expand"
        assert handler.phase.rounds == 1

    def test_init_state(self):
        assert ExpandThoughtsHandler().init_state(make_disc()) == {
            "expansions": []}

    def test_system_prompt_shows_beam_only(self, ai_entity):
        disc = _disc_with_beam()
        prompt = ExpandThoughtsHandler().get_system_prompt(
            ai_entity, disc)
        assert "submit_expansions" in prompt
        assert "T1" in prompt and "T2" in prompt
        assert "T3" not in prompt  # pruned
        assert "obstacle" in prompt.lower()

    def test_free_text_fallback_records_expansions(self, ai_entity):
        disc = _disc_with_beam()
        ExpandThoughtsHandler().process_response(
            EXPANSIONS_BLOCK, ai_entity, disc)
        recorded = disc.method_state["expansions"]
        assert len(recorded) == 1
        assert recorded[0]["thought_id"] == 1
        assert recorded[0]["depth"] == 1

    def test_unparseable_free_text_records_nothing(self, ai_entity):
        disc = _disc_with_beam()
        ExpandThoughtsHandler().process_response(
            "It all looks good.", ai_entity, disc)
        assert disc.method_state["expansions"] == []

    def test_next_phase_loops_back_to_score(self):
        assert ExpandThoughtsHandler().next_phase(
            _disc_with_beam()) == "score"

    def test_advances_after_one_round(self):
        disc = _disc_with_beam(phase_round=2)
        assert ExpandThoughtsHandler().should_advance(disc) is True

    def test_context_is_anonymised(self, ai_entity):
        disc = _disc_with_beam()
        filtered = ExpandThoughtsHandler().filter_context_message(
            "TestAI", "TestAI: obstacles are…", "assistant", disc,
            current_entity_id=2)
        assert "TestAI" not in filtered


def _artifact(stop_reason: str = STOP_CONVERGED) -> dict:
    return {
        "recommendation": {"id": 1, "text": "Launch a plugin marketplace",
                           "composite": 12.5},
        "converged": stop_reason == STOP_CONVERGED,
        "stop_reason": stop_reason,
        "depth": 2,
        "final_beam": [{"id": 1, "text": "Launch a plugin marketplace",
                        "composite": 12.5, "scorer_count": 2}],
        "beam_history": [{"depth": 1, "beam_ids": [1], "ranking": []},
                         {"depth": 2, "beam_ids": [1], "ranking": []}],
        "expansions": [],
        "caveats": ["A caveat about scoring coverage"],
    }


class TestSynthesiseThoughtsHandler:
    def test_phase_metadata_and_not_structured(self):
        handler = SynthesiseThoughtsHandler()
        assert handler.phase.name == "synthesise"
        assert handler.phase.rounds == 1
        assert handler.requires_structured_output is False

    def test_moderator_only_turn_order(self):
        disc = make_disc(current_phase="synthesise")
        assert SynthesiseThoughtsHandler().get_turn_order(
            [1, 2], disc) == [99]

    def test_system_prompt_quotes_artifact(self, moderator):
        disc = make_disc(current_phase="synthesise",
                         tot_artifact=_artifact())
        prompt = SynthesiseThoughtsHandler().get_system_prompt(
            moderator, disc)
        assert "plugin marketplace" in prompt
        assert "12.5" in prompt
        assert "caveat about scoring coverage" in prompt

    def test_transition_message_embeds_digest(self):
        """A human moderator must see the outcome numbers in the
        transcript, not only in the AI-only system prompt."""
        disc = make_disc(current_phase="synthesise",
                         tot_artifact=_artifact())
        message = SynthesiseThoughtsHandler().get_transition_message(disc)
        assert "plugin marketplace" in message
        assert "12.5" in message

    def test_context_is_anonymised(self, moderator):
        disc = make_disc(current_phase="synthesise")
        filtered = SynthesiseThoughtsHandler().filter_context_message(
            "TestAI", "TestAI said things", "assistant", disc,
            current_entity_id=99)
        assert "TestAI" not in filtered


class TestTreeOfThoughtsMethod:
    def test_registry_and_metadata(self):
        method = get_method("tree_of_thoughts")
        assert method.display_name == "Tree of Thoughts"
        assert [p.name for p in method.default_phases] == [
            "propose", "score", "prune", "expand", "synthesise"]
        assert any(m["name"] == "tree_of_thoughts" for m in list_methods())

    def test_requires_structured_output(self):
        assert (get_method("tree_of_thoughts").requires_structured_output()
                is True)

    def test_init_state_merges_handler_keys(self):
        disc = make_disc()
        state = get_method("tree_of_thoughts").init_state(disc)
        for key in ("thoughts", "thought_scores", "beam_history",
                    "tot_artifact", "expansions"):
            assert key in state
        assert state["current_phase"] == "propose"

    def test_conclusion_prompt_quotes_artifact_numbers(self):
        method = get_method("tree_of_thoughts")
        disc = make_disc(tot_artifact=_artifact())
        prompt = method.get_conclusion_prompt(disc)
        assert "plugin marketplace" in prompt
        assert "12.5" in prompt

    def test_conclusion_prompt_varies_by_stop_reason(self):
        method = get_method("tree_of_thoughts")
        converged = method.get_conclusion_prompt(
            make_disc(tot_artifact=_artifact(STOP_CONVERGED)))
        depth = method.get_conclusion_prompt(
            make_disc(tot_artifact=_artifact(STOP_DEPTH)))
        assert converged != depth

    def test_conclusion_honest_after_propose_abort(self):
        """No artifact + no thoughts → no invented depth-budget story."""
        method = get_method("tree_of_thoughts")
        prompt = method.get_conclusion_prompt(make_disc())
        assert "depth budget" not in prompt.lower()
        assert "no approaches were generated" in prompt.lower()
        assert "Do NOT invent" in prompt

    def test_conclusion_honest_when_ended_mid_method(self):
        """Thoughts exist but no prune outcome was recorded."""
        method = get_method("tree_of_thoughts")
        disc = make_disc()
        _seed_thoughts(disc, 2)
        prompt = method.get_conclusion_prompt(disc)
        assert "depth budget" not in prompt.lower()
        assert "did not finish" in prompt

    def test_taxonomy_mentions_tree_of_thoughts(self):
        from consensus.methods.recommender import _TAXONOMY
        assert "Tree of Thoughts" in _TAXONOMY
