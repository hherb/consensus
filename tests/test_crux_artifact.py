"""Tests for the Double Crux artifact and formatting layer (issue #27).

Covers ``build_crux_map`` (verdict, shared crux, belief shifts, caveats,
evidence summary) and the display formatters.  The validator/recorder/
extractor layer is covered in ``test_crux_helpers.py``; this file split
off to keep both under the ~500-line guideline.
"""

from consensus.methods.phases._crux_helpers import (
    VERDICT_FACTUAL,
    VERDICT_NONE,
    build_crux_map,
    format_belief_shifts,
    format_cruxes,
    format_positions,
    format_resolutions,
    format_shared_crux,
    record_crux_selection,
    record_cruxes,
    record_resolution,
)
from consensus.models import Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


CLAIM_A = "Remote work reduces measured team productivity"


def _crux(claim: str = CLAIM_A, belief: float = 0.8) -> dict:
    return {"claim": claim, "belief": belief,
            "why_pivotal": "My whole position rests on this."}


class TestBuildCruxMap:
    def _full_state(self) -> dict:
        state: dict = {"positions": {"Alice": "Remote-first",
                                     "Bob": "Office-first"}}
        record_cruxes(state, _entity(1, "Alice"), [_crux(CLAIM_A, 0.9)])
        record_cruxes(state, _entity(2, "Bob"),
                      [_crux(CLAIM_A + " overall", 0.2)])
        record_crux_selection(state, {
            "verdict": "factual", "crux_ids": [1, 2], "claim": CLAIM_A,
            "reasoning": "shared"})
        state["shared_crux"]["initial_beliefs"] = {"Alice": 0.9, "Bob": 0.2}
        record_resolution(state, _entity(1, "Alice"), {
            "stance": "updated", "position": "Hybrid works best",
            "crux_belief": 0.55, "reasoning": "trials"})
        record_resolution(state, _entity(2, "Bob"), {
            "stance": "unchanged", "position": "Office-first still",
            "crux_belief": 0.25, "reasoning": "unmoved"})
        return state

    def test_factual_map_with_shifts(self):
        crux_map = build_crux_map(self._full_state())
        assert crux_map["verdict"] == VERDICT_FACTUAL
        assert crux_map["shared_crux"]["claim"] == CLAIM_A
        shifts = crux_map["belief_shifts"]
        assert shifts["Alice"] == {"initial": 0.9, "final": 0.55,
                                   "shift": -0.35}
        assert shifts["Bob"] == {"initial": 0.2, "final": 0.25,
                                 "shift": 0.05}
        assert crux_map["caveats"] == []

    def test_final_only_shift_has_no_delta(self):
        state = self._full_state()
        state["shared_crux"]["initial_beliefs"].pop("Bob")
        shifts = build_crux_map(state)["belief_shifts"]
        assert shifts["Bob"] == {"initial": None, "final": 0.25,
                                 "shift": None}

    def test_none_verdict_carries_caveat(self):
        state: dict = {"positions": {}, "cruxes": [], "resolutions": [],
                       "crux_verdict": VERDICT_NONE, "shared_crux": {}}
        crux_map = build_crux_map(state)
        assert crux_map["verdict"] == VERDICT_NONE
        assert any("no shared crux" in c.lower()
                   for c in crux_map["caveats"])

    def test_zero_resolutions_carries_caveat(self):
        state = self._full_state()
        state["resolutions"] = []
        crux_map = build_crux_map(state)
        assert any("resolution" in c.lower() for c in crux_map["caveats"])

    def test_factual_without_shifts_carries_caveat(self):
        state = self._full_state()
        state["shared_crux"]["initial_beliefs"] = {}
        for r in state["resolutions"]:
            r["crux_belief"] = None
        crux_map = build_crux_map(state)
        assert any("belief" in c.lower() for c in crux_map["caveats"])

    def test_build_crux_map_includes_evidence_summary(self):
        state = {
            "crux_verdict": "factual",
            "evidence_log": [
                {"entity_name": "Alice", "grounded": True,
                 "sources": [{"type": "document", "document_id": 3}]},
                {"entity_name": "Bob", "grounded": False, "sources": []},
            ],
        }
        artifact = build_crux_map(state)
        assert artifact["evidence"]["counts"] == {
            "grounded": 1, "reasoning_based": 1}


class TestFormatters:
    def _state(self) -> dict:
        state: dict = {"positions": {"Alice": "Remote-first"}}
        record_cruxes(state, _entity(1, "Alice"), [_crux(CLAIM_A, 0.9)])
        record_crux_selection(state, {
            "verdict": "factual", "crux_ids": [1], "claim": CLAIM_A,
            "reasoning": "r"})
        state["shared_crux"]["initial_beliefs"] = {"Alice": 0.9}
        record_resolution(state, _entity(1, "Alice"), {
            "stance": "updated", "position": "Hybrid works best",
            "crux_belief": 0.55, "reasoning": "trials"})
        return state

    def test_format_positions(self):
        text = format_positions(self._state())
        assert "Alice" in text and "Remote-first" in text
        assert "no positions" in format_positions({}).lower()

    def test_format_cruxes(self):
        text = format_cruxes(self._state())
        assert "Crux 1" in text and CLAIM_A in text and "0.9" in text
        assert "no cruxes" in format_cruxes({}).lower()

    def test_format_shared_crux(self):
        assert CLAIM_A in format_shared_crux(self._state())
        assert "no shared crux" in format_shared_crux({}).lower()

    def test_format_belief_shifts(self):
        text = format_belief_shifts(self._state())
        assert "Alice" in text and "0.9" in text and "0.55" in text

    def test_format_resolutions(self):
        text = format_resolutions(self._state())
        assert "Alice" in text and "updated" in text
        assert "no resolutions" in format_resolutions({}).lower()
