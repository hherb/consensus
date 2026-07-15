"""Flow-level wiring for evidence-tracked phases (#28)."""
from unittest.mock import AsyncMock

import pytest

from consensus.ai_client import AIResponse
from consensus.app_discussion_flow import generate_ai_turn, submit_human_message
from consensus.methods.base import Phase, ProcessedResponse
from consensus.models import Discussion, Entity
from consensus.moderator import Moderator
from consensus.pricing import PricingCache
from consensus.tools import ToolCallRecord


class _FakePhaseMethod:
    """Minimal active method exposing a track_evidence phase."""

    def __init__(self, track):
        self._phase = Phase(name="test_crux", display_name="Crux",
                            track_evidence=track)

    def current_phase(self, discussion):
        return self._phase

    def process_response(self, content, entity, discussion):
        return ProcessedResponse(display_content=content)

    def process_structured_response(self, payload, entity, discussion):
        return ProcessedResponse(display_content=str(payload))


def _human_turn_discussion(db, phase="test_crux"):
    """An active discussion whose current speaker is a human."""
    eid = db.add_entity("Alice", "human", "#123456")
    alice = Entity.from_db_row(db.get_entity(eid))
    disc = Discussion(
        topic="t", entities=[alice],
        turn_order=[alice.id], base_turn_order=[alice.id],
        current_turn_index=0, turn_number=1,
        is_active=True, status="active", discussion_method="double_crux",
    )
    disc.id = db.create_discussion(disc.topic, alice.id)
    disc.method_state = {"current_phase": phase}
    return disc, alice


def test_human_turn_in_tracked_phase_is_logged(tmp_db, monkeypatch):
    disc, alice = _human_turn_discussion(tmp_db, phase="test_crux")
    monkeypatch.setattr(
        "consensus.app_discussion_flow.get_active_method",
        lambda d: _FakePhaseMethod(track=True))
    submit_human_message(disc, tmp_db, alice.id,
                         "It holds, see https://a.example/x")
    log = disc.method_state["evidence_log"]
    assert log and log[0]["grounded"] is True
    assert log[0]["sources"][0]["url"] == "https://a.example/x"


def test_human_turn_untracked_phase_no_log(tmp_db, monkeypatch):
    disc, alice = _human_turn_discussion(tmp_db, phase="positions")
    monkeypatch.setattr(
        "consensus.app_discussion_flow.get_active_method",
        lambda d: _FakePhaseMethod(track=False))
    submit_human_message(disc, tmp_db, alice.id, "Just my opinion.")
    assert "evidence_log" not in disc.method_state


@pytest.mark.asyncio
async def test_ai_turn_tool_call_is_logged(tmp_db, monkeypatch,
                                           discussion_with_entities):
    """A tracked-phase AI turn whose AIResponse carries an evidence tool
    call records a grounded entry with the AI's id, turn, and source.

    Exercises the real ``generate_ai_turn`` — only ``generate_turn``
    (network) and ``get_active_method`` are stubbed.
    """
    disc = discussion_with_entities  # current speaker is the AI entity
    ai = disc.current_speaker
    disc.method_state = {"current_phase": "test_crux"}
    disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)

    monkeypatch.setattr(
        "consensus.app_discussion_flow.get_active_method",
        lambda d: _FakePhaseMethod(track=True))

    moderator = Moderator(disc, tmp_db)
    moderator.generate_turn = AsyncMock(return_value=AIResponse(
        content="Per the document.", model="test-model",
        prompt_tokens=1, completion_tokens=1, total_tokens=2,
        tool_calls=[ToolCallRecord(
            "doc_ask", {"document_id": 7, "question": "q"},
            result="...", is_error=False)]))
    pricing = PricingCache(tmp_db.conn, tmp_db._lock)

    await generate_ai_turn(disc, moderator, tmp_db, pricing,
                           key_resolver=lambda *a, **k: "")

    entry = disc.method_state["evidence_log"][0]
    assert entry["grounded"] is True
    assert entry["entity_id"] == ai.id
    assert entry["turn"] == disc.turn_number
    assert entry["sources"][0]["document_id"] == 7


class TestGetStateTrackEvidenceFlag:
    """`get_state` exposes whether the active phase tracks evidence, so the
    frontend can gate the "Attach evidence" button to tracked phases only."""

    def _app(self, tmp_path):
        from consensus.app import ConsensusApp
        return ConsensusApp(db_path=str(tmp_path / "te_state.db"))

    def test_true_in_double_crux_test_crux_phase(self, tmp_path):
        app = self._app(tmp_path)
        app.discussion.discussion_method = "double_crux"
        app.discussion.method_state = {"current_phase": "test_crux"}
        assert app.get_state()["track_evidence_phase"] is True

    def test_false_in_untracked_phase(self, tmp_path):
        app = self._app(tmp_path)
        app.discussion.discussion_method = "double_crux"
        app.discussion.method_state = {"current_phase": "positions"}
        assert app.get_state()["track_evidence_phase"] is False

    def test_false_for_open_discussion(self, tmp_path):
        app = self._app(tmp_path)
        app.discussion.discussion_method = "open_discussion"
        assert app.get_state()["track_evidence_phase"] is False
