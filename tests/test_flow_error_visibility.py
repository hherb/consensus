"""Error visibility in the active-discussion flow (issues #71, #72, #74).

Golden rule 6: every caught error must be shown to the user in the UI and
logged.  The final synthesis swallowed its exception whole (#71) and
``generate_ai_turn`` reported internal bugs to the user as provider "API
errors" (#74).  These tests pin the repaired contracts.  The Triage
recommender's own silent degrade (#72) is in
``tests/test_flow_triage_recommender.py``.
"""

import sqlite3
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from consensus.app_discussion_flow import (
    conclude_discussion,
    generate_ai_turn,
    mediate,
)
from consensus.app_discussion_flow.helpers import (
    describe_internal_error,
    is_provider_error,
)
from consensus.models import MessageRole
from consensus.pricing import PricingCache


def _http_status_error(status_code: int, body: str) -> httpx.HTTPStatusError:
    """Build a real httpx.HTTPStatusError carrying ``body``."""
    request = httpx.Request("POST", "https://api.example.com/chat")
    response = httpx.Response(status_code, text=body, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response)


_INSUFFICIENT_BALANCE = '{"error":{"code":"1113","message":"Insufficient balance"}}'


def _conclusion_response():
    """A minimal successful ``generate_conclusion`` response."""
    resp = MagicMock()
    resp.content = "We agree on X."
    resp.model = "test-model"
    resp.prompt_tokens = 10
    resp.completion_tokens = 20
    resp.total_tokens = 30
    resp.latency_ms = 100
    return resp


# ---------------------------------------------------------------------------
# Issue #71 — the final synthesis must never fail silently
# ---------------------------------------------------------------------------

class TestConclusionFailureIsVisible:
    """A failed Final Synthesis must reach the transcript and the caller."""

    @pytest.mark.asyncio
    async def test_failure_posts_notice_into_transcript(
        self, tmp_db, discussion_with_entities
    ):
        """The provider's message lands in the discussion as a system message."""
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        moderator = MagicMock()
        moderator.generate_conclusion = AsyncMock(
            side_effect=_http_status_error(402, _INSUFFICIENT_BALANCE))
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        await conclude_discussion(disc, moderator, tmp_db, pricing)

        notices = [m for m in disc.messages if m.role == MessageRole.SYSTEM]
        assert len(notices) == 1
        assert "HTTP 402: Insufficient balance" in notices[0].content
        persisted = tmp_db.get_messages(disc.id)
        assert any("Insufficient balance" in m["content"] for m in persisted)

    @pytest.mark.asyncio
    async def test_failure_returned_to_the_caller(
        self, tmp_db, discussion_with_entities
    ):
        """The result dict carries conclusion_error alongside concluded."""
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        moderator = MagicMock()
        moderator.generate_conclusion = AsyncMock(
            side_effect=_http_status_error(402, _INSUFFICIENT_BALANCE))
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        result = await conclude_discussion(disc, moderator, tmp_db, pricing)

        assert result["concluded"] is True
        assert "HTTP 402: Insufficient balance" in result["conclusion_error"]

    @pytest.mark.asyncio
    async def test_discussion_still_concluded_after_failure(
        self, tmp_db, discussion_with_entities
    ):
        """A failed synthesis must not leave the discussion half-ended."""
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        moderator = MagicMock()
        moderator.generate_conclusion = AsyncMock(side_effect=RuntimeError("boom"))
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        await conclude_discussion(disc, moderator, tmp_db, pricing)

        assert disc.is_active is False
        assert disc.status == "concluded"

    @pytest.mark.asyncio
    async def test_database_failure_still_reaches_the_caller(
        self, tmp_db, discussion_with_entities
    ):
        """A DB-origin failure must not suppress the user-facing notice.

        ``db.add_message`` is itself the failing call here, so the notice
        cannot be persisted — the error must still surface in the returned
        dict and in the in-memory transcript rather than raising out.
        """
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        moderator = MagicMock()
        moderator.generate_conclusion = AsyncMock(
            return_value=_conclusion_response())
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        tmp_db.add_message = MagicMock(
            side_effect=sqlite3.OperationalError("database is locked"))

        result = await conclude_discussion(disc, moderator, tmp_db, pricing)

        assert "database is locked" in result["conclusion_error"]
        assert any(m.role == MessageRole.SYSTEM
                   and "database is locked" in m.content
                   for m in disc.messages)

    @pytest.mark.asyncio
    async def test_successful_conclusion_reports_no_error(
        self, tmp_db, discussion_with_entities
    ):
        """The happy path must not gain a spurious error key."""
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        moderator = MagicMock()
        moderator.generate_conclusion = AsyncMock(
            return_value=_conclusion_response())
        moderator.prompt_id = MagicMock(return_value=None)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        result = await conclude_discussion(disc, moderator, tmp_db, pricing)

        assert result == {"concluded": True}
        assert not [m for m in disc.messages if m.role == MessageRole.SYSTEM]


