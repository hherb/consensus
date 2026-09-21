"""Triage method-recommendation failures must look like failures (#72).

The recommend phase exists to pick a method.  When the classifier could not
run, it used to record ``open_discussion`` and say nothing, leaving the user
unable to tell a deliberate recommendation from a crash.  These tests pin
the repaired contract: the failure is recorded in ``method_state``, appended
to the recommend-phase transcript message, carried into the confirm-phase
prompts, and an explicitly named method is honoured despite the empty
shortlist.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from consensus.app_discussion_flow import generate_ai_turn
# Package-internal: imported from its defining submodule, not the package
# facade, which re-exports only the flow API ``ConsensusApp`` calls.
from consensus.app_discussion_flow.method_switch import run_triage_recommender
from consensus.pricing import PricingCache


def _http_status_error(status_code: int, body: str) -> httpx.HTTPStatusError:
    """Build a real httpx.HTTPStatusError carrying ``body``."""
    request = httpx.Request("POST", "https://api.example.com/chat")
    response = httpx.Response(status_code, text=body, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response)


# ---------------------------------------------------------------------------
# Issue #72 — a failed Triage recommendation must look like a failure
# ---------------------------------------------------------------------------

class TestTriageRecommenderFailureIsVisible:
    """``run_triage_recommender`` must record and report its failures."""

    @pytest.mark.asyncio
    async def test_failure_recorded_in_method_state(
        self, tmp_db, discussion_with_entities
    ):
        disc = discussion_with_entities
        mod = disc.moderator

        with patch("consensus.methods.recommender.MethodRecommender") as MockRec, \
             patch("consensus.ai_client.AIClient") as MockClient:
            MockRec.return_value.recommend = AsyncMock(
                side_effect=_http_status_error(401, '{"error":"Invalid API key"}'))
            MockClient.return_value.close = AsyncMock()
            detail = await run_triage_recommender(
                disc, mod, lambda pid, env: "key")

        assert "HTTP 401: Invalid API key" in detail
        state = disc.method_state
        assert "HTTP 401: Invalid API key" in state["recommender_error"]
        assert state["recommendations"] == []
        assert state["recommended_method"] == "open_discussion"

    @pytest.mark.asyncio
    async def test_missing_ai_config_recorded_as_failure(
        self, tmp_db, discussion_with_entities
    ):
        """A moderator with no ai_config cannot run the classifier at all."""
        disc = discussion_with_entities
        mod = disc.moderator
        mod.ai_config = None

        detail = await run_triage_recommender(
            disc, mod, lambda pid, env: "key")

        assert detail
        assert disc.method_state["recommender_error"] == detail
        assert disc.method_state["recommended_method"] == "open_discussion"

    @pytest.mark.asyncio
    async def test_success_reports_no_failure(
        self, tmp_db, discussion_with_entities
    ):
        """A successful run returns None and clears a stale error."""
        disc = discussion_with_entities
        disc.method_state["recommender_error"] = "stale"
        mod = disc.moderator
        rec = MagicMock()
        rec.method_name = "delphi"
        rec.to_dict = MagicMock(return_value={"method_name": "delphi"})

        with patch("consensus.methods.recommender.MethodRecommender") as MockRec, \
             patch("consensus.ai_client.AIClient") as MockClient:
            MockRec.return_value.recommend = AsyncMock(return_value=[rec])
            MockClient.return_value.close = AsyncMock()
            detail = await run_triage_recommender(
                disc, mod, lambda pid, env: "key")

        assert detail is None
        assert "recommender_error" not in disc.method_state
        assert disc.method_state["recommended_method"] == "delphi"

    @pytest.mark.asyncio
    async def test_turn_transcript_names_the_failure(
        self, tmp_db, discussion_with_entities
    ):
        """The recommend-phase message must say the recommendation failed.

        This also pins the wiring of ``run_triage_recommender`` into
        ``generate_ai_turn`` (issue #73), which no test drove before.
        """
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.discussion_method = "triage"
        disc.method_state = {
            "current_phase": "recommend",
            "recommendations": [],
            "recommended_method": None,
            "chosen_method": None,
        }
        # The moderator takes the recommend-phase turn.
        disc.turn_order = [disc.moderator_id]
        disc.current_turn_index = 0

        resp = MagicMock()
        resp.content = "This is a forecasting problem."
        resp.model = "test-model"
        resp.prompt_tokens = 10
        resp.completion_tokens = 20
        resp.total_tokens = 30
        resp.latency_ms = 100
        resp.tool_calls = []
        resp.warning = None
        resp.structured_output = None
        resp.finish_reason = "stop"
        moderator = MagicMock()
        moderator.generate_turn = AsyncMock(return_value=resp)
        moderator.prompt_id = MagicMock(return_value=None)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        with patch("consensus.methods.recommender.MethodRecommender") as MockRec, \
             patch("consensus.ai_client.AIClient") as MockClient:
            MockRec.return_value.recommend = AsyncMock(
                side_effect=RuntimeError("classifier exploded"))
            MockClient.return_value.close = AsyncMock()
            result = await generate_ai_turn(
                disc, moderator, tmp_db, pricing,
                key_resolver=lambda pid, env: "key")

        assert "classifier exploded" in result["content"]
        assert "open_discussion" in result["content"]


class TestTriageConfirmPromptNamesFailure:
    """The confirm phase must not present a fallback as a recommendation."""

    def test_prompt_states_the_recommender_failed(
        self, discussion_with_entities
    ):
        from consensus.methods.phases.triage_confirm import (
            TriageConfirmHandler,
        )

        disc = discussion_with_entities
        disc.method_state = {
            "recommendations": [],
            "recommended_method": "open_discussion",
            "recommender_error": "HTTP 401: Invalid API key",
        }
        handler = TriageConfirmHandler()
        entity = disc.entities[1]

        system = handler.get_system_prompt(entity, disc)
        turn = handler.get_turn_prompt(entity, disc)

        assert "HTTP 401: Invalid API key" in system
        assert "HTTP 401: Invalid API key" in turn


class TestTriageConfirmHonoursExplicitChoice:
    """With no recommendations, a named method must still be selectable.

    The failure notice asks the user to name the method they want; that
    instruction is only honest if ``process_response`` can act on it.  The
    recommendation list is the normal validation whitelist, and it is empty
    on exactly this path (issue #72).
    """

    def _moderator_choice(self, disc, content):
        from consensus.methods.phases.triage_confirm import (
            TriageConfirmHandler,
        )

        moderator = disc.moderator
        TriageConfirmHandler().process_response(content, moderator, disc)
        return disc.method_state["chosen_method"]

    def test_backticked_method_is_honoured(self, discussion_with_entities):
        disc = discussion_with_entities
        disc.method_state = {
            "recommendations": [],
            "recommended_method": "open_discussion",
            "recommender_error": "HTTP 401: Invalid API key",
        }

        chosen = self._moderator_choice(
            disc, "The group asked for `delphi`, so we will use that.")

        assert chosen == "delphi"

    def test_unknown_name_falls_back(self, discussion_with_entities):
        """An invented method name must not be written into the state."""
        disc = discussion_with_entities
        disc.method_state = {
            "recommendations": [],
            "recommended_method": "open_discussion",
            "recommender_error": "HTTP 401: Invalid API key",
        }

        chosen = self._moderator_choice(
            disc, "Let us use `deliberative_telepathy`.")

        assert chosen == "open_discussion"
