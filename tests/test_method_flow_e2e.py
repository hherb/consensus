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


# ---------------------------------------------------------------------------
# Weighted Decision Matrix — straight line through all five phases
# ---------------------------------------------------------------------------

#: Distinct options (pairwise overlap ~0) -> O1, O2 from P1; O3 from P2.
MCDA_OPTIONS = {
    "P1": "1. Adopt PostgreSQL as the primary datastore\n"
          "2. Keep SQLite with a write-ahead log",
    "P2": "1. Move everything to a managed cloud database",
}

#: The '(weight: N)' suffix is the only parsed free-text weight form.
#: 'Operational cost' appears from both entities -> one criterion C1
#: with weight votes {P1: 4, P2: 2}.  Criteria rounds=2: the same lines
#: are resubmitted in round 2 (idempotent last-write-wins refinement).
MCDA_CRITERIA = {
    "P1": "1. Operational cost (weight: 4)\n"
          "2. Query performance (weight: 3)",
    "P2": "1. Operational cost (weight: 2)\n"
          "2. Migration effort (weight: 5)",
}

MCDA_SCORES = {
    "P1": ("My scores:\n```json\n"
           '{"scores": {'
           '"O1": {"C1": 5, "C2": 4, "C3": 4}, '
           '"O2": {"C1": 3, "C2": 3, "C3": 2}, '
           '"O3": {"C1": 2, "C2": 4, "C3": 1}}}'
           "\n```"),
    "P2": ("My scores:\n```json\n"
           '{"scores": {'
           '"O1": {"C1": 4, "C2": 4, "C3": 4}, '
           '"O2": {"C1": 3, "C2": 2, "C3": 2}, '
           '"O3": {"C1": 2, "C2": 3, "C3": 1}}}'
           "\n```"),
}


def mcda_content(disc: Discussion, speaker: Entity) -> str:
    """Scripted turn content for the MCDA run, keyed on the live phase."""
    phase = disc.method_state["current_phase"]
    if phase == "options":
        return MCDA_OPTIONS[speaker.name]
    if phase == "criteria":
        return MCDA_CRITERIA[speaker.name]
    if phase == "score":
        return MCDA_SCORES[speaker.name]
    if phase == "sensitivity":
        return ("The weighted ranking is robust: the leader holds under "
                "the tested weight variations.")
    if phase == "decide":
        return ("I recommend adopting PostgreSQL as the primary "
                "datastore based on the weighted results.")
    pytest.fail(f"unexpected MCDA phase {phase!r} for {speaker.name}")


class TestDecisionMatrixFlow:
    """MCDA full lifecycle: options -> criteria -> score -> sensitivity -> decide."""

    @pytest.mark.asyncio
    async def test_full_run(self, tmp_db):
        disc, moderator, pricing, mod, parts = start_method_discussion(
            tmp_db, "decision_matrix", n_participants=2,
            topic="Which datastore should the project standardise on?",
        )

        trace, result = await run_method(
            disc, moderator, tmp_db, pricing, mcda_content)

        # --- Flow ---------------------------------------------------
        assert result.get("method_complete") is True
        assert [phase for phase, _ in trace] == (
            ["options"] * 2 + ["criteria"] * 4 + ["score"] * 2
            + ["sensitivity"] + ["decide"]
        )
        assert all(name == "Mod" for phase, name in trace
                   if phase in ("sensitivity", "decide"))

        # --- Artifacts (all numbers computed in code) -----------------
        state = disc.method_state
        assert [o["id"] for o in state["options"]] == [1, 2, 3]
        weights = {c["id"]: sorted(c["weight_votes"].values())
                   for c in state["criteria"]}
        assert weights == {1: [2, 4], 2: [3], 3: [5]}

        artifact = state["decision_artifact"]
        assert artifact["recommended_option_id"] == 1
        assert artifact["ranking"][0]["option_id"] == 1
        assert artifact["ranking"][0]["weighted_total"] == 45.5
        assert [r["weighted_total"] for r in artifact["ranking"]] == [
            45.5, 26.5, 21.5]
        assert artifact["scorers"] == 2
        assert any("defaulted to the top-ranked option" in c
                   for c in artifact["caveats"]), (
            "free-text decide turn must record the default-caveat")

        # --- Conclusion prompt renders the real data -----------------
        prompt = get_method("decision_matrix").get_conclusion_prompt(disc)
        assert "Adopt PostgreSQL as the primary datastore" in prompt
        assert "45.5" in prompt

        # --- Persistence sanity ---------------------------------------
        persisted = db_method_state(tmp_db, disc.id)
        assert persisted["current_phase"] == state["current_phase"]
        assert (persisted["decision_artifact"]["recommended_option_id"]
                == artifact["recommended_option_id"])


