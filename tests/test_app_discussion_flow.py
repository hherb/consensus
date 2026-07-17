"""Tests for consensus.app_discussion_flow — active discussion operations."""

import logging
import time

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from consensus.app_discussion_flow import (
    calculate_discussion_cost,
    complete_turn,
    generate_ai_turn,
    is_pass,
    submit_human_message,
    submit_moderator_message,
    switch_discussion_method,
)
from consensus.models import Discussion, Entity, EntityType, Message, MessageRole
from consensus.pricing import PricingCache


def _insert_model(tmp_db, model_id: str, supported: str) -> None:
    """Insert a model_pricing row with a given ``supported_parameters`` value.

    Mirrors the helper in tests/test_structured_setup_check.py.
    """
    tmp_db.conn.execute(
        "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost,"
        " last_updated, input_modalities, context_length,"
        " supported_parameters) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (model_id, 0.0, 0.0, time.time(), "text", 8192, supported),
    )
    tmp_db.conn.commit()


class TestIsPass:
    """Tests for the is_pass helper function."""

    def test_pass_bracket(self):
        """Bracket notation [PASS] is recognised."""
        assert is_pass("[PASS]") is True

    def test_pass_plain(self):
        """Plain PASS is recognised."""
        assert is_pass("PASS") is True

    def test_pass_formatted(self):
        """Formatted '*Name passed this round.*' is recognised."""
        assert is_pass("*Alice passed this round.*") is True

    def test_not_pass(self):
        """Regular discussion content is not a pass."""
        assert is_pass("I think we should consider...") is False

    def test_pass_with_whitespace(self):
        """Leading/trailing whitespace does not prevent detection."""
        assert is_pass("  [PASS]  ") is True

    def test_pass_with_markdown(self):
        """Bold markdown around [PASS] is recognised."""
        assert is_pass("**[PASS]**") is True

    def test_phrase_mid_sentence_is_not_pass(self):
        """The pass phrase inside a longer contribution is not a pass."""
        assert is_pass(
            "I disagree that everyone passed this round. Here is my view: ..."
        ) is False


class TestSubmitHumanMessage:
    """Tests for submit_human_message."""

    def test_submit_message(self, tmp_db, discussion_with_entities):
        """A human whose turn it is can submit a message."""
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        # Current speaker is at index 0 (AI entity) — switch to human (index 1)
        disc.current_turn_index = 1
        speaker = disc.current_speaker
        result = submit_human_message(disc, tmp_db, speaker.id, "Hello world")
        assert "error" not in result
        assert result["content"] == "Hello world"

    def test_wrong_turn_returns_error(self, tmp_db, discussion_with_entities):
        """Submitting when it is not the entity's turn returns an error."""
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        # Current speaker is at index 0 — pick the other entity
        other = [e for e in disc.entities if e.id != disc.current_speaker.id][0]
        result = submit_human_message(disc, tmp_db, other.id, "Hello")
        assert "error" in result

    def test_entity_not_found(self, tmp_db, discussion_with_entities):
        """Submitting for a non-existent entity returns an error."""
        disc = discussion_with_entities
        result = submit_human_message(disc, tmp_db, 9999, "Hello")
        assert "error" in result


class TestSubmitModeratorMessage:
    """Tests for submit_moderator_message."""

    def test_submit_moderator_message(self, tmp_db, discussion_with_entities):
        """The moderator can submit a message."""
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        result = submit_moderator_message(disc, tmp_db, "Moderator says hello")
        assert "error" not in result

    def test_no_moderator_returns_error(self, tmp_db):
        """Submitting a moderator message with no moderator returns an error."""
        disc = Discussion()
        result = submit_moderator_message(disc, tmp_db, "Hello")
        assert "error" in result


class TestCalculateDiscussionCost:
    """Tests for the calculate_discussion_cost helper."""

    def test_empty_messages(self):
        """Discussion with no messages returns 0."""
        disc = Discussion()
        assert calculate_discussion_cost(disc) == 0.0

    def test_sums_costs(self):
        """Sums cost from all messages."""
        disc = Discussion(messages=[
            Message(entity_id=1, entity_name="A", content="x",
                    role=MessageRole.PARTICIPANT, cost=0.05),
            Message(entity_id=2, entity_name="B", content="y",
                    role=MessageRole.PARTICIPANT, cost=0.10),
        ])
        assert abs(calculate_discussion_cost(disc) - 0.15) < 1e-9

    def test_none_costs_treated_as_zero(self):
        """Messages with cost=None (human messages) are treated as $0."""
        disc = Discussion(messages=[
            Message(entity_id=1, entity_name="A", content="x",
                    role=MessageRole.PARTICIPANT, cost=None),
            Message(entity_id=2, entity_name="B", content="y",
                    role=MessageRole.PARTICIPANT, cost=0.10),
        ])
        assert abs(calculate_discussion_cost(disc) - 0.10) < 1e-9


