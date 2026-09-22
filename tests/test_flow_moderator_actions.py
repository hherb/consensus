"""Moderator interventions and turn reassignment (issue #73).

``mediate`` and the ``reassign_turn`` flow wrapper are live web routes
(``server.py``) that no test in the suite drove before the #61 split
surfaced the gap.  Both persist or return contracts the frontend depends
on, so they are pinned here: cost attribution, the moderator-role message,
the awaiting-human branch, and the reassignment result shape.

``complete_turn``'s AI-summary success branch is here for the same reason —
moderator-summary cost attribution was entirely unverified.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from consensus.app_discussion_flow import complete_turn, mediate, reassign_turn
from consensus.models import EntityType, MessageRole
from consensus.pricing import PricingCache

#: Per-token pricing for the stub model, chosen so prompt and completion
#: contributions stay distinguishable in the asserted total.
_PROMPT_COST = 0.001
_COMPLETION_COST = 0.002


def _insert_model(tmp_db, model_id: str) -> None:
    """Give ``model_id`` known per-token pricing so cost is assertable."""
    tmp_db.conn.execute(
        "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost,"
        " last_updated, input_modalities, context_length,"
        " supported_parameters) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (model_id, _PROMPT_COST, _COMPLETION_COST, time.time(),
         "text", 8192, "tools"),
    )
    tmp_db.conn.commit()


def _ai_response(content: str, model: str = "test-model"):
    """A minimal successful moderator response."""
    resp = MagicMock()
    resp.content = content
    resp.model = model
    resp.prompt_tokens = 100
    resp.completion_tokens = 50
    resp.total_tokens = 150
    resp.latency_ms = 42
    return resp


_EXPECTED_COST = 100 * _PROMPT_COST + 50 * _COMPLETION_COST

#: Prompt-template id the stubbed moderator reports, so the persisted
#: message can be checked to carry it through.
_PROMPT_ID = 7


class TestMediate:
    """``app.mediate`` → ``mediate`` — the moderator steps in mid-discussion."""

    def _moderator(self, content="Let us refocus.", model="test-model"):
        moderator = MagicMock()
        moderator.mediate = AsyncMock(return_value=_ai_response(content, model))
        moderator.prompt_id = MagicMock(return_value=_PROMPT_ID)
        return moderator

    @pytest.mark.asyncio
    async def test_returns_the_mediation_message(
        self, tmp_db, discussion_with_entities
    ):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        _insert_model(tmp_db, "test-model")
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        result = await mediate(
            disc, self._moderator(), tmp_db, pricing, "they are talking past "
            "each other")

        assert result["content"] == "Let us refocus."
        assert result["role"] == MessageRole.MODERATOR.value
        assert result["entity_id"] == disc.moderator_id

    @pytest.mark.asyncio
    async def test_appends_to_the_in_memory_transcript(
        self, tmp_db, discussion_with_entities
    ):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        _insert_model(tmp_db, "test-model")
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        await mediate(disc, self._moderator(), tmp_db, pricing)

        assert len(disc.messages) == 1
        assert disc.messages[0].role == MessageRole.MODERATOR

    @pytest.mark.asyncio
    async def test_persists_with_cost_and_prompt_id(
        self, tmp_db, discussion_with_entities
    ):
        """Mediation is a billable moderator call — it must be costed."""
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        _insert_model(tmp_db, "test-model")
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        result = await mediate(disc, self._moderator(), tmp_db, pricing)

        assert result["cost"] == pytest.approx(_EXPECTED_COST)
        rows = tmp_db.get_messages(disc.id)
        assert len(rows) == 1
        assert rows[0]["cost"] == pytest.approx(_EXPECTED_COST)
        assert rows[0]["prompt_id"] == _PROMPT_ID
        assert rows[0]["total_tokens"] == 150

    @pytest.mark.asyncio
    async def test_unpriced_model_is_not_a_failure(
        self, tmp_db, discussion_with_entities
    ):
        """An unknown model yields no cost, but still a mediation message."""
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        pricing.refresh = MagicMock(return_value=None)

        result = await mediate(
            disc, self._moderator(model="unpriced-model"), tmp_db, pricing)

        assert result["content"] == "Let us refocus."
        # Message.to_dict omits the key entirely when no cost is known.
        assert "cost" not in result

    @pytest.mark.asyncio
    async def test_human_moderator_awaits_input(
        self, tmp_db, discussion_with_entities
    ):
        """A human moderator is prompted by the UI instead of generating."""
        disc = discussion_with_entities
        disc.moderator.entity_type = EntityType.HUMAN
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = self._moderator()

        result = await mediate(disc, moderator, tmp_db, pricing)

        assert result == {"awaiting_human_moderator": True}
        moderator.mediate.assert_not_awaited()
        assert disc.messages == []

    @pytest.mark.asyncio
    async def test_no_moderator_returns_error(
        self, tmp_db, discussion_with_entities
    ):
        disc = discussion_with_entities
        disc.moderator_id = 0
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        result = await mediate(disc, self._moderator(), tmp_db, pricing)

        assert result == {"error": "No moderator"}


class TestReassignTurn:
    """The flow wrapper around ``Moderator.reassign_turn`` (server.py route)."""

    def test_success_returns_the_new_speaker(self, discussion_with_entities):
        disc = discussion_with_entities
        target = disc.entities[1]
        moderator = MagicMock()
        moderator.reassign_turn = MagicMock(return_value=target)

        result = reassign_turn(moderator, target.id)

        assert result["reassigned_to"]["id"] == target.id
        assert result["reassigned_to"]["name"] == target.name

    def test_unknown_entity_returns_error(self, discussion_with_entities):
        """A refused reassignment must report, not silently no-op."""
        moderator = MagicMock()
        moderator.reassign_turn = MagicMock(return_value=None)

        result = reassign_turn(moderator, 9999)

        assert result == {"error": "Could not reassign turn"}


class TestCompleteTurnSummaryCost:
    """The AI-moderator summary branch of ``complete_turn`` (issue #73)."""

    def _discussion(self, tmp_db, discussion_with_entities):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        _insert_model(tmp_db, "test-model")
        # A real participant contribution to summarise (not a pass, which
        # takes the no-AI-call branch).
        tmp_db.add_message(
            disc.id, disc.entities[0].id, "My contribution.", "participant",
            turn_number=disc.turn_number)
        from consensus.models import Message

        disc.messages.append(Message(
            entity_id=disc.entities[0].id, entity_name=disc.entities[0].name,
            content="My contribution.", role=MessageRole.PARTICIPANT))
        return disc

    def _moderator(self, disc, summary="Alice argued X."):
        moderator = MagicMock()
        moderator.peek_next_speaker = MagicMock(return_value=disc.entities[1])
        moderator.generate_summary = AsyncMock(
            return_value=_ai_response(summary))
        moderator.prompt_id = MagicMock(return_value=_PROMPT_ID)
        moderator.advance_turn = MagicMock(return_value=disc.entities[1])
        return moderator

    @pytest.mark.asyncio
    async def test_summary_is_costed_and_attributed(
        self, tmp_db, discussion_with_entities
    ):
        """Moderator summaries are billable calls and must carry their cost."""
        disc = self._discussion(tmp_db, discussion_with_entities)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        await complete_turn(
            disc, self._moderator(disc), tmp_db, pricing,
            get_state_fn=lambda: {})

        summaries = [r for r in tmp_db.get_messages(disc.id)
                     if r["role"] == "moderator"]
        assert len(summaries) == 1
        assert summaries[0]["content"] == "Alice argued X."
        assert summaries[0]["cost"] == pytest.approx(_EXPECTED_COST)
        assert summaries[0]["prompt_id"] == _PROMPT_ID
        assert summaries[0]["model_used"] == "test-model"

    @pytest.mark.asyncio
    async def test_summary_becomes_a_storyboard_entry(
        self, tmp_db, discussion_with_entities
    ):
        disc = self._discussion(tmp_db, discussion_with_entities)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        await complete_turn(
            disc, self._moderator(disc), tmp_db, pricing,
            get_state_fn=lambda: {})

        assert [e.summary for e in disc.storyboard] == ["Alice argued X."]


class TestCompleteTurnLimits:
    """``complete_turn``'s completion-side max-rounds and cost gates.

    The pre-flight gate in ``generate_ai_turn`` was tested; these returns,
    which are what actually stops a running discussion, were not (#73).
    """

    def _ready_discussion(self, tmp_db, discussion_with_entities):
        """A discussion whose turn is complete bar the limit checks."""
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        # A human moderator with a supplied summary keeps the AI out of it.
        disc.moderator.entity_type = EntityType.HUMAN
        return disc

    def _moderator(self, disc):
        moderator = MagicMock()
        moderator.advance_turn = MagicMock(return_value=disc.entities[0])
        return moderator

    @pytest.mark.asyncio
    async def test_max_rounds_reached_is_reported(
        self, tmp_db, discussion_with_entities
    ):
        disc = self._ready_discussion(tmp_db, discussion_with_entities)
        disc.max_rounds = 2
        # current_round is derived: (turn_number - 1) // len(turn_order) + 1,
        # so turn 5 of a two-entity rotation is round 3.
        disc.turn_number = 5

        result = await complete_turn(
            disc, self._moderator(disc), tmp_db,
            PricingCache(tmp_db.conn, tmp_db._lock),
            get_state_fn=lambda: {"snapshot": True},
            moderator_summary="A summary.")

        assert result["max_rounds_reached"] is True
        assert result["current_round"] == 3
        assert result["state"] == {"snapshot": True}

    @pytest.mark.asyncio
    async def test_within_max_rounds_reports_the_next_speaker(
        self, tmp_db, discussion_with_entities
    ):
        disc = self._ready_discussion(tmp_db, discussion_with_entities)
        disc.max_rounds = 5
        disc.turn_number = 5

        result = await complete_turn(
            disc, self._moderator(disc), tmp_db,
            PricingCache(tmp_db.conn, tmp_db._lock),
            get_state_fn=lambda: {},
            moderator_summary="A summary.")

        assert "max_rounds_reached" not in result
        assert result["next_speaker"] is not None

    @pytest.mark.asyncio
    async def test_cost_limit_reached_is_reported(
        self, tmp_db, discussion_with_entities
    ):
        disc = self._ready_discussion(tmp_db, discussion_with_entities)
        disc.cost_limit = 0.10
        from consensus.models import Message

        disc.messages.append(Message(
            entity_id=disc.entities[0].id, entity_name="Alice",
            content="expensive", role=MessageRole.PARTICIPANT, cost=0.25))

        result = await complete_turn(
            disc, self._moderator(disc), tmp_db,
            PricingCache(tmp_db.conn, tmp_db._lock),
            get_state_fn=lambda: {},
            moderator_summary="A summary.")

        assert result["cost_limit_reached"] is True
        assert result["total_cost"] == pytest.approx(0.25)
        assert result["cost_limit"] == pytest.approx(0.10)

    @pytest.mark.asyncio
    async def test_under_cost_limit_reports_the_next_speaker(
        self, tmp_db, discussion_with_entities
    ):
        disc = self._ready_discussion(tmp_db, discussion_with_entities)
        disc.cost_limit = 10.0
        from consensus.models import Message

        disc.messages.append(Message(
            entity_id=disc.entities[0].id, entity_name="Alice",
            content="cheap", role=MessageRole.PARTICIPANT, cost=0.01))

        result = await complete_turn(
            disc, self._moderator(disc), tmp_db,
            PricingCache(tmp_db.conn, tmp_db._lock),
            get_state_fn=lambda: {},
            moderator_summary="A summary.")

        assert "cost_limit_reached" not in result
        assert result["next_speaker"] is not None


class TestTurnGuards:
    """Guard clauses and the pass path — the last uncovered flow branches.

    Each is a user-visible contract the frontend branches on, so none of
    them should be reachable only in production.
    """

    def _moderator(self, disc):
        moderator = MagicMock()
        moderator.advance_turn = MagicMock(return_value=disc.entities[0])
        return moderator

    @pytest.mark.asyncio
    async def test_concluded_discussion_refuses_a_turn(
        self, tmp_db, discussion_with_entities
    ):
        from consensus.app_discussion_flow import generate_ai_turn

        disc = discussion_with_entities
        disc.status = "concluded"
        disc.is_active = False

        result = await generate_ai_turn(
            disc, self._moderator(disc), tmp_db,
            PricingCache(tmp_db.conn, tmp_db._lock))

        assert result == {"error": "Discussion is not active"}

    @pytest.mark.asyncio
    async def test_empty_turn_order_has_no_speaker(
        self, tmp_db, discussion_with_entities
    ):
        from consensus.app_discussion_flow import generate_ai_turn

        disc = discussion_with_entities
        disc.turn_order = []

        result = await generate_ai_turn(
            disc, self._moderator(disc), tmp_db,
            PricingCache(tmp_db.conn, tmp_db._lock))

        assert result == {"error": "No current speaker"}

    @pytest.mark.asyncio
    async def test_human_speaker_is_not_generated_for(
        self, tmp_db, discussion_with_entities
    ):
        """The human's turn waits for input rather than being invented."""
        from consensus.app_discussion_flow import generate_ai_turn

        disc = discussion_with_entities
        disc.current_turn_index = 1  # the human entity

        result = await generate_ai_turn(
            disc, self._moderator(disc), tmp_db,
            PricingCache(tmp_db.conn, tmp_db._lock))

        assert "human - waiting for input" in result["error"]

    @pytest.mark.asyncio
    async def test_ai_pass_is_flagged_and_formatted(
        self, tmp_db, discussion_with_entities
    ):
        from consensus.app_discussion_flow import generate_ai_turn

        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        speaker = disc.current_speaker
        resp = _ai_response("[PASS]")
        resp.tool_calls = []
        resp.warning = None
        resp.structured_output = None
        resp.finish_reason = "stop"
        moderator = MagicMock()
        moderator.generate_turn = AsyncMock(return_value=resp)
        moderator.prompt_id = MagicMock(return_value=_PROMPT_ID)

        result = await generate_ai_turn(
            disc, moderator, tmp_db, PricingCache(tmp_db.conn, tmp_db._lock))

        assert result["passed"] is True
        assert result["content"] == f"*{speaker.name} passed this round.*"

    @pytest.mark.asyncio
    async def test_pass_is_summarised_without_an_ai_call(
        self, tmp_db, discussion_with_entities
    ):
        """A pass needs no synthesis — and must not be billed for one."""
        from consensus.models import Message

        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        speaker = disc.current_speaker
        disc.messages.append(Message(
            entity_id=speaker.id, entity_name=speaker.name,
            content=f"*{speaker.name} passed this round.*",
            role=MessageRole.PARTICIPANT))
        moderator = self._moderator(disc)
        moderator.generate_summary = AsyncMock()

        await complete_turn(
            disc, moderator, tmp_db, PricingCache(tmp_db.conn, tmp_db._lock),
            get_state_fn=lambda: {})

        moderator.generate_summary.assert_not_awaited()
        rows = [r for r in tmp_db.get_messages(disc.id)
                if r["role"] == "moderator"]
        assert rows[0]["content"] == f"{speaker.name} passed this round."

    @pytest.mark.asyncio
    async def test_no_moderator_cannot_complete_a_turn(
        self, tmp_db, discussion_with_entities
    ):
        disc = discussion_with_entities
        disc.moderator_id = 0

        result = await complete_turn(
            disc, self._moderator(disc), tmp_db,
            PricingCache(tmp_db.conn, tmp_db._lock), get_state_fn=lambda: {})

        assert result == {"error": "No moderator designated"}

    @pytest.mark.asyncio
    async def test_human_moderator_without_summary_awaits_one(
        self, tmp_db, discussion_with_entities
    ):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.moderator.entity_type = EntityType.HUMAN

        result = await complete_turn(
            disc, self._moderator(disc), tmp_db,
            PricingCache(tmp_db.conn, tmp_db._lock),
            get_state_fn=lambda: {"snapshot": True})

        assert result == {"awaiting_moderator_summary": True,
                          "state": {"snapshot": True}}