# ---------------------------------------------------------------------------
# Double Crux — exercises the identify -> hunt loop-back (issue #22 path)
# ---------------------------------------------------------------------------

DC_POSITIONS = {
    "P1": "Remote-first work makes our engineering team more productive "
          "overall.",
    "P2": "Our engineering team loses more than it gains when it is "
          "fully remote.",
}

#: Round-1 cruxes deliberately do NOT overlap -> the moderator's first
#: identify pass returns verdict 'none' and loops back to hunting.
DC_HUNT_ROUND_1 = {
    "P1": ("```json\n"
           '{"cruxes": [{"claim": "Commute savings convert into extra '
           'focused work hours", "belief": 0.8, "why_pivotal": '
           '"Recovered time is the core of my case"}]}'
           "\n```"),
    "P2": ("```json\n"
           '{"cruxes": [{"claim": "Junior engineers ramp up slower '
           'without in-person mentoring", "belief": 0.9, "why_pivotal": '
           '"Mentoring quality drives my concern"}]}'
           "\n```"),
}

#: Round-2 cruxes share one pivotal claim (same polarity for both, so
#: the snapshot initial_beliefs are directly comparable) and are NOT
#: word-overlap similar to each author's own round-1 crux (same-entity
#: near-duplicates would be dropped).
DC_HUNT_ROUND_2 = {
    "P1": ("```json\n"
           '{"cruxes": [{"claim": "Distributed teams deliver features '
           'as fast as colocated teams", "belief": 0.75, "why_pivotal": '
           '"Delivery speed is what productivity means here"}]}'
           "\n```"),
    "P2": ("```json\n"
           '{"cruxes": [{"claim": "Feature delivery speed of distributed '
           'teams matches colocated teams", "belief": 0.25, '
           '"why_pivotal": "If speed holds up my objection collapses"}]}'
           "\n```"),
}

DC_IDENTIFY_NONE = (
    "```json\n"
    '{"verdict": "none", "reasoning": "The candidate cruxes address '
    'different mechanisms; no shared pivotal claim yet."}'
    "\n```"
)

DC_IDENTIFY_FACTUAL = (
    "```json\n"
    '{"verdict": "factual", "crux_ids": [3, 4], "claim": "Distributed '
    'teams deliver features as fast as colocated teams", "reasoning": '
    '"Both parties\' updated cruxes pivot on delivery speed."}'
    "\n```"
)

DC_TEST_CRUX = {
    "P1": "A 2024 multi-company study found delivery-speed parity for "
          "distributed teams [evidence: https://example.org/remote-study].",
    "P2": "In my direct experience, cross-team coordination overhead "
          "grows once a team is fully distributed.",
}

DC_RESOLUTIONS = {
    "P1": ("```json\n"
           '{"stance": "unchanged", "position": "Remote-first still nets '
           'out positive for our productivity", "crux_belief": 0.7, '
           '"reasoning": "The study supports delivery-speed parity"}'
           "\n```"),
    "P2": ("```json\n"
           '{"stance": "updated", "position": "I now think delivery '
           'speed is roughly comparable when distributed", '
           '"crux_belief": 0.6, "reasoning": "The cited study shifted '
           'my estimate upward"}'
           "\n```"),
}


def dc_content(disc: Discussion, speaker: Entity) -> str:
    """Scripted turn content for the Double Crux run.

    Reads the live ``crux_search_rounds`` to distinguish hunt round 1
    from round 2 and identify pass 1 from pass 2 — the loop-back is
    driven by real state, not a pre-scripted turn list.
    """
    state = disc.method_state
    phase = state["current_phase"]
    search_round = state.get("crux_search_rounds", 1)
    if phase == "positions":
        return DC_POSITIONS[speaker.name]
    if phase == "hunt_cruxes":
        return (DC_HUNT_ROUND_1 if search_round == 1
                else DC_HUNT_ROUND_2)[speaker.name]
    if phase == "identify_crux":
        return DC_IDENTIFY_NONE if search_round == 1 else DC_IDENTIFY_FACTUAL
    if phase == "test_crux":
        return DC_TEST_CRUX[speaker.name]
    if phase == "resolve":
        return DC_RESOLUTIONS[speaker.name]
    pytest.fail(f"unexpected Double Crux phase {phase!r} for {speaker.name}")


