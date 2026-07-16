"""Real-pipeline end-to-end flow tests for the four newest methods.

Each test drives one method from ``start_discussion`` to
``method_complete`` through the production pipeline
(``submit_human_message`` -> ``complete_turn``) with an all-human
roster: phase transitions, moderator-only turn-order handoffs, and (for
Double Crux / Tree of Thoughts) loop routing all run through the real
``should_advance_phase`` -> ``advance_phase`` -> ``apply_method_turn_order``
machinery.  Nothing is pre-seeded into ``method_state``.

Spec: docs/superpowers/specs/2026-07-16-method-flow-e2e-tests-design.md
"""

import pytest

from consensus.methods import get_method
from consensus.methods.phases._ngt_helpers import tally_points
from consensus.models import Discussion, Entity
from tests.flow_e2e_helpers import (
    db_method_state,
    run_method,
    start_method_discussion,
)

# ---------------------------------------------------------------------------
# Nominal Group Technique — straight line through all five phases
# ---------------------------------------------------------------------------

#: Two ideas per participant, pairwise word-overlap far below the 0.7
#: clustering threshold so all six survive as distinct ideas.
NGT_IDEAS = {
    "P1": "1. Build solar panel arrays across municipal rooftops\n"
          "2. Launch community composting hubs in every district",
    "P2": "1. Retrofit old buildings with modern insulation standards\n"
          "2. Create protected cycling corridors through downtown",
    "P3": "1. Deploy neighbourhood battery storage cooperatives\n"
          "2. Plant native drought-resistant trees along avenues",
}

#: The moderator's consolidation (numbered list, items >= 10 chars).
NGT_CANDIDATES = (
    "1. Municipal rooftop solar arrays\n"
    "2. Community composting hubs\n"
    "3. Building insulation retrofits\n"
    "4. Protected cycling corridors"
)

#: Each batch sums to exactly POINTS_PER_VOTER (10) — the free-text
#: allocation gate is all-or-nothing.  Totals: C1=11, C2=8, C4=6, C3=5.
NGT_ALLOCATIONS = {
    "P1": "Candidate 1: 6 points\nCandidate 2: 4 points",
    "P2": "Candidate 1: 5 points\nCandidate 3: 5 points",
    "P3": "Candidate 4: 6 points\nCandidate 2: 4 points",
}


def ngt_content(disc: Discussion, speaker: Entity) -> str:
    """Scripted turn content for the NGT run, keyed on the live phase."""
    phase = disc.method_state["current_phase"]
    if phase == "generate":
        return NGT_IDEAS[speaker.name]
    if phase == "cluster":
        return NGT_CANDIDATES
    if phase == "clarify":
        return ("Could the composting hubs accept commercial food waste "
                "as well as household waste?")
    if phase == "allocate":
        return NGT_ALLOCATIONS[speaker.name]
    if phase == "rank":
        return "Here is the final ranking based on your point allocations."
    pytest.fail(f"unexpected NGT phase {phase!r} for {speaker.name}")


class TestNominalGroupFlow:
    """NGT full lifecycle: generate -> cluster -> clarify -> allocate -> rank."""

    @pytest.mark.asyncio
    async def test_full_run(self, tmp_db):
        disc, moderator, pricing, mod, parts = start_method_discussion(
            tmp_db, "nominal_group", n_participants=3,
            topic="How can our city cut household carbon emissions?",
        )

        trace, result = await run_method(
            disc, moderator, tmp_db, pricing, ngt_content)

        # --- Flow ---------------------------------------------------
        assert result.get("method_complete") is True
        assert [phase for phase, _ in trace] == (
            ["generate"] * 3 + ["cluster"] + ["clarify"] * 3
            + ["allocate"] * 3 + ["rank"]
        )
        assert [name for phase, name in trace if phase == "generate"] == [
            "P1", "P2", "P3"]
        assert all(name == "Mod" for phase, name in trace
                   if phase in ("cluster", "rank")), (
            "moderator-only phases must be spoken by the moderator alone")

        # --- Artifacts ----------------------------------------------
        state = disc.method_state
        assert len(state["ideas"]) == 6, "all six distinct ideas recorded"
        assert [c["id"] for c in state["candidates"]] == [1, 2, 3, 4]
        assert tally_points(state) == {1: 11, 2: 8, 3: 5, 4: 6}

        # --- Conclusion prompt renders the real data -----------------
        prompt = get_method("nominal_group").get_conclusion_prompt(disc)
        assert "3 participant(s) allocated 10 points each" in prompt
        assert "Municipal rooftop solar arrays" in prompt
        assert "11 point(s) from 2 participant(s)" in prompt

        # --- Persistence sanity (issue #16 convention) ---------------
        persisted = db_method_state(tmp_db, disc.id)
        assert persisted["current_phase"] == state["current_phase"]
        assert persisted["candidates"] == state["candidates"]