class TestCostLimitEnforcement:
    """Tests for cost limit checks in generate_ai_turn."""

    @pytest.mark.asyncio
    async def test_preflight_blocks_when_over_limit(
        self, tmp_db, discussion_with_entities
    ):
        """generate_ai_turn returns cost_limit_reached when budget exceeded."""
        disc = discussion_with_entities
        disc.cost_limit = 0.50
        # Add messages that exceed the limit
        disc.messages = [
            Message(entity_id=1, entity_name="A", content="x",
                    role=MessageRole.PARTICIPANT, cost=0.30),
            Message(entity_id=2, entity_name="B", content="y",
                    role=MessageRole.PARTICIPANT, cost=0.25),
        ]
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = MagicMock()
        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)
        assert result["cost_limit_reached"] is True
        assert result["total_cost"] >= 0.50

    @pytest.mark.asyncio
    async def test_preflight_allows_when_under_limit(
        self, tmp_db, discussion_with_entities
    ):
        """generate_ai_turn proceeds normally when under budget."""
        disc = discussion_with_entities
        disc.cost_limit = 10.0
        disc.messages = [
            Message(entity_id=1, entity_name="A", content="x",
                    role=MessageRole.PARTICIPANT, cost=0.01),
        ]
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = MagicMock()
        # Mock generate_turn to return a response
        mock_resp = MagicMock()
        mock_resp.content = "AI response"
        mock_resp.model = "test-model"
        mock_resp.prompt_tokens = 10
        mock_resp.completion_tokens = 20
        mock_resp.total_tokens = 30
        mock_resp.latency_ms = 100
        mock_resp.tool_calls = []
        mock_resp.warning = None
        moderator.generate_turn = AsyncMock(return_value=mock_resp)
        moderator.prompt_id = MagicMock(return_value=None)

        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)
        assert "cost_limit_reached" not in result
        assert "error" not in result

    @pytest.mark.asyncio
    async def test_structured_payload_routed(
        self, monkeypatch, tmp_db, discussion_with_entities
    ):
        """generate_ai_turn routes structured payloads to
        process_structured_response (issue #23)."""
        import consensus.methods as methods_registry
        from consensus.ai_client import AIResponse
        from consensus.methods.base import (
            DiscussionMethod, Phase, ProcessedResponse,
        )
        from consensus.methods.phase_handler import PhaseHandler
        from consensus.moderator import Moderator

        calls = {}

        class _Handler(PhaseHandler):
            phase = Phase("p", "P")
            requires_structured_output = True

            def get_system_prompt(self, entity, discussion):
                return ""

            def get_turn_prompt(self, entity, discussion):
                return ""

            def process_structured_response(self, payload, entity,
                                            discussion):
                calls["payload"] = payload
                return ProcessedResponse(display_content="structured!")

            def process_response(self, content, entity, discussion):
                calls["free_text"] = content
                return ProcessedResponse(display_content=content)

        class _M(DiscussionMethod):
            name = "_test_routing"
            display_name = "Routing"
            description = "test"
            phase_handlers = (_Handler(),)

        disc = discussion_with_entities
        disc.discussion_method = "_test_routing"
        disc.method_state = _M().init_state(disc)
        monkeypatch.setitem(methods_registry._METHODS,
                            "_test_routing", _M)
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did

        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = Moderator(disc, tmp_db)
        moderator.generate_turn = AsyncMock(return_value=AIResponse(
            content="", structured_output={"estimate": 3}))
        moderator.prompt_id = MagicMock(return_value=None)

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)

        assert calls["payload"] == {"estimate": 3}
        assert "free_text" not in calls
        assert result["content"] == "structured!"

    @pytest.mark.asyncio
    async def test_no_limit_when_zero(
        self, tmp_db, discussion_with_entities
    ):
        """cost_limit=0 means unlimited — no blocking even with high costs."""
        disc = discussion_with_entities
        disc.cost_limit = 0.0
        disc.messages = [
            Message(entity_id=1, entity_name="A", content="x",
                    role=MessageRole.PARTICIPANT, cost=999.99),
        ]
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = MagicMock()
        mock_resp = MagicMock()
        mock_resp.content = "AI response"
        mock_resp.model = "test-model"
        mock_resp.prompt_tokens = 10
        mock_resp.completion_tokens = 20
        mock_resp.total_tokens = 30
        mock_resp.latency_ms = 100
        mock_resp.tool_calls = []
        mock_resp.warning = None
        moderator.generate_turn = AsyncMock(return_value=mock_resp)
        moderator.prompt_id = MagicMock(return_value=None)

        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)
        assert "cost_limit_reached" not in result


