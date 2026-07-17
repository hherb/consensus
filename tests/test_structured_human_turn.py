"""Tests for the human structured-turn recording path (#57, task 6).

Drives an all-human Double Crux discussion through the real production
pipeline (``submit_human_message`` -> ``complete_turn``) to the
``poll_belief`` phase, then exercises:

* ``submit_human_structured_message`` — the new dedicated entry point
  for validated structured payloads from a human participant.
* the safety net inside ``submit_human_message`` — a structured-phase
  free-text turn that records nothing must surface a visible error
  (golden rule 6), not silently drop.

Reuses the existing scripted Double Crux content (``dc_content``) and
driver infrastructure (``start_method_discussion`` / ``complete_turn``)
from the method-flow E2E suite rather than re-inventing them.
"""

import pytest

from consensus.app_discussion_flow import (
    complete_turn,
    submit_human_message,
    submit_human_structured_message,
)
from tests.flow_e2e_helpers import MAX_E2E_TURNS, start_method_discussion
from tests.test_method_flow_e2e import dc_content


async def drive_double_crux_to_poll(db):
    """Drive an all-human Double Crux discussion to the ``poll_belief`` phase.

    Uses the real production pipeline end to end: no state is
    pre-seeded.  Returns ``(discussion, moderator, pricing,
    current_speaker)`` where ``current_speaker`` is a human participant
    whose turn it is in the belief-poll phase.
    """
    disc, moderator, pricing, mod, parts = start_method_discussion(
        db, "double_crux", n_participants=2,
        topic="Should our engineering team stay remote-first?")
    for _ in range(MAX_E2E_TURNS):
        if disc.method_state.get("current_phase") == "poll_belief":
            return disc, moderator, pricing, disc.current_speaker
        speaker = disc.current_speaker
        submit_human_message(disc, db, speaker.id, dc_content(disc, speaker))
        await complete_turn(disc, moderator, db, pricing,
                            get_state_fn=lambda: {}, moderator_summary="ok")
    pytest.fail("never reached poll_belief")


class TestStructuredHumanTurn:
    """The dedicated structured-payload entry point for human turns."""

    @pytest.mark.asyncio
    async def test_valid_payload_records_belief(self, tmp_db):
        disc, moderator, pricing, entity = await drive_double_crux_to_poll(
            tmp_db)
        res = submit_human_structured_message(
            disc, tmp_db, entity.id, {"belief": 0.7, "reasoning": "study"})
        assert "error" not in res
        polls = disc.method_state["poll_beliefs"]
        assert any(p["entity_id"] == entity.id and p["belief"] == 0.7
                   for p in polls)

    @pytest.mark.asyncio
    async def test_invalid_payload_returns_error_and_records_nothing(
            self, tmp_db):
        disc, moderator, pricing, entity = await drive_double_crux_to_poll(
            tmp_db)
        before = list(disc.method_state["poll_beliefs"])
        res = submit_human_structured_message(
            disc, tmp_db, entity.id, {"belief": 5, "reasoning": "x"})
        assert "error" in res
        assert disc.method_state["poll_beliefs"] == before

    @pytest.mark.asyncio
    async def test_freetext_prose_in_structured_phase_errors_not_silent(
            self, tmp_db):
        disc, moderator, pricing, entity = await drive_double_crux_to_poll(
            tmp_db)
        res = submit_human_message(disc, tmp_db, entity.id,
                                   "I'm about 70% sure")
        assert "error" in res  # golden rule 6: visible, not a silent drop

    @pytest.mark.asyncio
    async def test_freetext_json_block_still_records(self, tmp_db):
        disc, moderator, pricing, entity = await drive_double_crux_to_poll(
            tmp_db)
        res = submit_human_message(
            disc, tmp_db, entity.id,
            '```json\n{"belief": 0.6, "reasoning": "ok"}\n```')
        assert "error" not in res
        assert any(p["entity_id"] == entity.id
                   for p in disc.method_state["poll_beliefs"])