class TestConcludeDiscussionSurfacesError:
    """``ConsensusApp.conclude_discussion`` must not drop the error key."""

    @pytest.mark.asyncio
    async def test_state_carries_conclusion_error(self, tmp_path):
        from consensus.app import ConsensusApp

        app = ConsensusApp(db_path=str(tmp_path / "conclude.db"))
        pid = app.db.add_provider("Local", "http://localhost:11434/v1", "")
        mod_id = app.db.add_entity(
            "Moderator", "ai", "#aaa", pid, "llama3", 0.5, 512, "")
        p1_id = app.db.add_entity(
            "Alice", "ai", "#bbb", pid, "llama3", 0.7, 1024, "")
        app.add_to_discussion(mod_id, is_moderator=True)
        app.add_to_discussion(p1_id)
        app.set_topic("Should AI be regulated?")
        app.discussion.id = app.db.create_discussion(
            app.discussion.topic, mod_id)
        app.moderator.generate_conclusion = AsyncMock(
            side_effect=_http_status_error(402, _INSUFFICIENT_BALANCE))

        state = await app.conclude_discussion()

        assert "HTTP 402: Insufficient balance" in state["conclusion_error"]


# ---------------------------------------------------------------------------
# Issue #71 (related) — mediation must not drop the provider's response body
# ---------------------------------------------------------------------------

class TestMediationFailureDetail:
    """``mediate`` must route its error through describe_turn_error."""

    @pytest.mark.asyncio
    async def test_provider_body_reaches_the_error(
        self, tmp_db, discussion_with_entities
    ):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        moderator = MagicMock()
        moderator.mediate = AsyncMock(
            side_effect=_http_status_error(429, _INSUFFICIENT_BALANCE))
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)

        result = await mediate(disc, moderator, tmp_db, pricing, "context")

        assert "HTTP 429: Insufficient balance" in result["error"]


# ---------------------------------------------------------------------------
# Issue #74 — internal bugs must not be reported as provider failures
# ---------------------------------------------------------------------------

class TestErrorClassification:
    """``is_provider_error`` separates provider/network faults from bugs."""

    def test_http_error_is_a_provider_error(self):
        assert is_provider_error(_http_status_error(500, "")) is True

    def test_connect_error_is_a_provider_error(self):
        assert is_provider_error(httpx.ConnectError("no route")) is True

    def test_timeout_is_a_provider_error(self):
        assert is_provider_error(TimeoutError()) is True

    def test_structured_output_error_is_a_provider_error(self):
        from consensus.structured_output import StructuredOutputError

        assert is_provider_error(StructuredOutputError("no tools")) is True

    def test_key_error_is_not_a_provider_error(self):
        assert is_provider_error(KeyError("positions")) is False

    def test_sqlite_error_is_not_a_provider_error(self):
        assert is_provider_error(sqlite3.OperationalError("locked")) is False