class TestDoubleCruxFlow:
    """Double Crux lifecycle incl. one identify->hunt loop iteration."""

    @pytest.mark.asyncio
    async def test_full_run_with_loop_back(self, tmp_db):
        disc, moderator, pricing, mod, parts = start_method_discussion(
            tmp_db, "double_crux", n_participants=2,
            topic="Should our engineering team stay remote-first?",
        )

        trace, result = await run_method(
            disc, moderator, tmp_db, pricing, dc_content)

        # --- Flow: the hunt/identify pair runs twice ------------------
        assert result.get("method_complete") is True
        assert [phase for phase, _ in trace] == (
            ["positions"] * 2
            + ["hunt_cruxes"] * 2 + ["identify_crux"]
            + ["hunt_cruxes"] * 2 + ["identify_crux"]
            + ["test_crux"] * 4 + ["resolve"] * 2
        )
        assert all(name == "Mod" for phase, name in trace
                   if phase == "identify_crux")

        # --- Loop + verdict state -------------------------------------
        state = disc.method_state
        assert state["crux_search_rounds"] == 2
        assert state["crux_verdict"] == "factual"
        assert state["shared_crux"]["claim"] == (
            "Distributed teams deliver features as fast as colocated teams")
        assert state["shared_crux"]["source_crux_ids"] == [3, 4]
        assert state["shared_crux"]["initial_beliefs"] == {
            "P1": 0.75, "P2": 0.25}

        # --- crux_map artifact (deterministic belief shifts) ----------
        crux_map = state["crux_map"]
        assert crux_map["verdict"] == "factual"
        assert crux_map["belief_shifts"]["P1"] == {
            "initial": 0.75, "final": 0.7, "shift": -0.05}
        assert crux_map["belief_shifts"]["P2"] == {
            "initial": 0.25, "final": 0.6, "shift": 0.35}
        assert crux_map["caveats"] == []

        # --- Evidence tracking on test_crux (issue #28) ---------------
        log = state["evidence_log"]
        assert len(log) == 4, "one entry per test_crux turn"
        assert [e["grounded"] for e in log] == [True, False, True, False]
        stored = [m.content for m in disc.messages]
        assert any("— sources:" in c and "example.org/remote-study" in c
                   for c in stored)
        assert any("reasoning-based contribution" in c for c in stored)

        # --- Conclusion prompt renders the real data ------------------
        prompt = get_method("double_crux").get_conclusion_prompt(disc)
        assert ("Distributed teams deliver features as fast as colocated "
                "teams") in prompt
        assert "Belief shifts on the crux (initial → final):" in prompt
        assert "0.25 → 0.6" in prompt

        # --- Persistence sanity ---------------------------------------
        persisted = db_method_state(tmp_db, disc.id)
        assert persisted["current_phase"] == state["current_phase"]
        assert persisted["crux_map"]["verdict"] == "factual"


# ---------------------------------------------------------------------------
# Tree of Thoughts — exercises one full score->prune->expand->score loop
# ---------------------------------------------------------------------------

#: Two thoughts per participant, pairwise overlap far below 0.7.
#: Raw order -> T1, T2 from P1; T3, T4 from P2.
TOT_THOUGHTS = {
    "P1": "1. Gamify the onboarding flow with progress rewards\n"
          "2. Rebuild documentation as interactive tutorials",
    "P2": "1. Assign every newcomer a dedicated peer mentor\n"
          "2. Automate environment setup with one-click scripts",
}