class TestStructuredOutputFlowRouting:
    """End-to-end routing of structured turns through generate_ai_turn
    (issue #23 review follow-ups)."""

    def _install_method(self, monkeypatch, disc, name):
        """Register a hybrid test method and return its call recorder.

        ``name`` must be unique per test: get_method() caches instances
        in _INSTANCES, which monkeypatch does not restore.
        """
        import consensus.methods as methods_registry
        from consensus.methods.base import (
            DiscussionMethod, Phase, ProcessedResponse,
        )
        from consensus.methods.phase_handler import PhaseHandler

        calls = {}

        class _Handler(PhaseHandler):
            phase = Phase("p", "P")
            requires_structured_output = True

            def get_system_prompt(self, entity, discussion):
                return ""

            def get_turn_prompt(self, entity, discussion):
                return ""

            def process_structured_response(self, payload, entity,
                                            discussion):
                calls["payload"] = payload
                return ProcessedResponse(display_content="structured!")

            def process_response(self, content, entity, discussion):
                calls["free_text"] = content
                return ProcessedResponse(display_content=content)

        class _M(DiscussionMethod):
            display_name = "Flow Routing"
            description = "test"
            phase_handlers = (_Handler(),)

        _M.name = name
        disc.discussion_method = name
        disc.method_state = _M().init_state(disc)
        monkeypatch.setitem(methods_registry._METHODS, name, _M)
        return calls

    def _moderator(self, disc, tmp_db, resp=None, error=None):
        from consensus.moderator import Moderator

        moderator = Moderator(disc, tmp_db)
        if error is not None:
            moderator.generate_turn = AsyncMock(side_effect=error)
        else:
            moderator.generate_turn = AsyncMock(return_value=resp)
        moderator.prompt_id = MagicMock(return_value=None)
        return moderator

    @pytest.mark.asyncio
    async def test_pass_content_beside_payload_is_not_a_pass(
        self, monkeypatch, tmp_db, discussion_with_entities
    ):
        """'PASS' as side content next to a validated tool call must not
        discard the structured payload."""
        from consensus.ai_client import AIResponse

        disc = discussion_with_entities
        calls = self._install_method(monkeypatch, disc,
                                     "_test_flow_pass")
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)

        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = self._moderator(disc, tmp_db, resp=AIResponse(
            content="PASS", structured_output={"estimate": 3}))

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)

        assert calls["payload"] == {"estimate": 3}
        assert "passed" not in result
        assert result["content"] == "structured!"

    @pytest.mark.asyncio
    async def test_fallback_free_text_routed_with_warning(
        self, monkeypatch, tmp_db, discussion_with_entities
    ):
        """Exhausted retries (structured_output=None + warning) fall back
        to process_response and surface the warning to the user."""
        from consensus.ai_client import AIResponse

        disc = discussion_with_entities
        calls = self._install_method(monkeypatch, disc,
                                     "_test_flow_fallback")
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)

        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = self._moderator(disc, tmp_db, resp=AIResponse(
            content="prose only", structured_output=None,
            warning="could not produce a valid output"))

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)

        assert calls["free_text"] == "prose only"
        assert "payload" not in calls
        assert result["warning"] == "could not produce a valid output"

    @pytest.mark.asyncio
    async def test_structured_output_error_skips_participant(
        self, monkeypatch, tmp_db, discussion_with_entities
    ):
        """A runtime StructuredOutputError posts a visible skip notice
        with the actionable message in the error field."""
        from consensus.structured_output import StructuredOutputError

        disc = discussion_with_entities
        self._install_method(monkeypatch, disc, "_test_flow_error")
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)

        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = self._moderator(disc, tmp_db, error=StructuredOutputError(
            "model-x rejected the forced tool call"))

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)

        assert result["skipped"] is True
        assert "model-x rejected the forced tool call" in result["error"]