class TestDescribeInternalError:
    """A bare KeyError renders as ``'positions'`` — useless on its own."""

    def test_names_the_exception_type(self):
        assert describe_internal_error(KeyError("positions")) == (
            "KeyError: 'positions'")

    def test_empty_message_uses_the_type_alone(self):
        assert describe_internal_error(RuntimeError()) == "RuntimeError"


class TestTurnErrorWording:
    """``generate_ai_turn`` must label the failure it actually saw."""

    def _install_failing_method(self, monkeypatch, disc, name, error):
        """Register a method whose process_response raises ``error``."""
        import consensus.methods as methods_registry
        from consensus.methods.base import DiscussionMethod, Phase
        from consensus.methods.phase_handler import PhaseHandler

        class _Handler(PhaseHandler):
            phase = Phase("p", "P")

            def get_system_prompt(self, entity, discussion):
                return ""

            def get_turn_prompt(self, entity, discussion):
                return ""

            def process_response(self, content, entity, discussion):
                raise error

        class _M(DiscussionMethod):
            display_name = "Failing"
            description = "test"
            phase_handlers = (_Handler(),)

        _M.name = name
        disc.discussion_method = name
        disc.method_state = _M().init_state(disc)
        monkeypatch.setitem(methods_registry._METHODS, name, _M)

    def _moderator(self, disc, tmp_db, resp=None, error=None):
        from consensus.moderator import Moderator

        moderator = Moderator(disc, tmp_db)
        if error is not None:
            moderator.generate_turn = AsyncMock(side_effect=error)
        else:
            moderator.generate_turn = AsyncMock(return_value=resp)
        moderator.prompt_id = MagicMock(return_value=None)
        return moderator

    def _response(self):
        resp = MagicMock()
        resp.content = "My contribution."
        resp.model = "test-model"
        resp.prompt_tokens = 10
        resp.completion_tokens = 20
        resp.total_tokens = 30
        resp.latency_ms = 100
        resp.tool_calls = []
        resp.warning = None
        resp.structured_output = None
        resp.finish_reason = "stop"
        return resp

    @pytest.mark.asyncio
    async def test_handler_bug_is_reported_as_internal(
        self, monkeypatch, tmp_db, discussion_with_entities
    ):
        """A KeyError in a phase handler is a bug, not an API error."""
        disc = discussion_with_entities
        self._install_failing_method(
            monkeypatch, disc, "_test_internal_bug", KeyError("positions"))
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = self._moderator(disc, tmp_db, resp=self._response())

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)

        assert result["skipped"] is True
        assert result["error_kind"] == "internal"
        assert "API error" not in result["content"]
        assert "internal error" in result["content"]
        assert "KeyError: 'positions'" in result["content"]

    @pytest.mark.asyncio
    async def test_provider_failure_keeps_api_wording(
        self, monkeypatch, tmp_db, discussion_with_entities
    ):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = self._moderator(
            disc, tmp_db,
            error=_http_status_error(429, _INSUFFICIENT_BALANCE))

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)

        assert result["error_kind"] == "provider"
        assert "API error" in result["content"]
        assert "HTTP 429: Insufficient balance" in result["content"]

    @pytest.mark.asyncio
    async def test_notice_survives_a_failing_database(
        self, monkeypatch, tmp_db, discussion_with_entities
    ):
        """A DB-origin failure must still produce a user-visible notice.

        The handler's own ``db.add_message`` call is the second casualty;
        without a guard it raises out of ``generate_ai_turn`` and the user
        sees nothing at all.
        """
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        pricing = PricingCache(tmp_db.conn, tmp_db._lock)
        moderator = self._moderator(disc, tmp_db, resp=self._response())
        tmp_db.add_message = MagicMock(
            side_effect=sqlite3.OperationalError("database is locked"))

        result = await generate_ai_turn(disc, moderator, tmp_db, pricing)

        assert result["skipped"] is True
        assert result["error_kind"] == "internal"
        assert "database is locked" in result["content"]
        assert disc.messages[-1].content == result["content"]