#: Identical payloads serve both scoring passes: in pass 2 only the
#: beam (T1, T3, T4) is eligible and the T2 entry is silently dropped,
#: while the unchanged values keep the ordered beam identical — the
#: convergence condition.  Mean composites: T3 14.5 > T1 12.0 > T4 11.0
#: > T2 8.5 -> beam [3, 1, 4].
TOT_SCORES = {
    "P1": ("My scores:\n```json\n"
           '{"scores": {'
           '"T1": {"feasibility": 4, "impact": 4, "risk": 2}, '
           '"T2": {"feasibility": 3, "impact": 3, "risk": 3}, '
           '"T3": {"feasibility": 5, "impact": 4, "risk": 1}, '
           '"T4": {"feasibility": 4, "impact": 3, "risk": 2}}}'
           "\n```"),
    "P2": ("My scores:\n```json\n"
           '{"scores": {'
           '"T1": {"feasibility": 4, "impact": 4, "risk": 2}, '
           '"T2": {"feasibility": 3, "impact": 2, "risk": 3}, '
           '"T3": {"feasibility": 5, "impact": 5, "risk": 1}, '
           '"T4": {"feasibility": 4, "impact": 3, "risk": 2}}}'
           "\n```"),
}

TOT_EXPANSIONS = {
    "P1": ("```json\n"
           '{"expansions": ['
           '{"thought_id": 3, "refinement": "Cap each mentor at two '
           'newcomers per quarter with weekly checkpoints", '
           '"obstacles": ["Mentor time budget"]}, '
           '{"thought_id": 1, "refinement": "Tie progress rewards to '
           'real environment-setup milestones"}]}'
           "\n```"),
    "P2": ("```json\n"
           '{"expansions": ['
           '{"thought_id": 4, "refinement": "Ship a bootstrap script '
           'exercised nightly in CI", '
           '"obstacles": ["Operating-system matrix drift"]}]}'
           "\n```"),
}


def tot_content(disc: Discussion, speaker: Entity) -> str:
    """Scripted turn content for the ToT run, keyed on the live phase."""
    phase = disc.method_state["current_phase"]
    if phase == "propose":
        return TOT_THOUGHTS[speaker.name]
    if phase == "score":
        return TOT_SCORES[speaker.name]
    if phase == "prune":
        return "The computed ranking and surviving beam are shown above."
    if phase == "expand":
        return TOT_EXPANSIONS[speaker.name]
    if phase == "synthesise":
        return "The exploration converged on the peer-mentoring approach."
    pytest.fail(f"unexpected ToT phase {phase!r} for {speaker.name}")


class TestTreeOfThoughtsFlow:
    """ToT lifecycle incl. one full expansion loop ending in convergence."""

    @pytest.mark.asyncio
    async def test_full_run_with_expansion_loop(self, tmp_db):
        disc, moderator, pricing, mod, parts = start_method_discussion(
            tmp_db, "tree_of_thoughts", n_participants=2,
            topic="How might we halve new-engineer onboarding time?",
        )

        trace, result = await run_method(
            disc, moderator, tmp_db, pricing, tot_content)

        # --- Flow: score/prune run twice, expand once ------------------
        assert result.get("method_complete") is True
        assert [phase for phase, _ in trace] == (
            ["propose"] * 2 + ["score"] * 2 + ["prune"]
            + ["expand"] * 2 + ["score"] * 2 + ["prune"]
            + ["synthesise"]
        )
        assert all(name == "Mod" for phase, name in trace
                   if phase in ("prune", "synthesise"))

        # --- Beam history: two passes, identical ordered beams --------
        state = disc.method_state
        assert [h["beam_ids"] for h in state["beam_history"]] == [
            [3, 1, 4], [3, 1, 4]]

        # --- tot_artifact (all numbers computed in code) ---------------
        artifact = state["tot_artifact"]
        assert artifact["stop_reason"] == "converged"
        assert artifact["converged"] is True
        assert artifact["depth"] == 2
        assert artifact["recommendation"] == {
            "id": 3,
            "text": "Assign every newcomer a dedicated peer mentor",
            "composite": 14.5,
        }
        assert len(artifact["final_beam"]) == 3
        assert artifact["caveats"] == []

        # --- Expansions recorded at depth 1 -----------------------------
        expansions = state["expansions"]
        assert len(expansions) == 3
        assert all(e["depth"] == 1 for e in expansions)
        assert {e["thought_id"] for e in expansions} == {1, 3, 4}

        # --- Conclusion prompt renders the real data --------------------
        prompt = get_method("tree_of_thoughts").get_conclusion_prompt(disc)
        assert "T3" in prompt
        assert "14.5" in prompt
        assert "beam stabilised" in prompt

        # --- Persistence sanity -----------------------------------------
        persisted = db_method_state(tmp_db, disc.id)
        assert persisted["current_phase"] == state["current_phase"]
        assert persisted["tot_artifact"]["stop_reason"] == "converged"