class TestSwitchDiscussionMethodToolCapability:
    """Runtime method switching must honour the same tool-capability gate
    as discussion setup (issue #23) — Triage's handoff to a chosen method
    must not bypass it."""

    def _prepare(self, disc, tmp_db):
        """Give the discussion an id and a starting method/state so a
        successful switch would have something to mutate."""
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.discussion_method = "triage"
        disc.method_state = {"chosen_method": "delphi"}
        disc.messages = []
        return disc

    def test_blocks_structured_target_with_non_tool_capable_model(
        self, tmp_db, discussion_with_entities, monkeypatch
    ):
        """Switching into delphi (structured) with a model known to lack
        tool support returns an error and leaves method/state/messages
        untouched."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc = self._prepare(discussion_with_entities, tmp_db)
        original_state = dict(disc.method_state)

        result = switch_discussion_method(disc, tmp_db, "delphi")

        assert "error" in result
        assert "test-model" in result["error"]
        assert disc.discussion_method == "triage"
        assert disc.method_state == original_state
        assert disc.messages == []

    def test_allows_unknown_capability(
        self, tmp_db, discussion_with_entities, monkeypatch
    ):
        """No pricing data (e.g. a local model) still allows the switch."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        disc = self._prepare(discussion_with_entities, tmp_db)

        result = switch_discussion_method(disc, tmp_db, "delphi")

        assert "error" not in result
        assert disc.discussion_method == "delphi"

    def test_allows_unstructured_target_with_non_tool_capable_model(
        self, tmp_db, discussion_with_entities, monkeypatch
    ):
        """A target method with no structured phases is never blocked,
        even with a known non-tool-capable model."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc = self._prepare(discussion_with_entities, tmp_db)

        result = switch_discussion_method(disc, tmp_db, "open_discussion")

        assert "error" not in result
        assert disc.discussion_method == "open_discussion"

    def test_blocked_switch_reports_blocked_entities(
        self, tmp_db, discussion_with_entities, monkeypatch
    ):
        """The gate error carries the structured offender list the
        recovery dialog needs (spec 2026-07-17)."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc = self._prepare(discussion_with_entities, tmp_db)

        result = switch_discussion_method(disc, tmp_db, "delphi")

        assert result["blocked_entities"] == [{
            "entity_id": disc.entities[0].id,
            "name": "Alice",
            "model": "test-model",
        }]


class TestCompleteTurnBlockedTriageSwitch:
    """A triage switch blocked by the tool-capability gate must be loud:
    logged, posted into the transcript as a system message, and surfaced
    to the frontend via ``switch_error`` — never silently swallowed into
    a bare ``method_complete`` (golden rule 6, issue #23)."""

    def _make_triage_pipeline(self, tmp_db, sample_provider):
        """Active triage discussion at the confirm phase, driven by a
        human moderator, with one AI panel member (model 'test-model').

        Returns (discussion, moderator_entity, Moderator, PricingCache).
        Mirrors the pipeline pattern in tests/test_belief_diffusion_abort.py.
        """
        from consensus.methods import get_method
        from consensus.moderator import Moderator

        mod_id = tmp_db.add_entity("Mod", "human", "#123456")
        ai_id = tmp_db.add_entity(
            "Alice", "ai", "#ff0000", sample_provider,
            "test-model", 0.5, 512, "You are Alice.",
        )
        mod = Entity.from_db_row(tmp_db.get_entity(mod_id))
        ai = Entity.from_db_row(tmp_db.get_entity(ai_id))
        disc = Discussion(
            topic="Which method fits?",
            entities=[mod, ai],
            moderator_id=mod.id,
            turn_order=[mod.id],
            base_turn_order=[ai.id],
            current_turn_index=0,
            turn_number=5,
            is_active=True,
            status="active",
            discussion_method="triage",
        )
        disc.id = tmp_db.create_discussion(disc.topic, mod.id)
        disc.method_state = get_method("triage").init_state(disc)
        disc.method_state["current_phase"] = "confirm"
        # The confirm handler re-parses the moderator's message; the
        # recommended_method fallback keeps the choice deterministic.
        disc.method_state["chosen_method"] = "delphi"
        disc.method_state["recommended_method"] = "delphi"
        moderator = Moderator(disc, tmp_db)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        return disc, mod, moderator, pricing

    async def _drive_turn(self, disc, mod, moderator, tmp_db, pricing):
        """One human-moderator turn through the real pipeline."""
        submitted = submit_human_message(
            disc, tmp_db, mod.id, "Yes, proceed with the recommendation.",
        )
        assert "error" not in submitted
        result = await complete_turn(
            disc, moderator, tmp_db, pricing,
            get_state_fn=lambda: {},
            moderator_summary="Noted.",
        )
        return result

    def _blocked_notices(self, disc):
        """System messages announcing the blocked switch."""
        return [m for m in disc.messages
                if m.role == MessageRole.SYSTEM
                and "could not be adopted" in m.content]

    @pytest.mark.asyncio
    async def test_blocked_switch_is_loud(
        self, tmp_db, sample_provider, monkeypatch, caplog,
    ):
        """A blocked switch returns switch_error, logs a warning, and
        posts an explanatory system message into the transcript."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc, mod, moderator, pricing = self._make_triage_pipeline(
            tmp_db, sample_provider)

        with caplog.at_level(logging.WARNING,
                             logger="consensus.app_discussion_flow"):
            result = await self._drive_turn(
                disc, mod, moderator, tmp_db, pricing)

        assert result.get("method_complete") is True
        assert "test-model" in result["switch_error"]
        assert disc.discussion_method == "triage"
        # Logged (golden rule 6)
        assert any("test-model" in rec.message
                   for rec in caplog.records)
        # Durable transcript notice quoting the validator's error
        notices = self._blocked_notices(disc)
        assert len(notices) == 1
        assert "test-model" in notices[0].content

    @pytest.mark.asyncio
    async def test_blocked_switch_notice_posted_only_once(
        self, tmp_db, sample_provider, monkeypatch,
    ):
        """Turns completing after the blocked switch must not repost the
        notice — complete_turn re-enters the method-ended branch while
        the frontend concludes."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc, mod, moderator, pricing = self._make_triage_pipeline(
            tmp_db, sample_provider)

        result = await self._drive_turn(
            disc, mod, moderator, tmp_db, pricing)
        assert result.get("switch_error")

        # One more turn completes before the frontend concludes —
        # complete_turn re-enters the method-ended branch directly.
        result = await complete_turn(
            disc, moderator, tmp_db, pricing,
            get_state_fn=lambda: {},
            moderator_summary="Noted.",
        )
        assert result.get("method_complete") is True
        assert result.get("switch_error"), (
            "re-entry must still report the blocked switch"
        )
        assert len(self._blocked_notices(disc)) == 1, (
            "the blocked-switch notice was posted more than once"
        )

    @pytest.mark.asyncio
    async def test_blocked_switch_to_different_method_posts_new_notice(
        self, tmp_db, sample_provider, monkeypatch,
    ):
        """The once-only dedup is per target method (PR #39 review):
        a later blocked switch to a *different* method is new
        information and must reach the transcript."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc, mod, moderator, pricing = self._make_triage_pipeline(
            tmp_db, sample_provider)

        result = await self._drive_turn(
            disc, mod, moderator, tmp_db, pricing)
        assert result.get("switch_error")
        assert len(self._blocked_notices(disc)) == 1

        # Triage re-selects a different (also structured) method.
        disc.method_state["chosen_method"] = "ach"
        disc.method_state["recommended_method"] = "ach"
        result = await complete_turn(
            disc, moderator, tmp_db, pricing,
            get_state_fn=lambda: {},
            moderator_summary="Noted.",
        )
        assert result.get("switch_error")
        notices = self._blocked_notices(disc)
        assert len(notices) == 2
        assert "Analysis of Competing Hypotheses" in notices[1].content

    @pytest.mark.asyncio
    async def test_unknown_capability_switch_unchanged(
        self, tmp_db, sample_provider, monkeypatch,
    ):
        """No pricing data (e.g. a local model): the switch proceeds and
        no switch_error is reported."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        disc, mod, moderator, pricing = self._make_triage_pipeline(
            tmp_db, sample_provider)

        result = await self._drive_turn(
            disc, mod, moderator, tmp_db, pricing)

        assert result.get("method_switched") is True
        assert "switch_error" not in result
        assert disc.discussion_method == "delphi"
        assert self._blocked_notices(disc) == []
