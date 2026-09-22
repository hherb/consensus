"""Error *classification* must name the right culprit (#72, #74 follow-ups).

The first round of error-visibility work made caught errors visible but
could still misattribute them: a provider outage reported as a Consensus
bug, a missing API key reported as a provider fault, and — the one that
mattered most — a crashed method classifier reported as nothing at all,
because ``MethodRecommender.recommend`` swallowed its own exceptions and
returned a stand-in recommendation that was indistinguishable from a real
one.

These tests pin the repaired contracts end to end, and deliberately drive
the *real* ``MethodRecommender`` rather than a mock of it: mocking
``recommend`` with ``side_effect`` asserts a raising contract the class did
not have, which is exactly how the original defect stayed green.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from consensus.ai_client import AIClient, AIResponseFormatError
from consensus.app_discussion_flow.conclusion import conclude_discussion, mediate
from consensus.app_discussion_flow.helpers import (
    ERROR_KIND_CONFIG, ERROR_KIND_INTERNAL, ERROR_KIND_PROVIDER,
    UNSAVED_NOTICE_SUFFIX, classify_flow_error, describe_flow_error,
    post_notice,
)
from consensus.app_discussion_flow.method_switch import run_triage_recommender
from consensus.models import ConfigurationError, MessageRole
from consensus.methods.recommender import RecommenderError


def _http_status_error(status_code: int, body: str) -> httpx.HTTPStatusError:
    """Build a real httpx.HTTPStatusError carrying ``body``."""
    request = httpx.Request("POST", "https://api.example.com/chat")
    response = httpx.Response(status_code, text=body, request=request)
    return httpx.HTTPStatusError(
        f"HTTP {status_code}", request=request, response=response)


def _ok_response(payload: object, text: str = "") -> httpx.Response:
    """Build a real HTTP 200 response carrying ``payload`` as its body."""
    request = httpx.Request("POST", "https://api.example.com/chat")
    if text:
        return httpx.Response(200, text=text, request=request)
    return httpx.Response(200, json=payload, request=request)


# ---------------------------------------------------------------------------
# The classifier itself
# ---------------------------------------------------------------------------

class TestClassifyFlowError:
    """Three buckets, because only one of them warrants a bug report."""

    def test_http_error_is_a_provider_fault(self):
        assert classify_flow_error(
            _http_status_error(429, "slow down")) == ERROR_KIND_PROVIDER

    def test_bare_connection_error_is_a_provider_fault(self):
        """Not every transport failure arrives as an httpx type.

        ``httpx.ConnectError`` subclasses ``httpx.HTTPError``, so a test
        using one does not exercise this arm at all — a raw socket failure
        from a non-httpx layer does.
        """
        assert classify_flow_error(
            ConnectionError("refused")) == ERROR_KIND_PROVIDER

    def test_timeout_is_a_provider_fault(self):
        assert classify_flow_error(
            TimeoutError("timed out")) == ERROR_KIND_PROVIDER

    def test_malformed_provider_body_is_a_provider_fault(self):
        """The #74 inversion: a 200 with a junk body is not our bug."""
        assert classify_flow_error(
            AIResponseFormatError("no choices")) == ERROR_KIND_PROVIDER

    def test_missing_ai_config_is_a_configuration_fault(self):
        """A setting to fix, not a bug to report and not a provider outage."""
        assert classify_flow_error(
            ConfigurationError("Alice has no AI configuration"),
        ) == ERROR_KIND_CONFIG

    def test_handler_bug_is_internal(self):
        assert classify_flow_error(
            KeyError("positions")) == ERROR_KIND_INTERNAL

    def test_value_error_is_internal(self):
        """The most likely shape of a real phase-handler bug."""
        assert classify_flow_error(
            ValueError("bad payload")) == ERROR_KIND_INTERNAL

    def test_recommender_error_is_classified_by_its_cause(self):
        """The wrapper must not hide whose fault it was."""
        cause = KeyError("phases")
        err = RecommenderError("wrapped")
        err.__cause__ = cause
        assert classify_flow_error(err) == ERROR_KIND_INTERNAL

    def test_recommender_error_with_provider_cause_stays_provider(self):
        cause = _http_status_error(503, "upstream down")
        err = RecommenderError("wrapped")
        err.__cause__ = cause
        assert classify_flow_error(err) == ERROR_KIND_PROVIDER


class TestDescribeFlowError:
    """The description has to stay actionable and bounded."""

    def test_http_body_is_preferred_over_str(self):
        detail = describe_flow_error(
            _http_status_error(402, '{"error":{"message":"Insufficient balance"}}'))
        assert "Insufficient balance" in detail

    def test_internal_error_names_its_type(self):
        """``str(KeyError('x'))`` is just ``'x'`` — useless on its own."""
        assert describe_flow_error(KeyError("positions")) == "KeyError: 'positions'"

    def test_internal_detail_is_truncated(self):
        """An internal exception can carry a huge repr; a toast cannot."""
        detail = describe_flow_error(ValueError("x" * 5000))
        assert len(detail) < 300

    def test_recommender_error_unwraps_to_the_provider_body(self):
        cause = _http_status_error(401, '{"error":{"message":"Invalid key"}}')
        err = RecommenderError("wrapped")
        err.__cause__ = cause
        assert "Invalid key" in describe_flow_error(err)


# ---------------------------------------------------------------------------
# AIClient must type its own parse failures
# ---------------------------------------------------------------------------

class TestAIClientResponseFormat:
    """A 200 with an unusable body is the provider's fault, and typed.

    Without this, ``data["choices"][0]`` raises a bare ``KeyError`` that
    the flow layer cannot tell from a bug in a phase handler.
    """

    @pytest.mark.asyncio
    async def test_non_json_body_raises_format_error(self):
        client = AIClient(base_url="https://x/v1", api_key="k")
        html = "<html><title>502 Bad Gateway</title></html>"
        with patch.object(client, "_post_with_retry",
                          AsyncMock(return_value=_ok_response(None, text=html))):
            with pytest.raises(AIResponseFormatError, match="not JSON"):
                await client.complete([{"role": "user", "content": "hi"}], "m")
        assert "Bad Gateway" in str(
            await _capture(client, _ok_response(None, text=html)))

    @pytest.mark.asyncio
    async def test_error_object_in_success_response_is_surfaced(self):
        """Gateways that answer 200 with ``{"error": ...}`` are common."""
        client = AIClient(base_url="https://x/v1", api_key="k")
        body = {"error": {"message": "context length exceeded"}}
        with patch.object(client, "_post_with_retry",
                          AsyncMock(return_value=_ok_response(body))):
            with pytest.raises(AIResponseFormatError,
                               match="context length exceeded"):
                await client.complete([{"role": "user", "content": "hi"}], "m")

    @pytest.mark.asyncio
    async def test_missing_choices_raises_format_error(self):
        client = AIClient(base_url="https://x/v1", api_key="k")
        with patch.object(client, "_post_with_retry",
                          AsyncMock(return_value=_ok_response({"usage": {}}))):
            with pytest.raises(AIResponseFormatError, match="no usable choices"):
                await client.complete([{"role": "user", "content": "hi"}], "m")

    @pytest.mark.asyncio
    async def test_empty_choices_list_raises_format_error(self):
        client = AIClient(base_url="https://x/v1", api_key="k")
        with patch.object(client, "_post_with_retry",
                          AsyncMock(return_value=_ok_response({"choices": []}))):
            with pytest.raises(AIResponseFormatError):
                await client.complete([{"role": "user", "content": "hi"}], "m")

    @pytest.mark.asyncio
    async def test_complete_with_tools_guards_the_same_way(self):
        client = AIClient(base_url="https://x/v1", api_key="k")
        with patch.object(client, "_post_with_retry",
                          AsyncMock(return_value=_ok_response({"choices": []}))):
            with pytest.raises(AIResponseFormatError):
                await client.complete_with_tools(
                    [{"role": "user", "content": "hi"}], "m")

    @pytest.mark.asyncio
    async def test_a_good_response_still_parses(self):
        """The guard must not change the happy path."""
        client = AIClient(base_url="https://x/v1", api_key="k")
        body = {"model": "m", "usage": {"total_tokens": 7},
                "choices": [{"message": {"content": "hello"},
                             "finish_reason": "stop"}]}
        with patch.object(client, "_post_with_retry",
                          AsyncMock(return_value=_ok_response(body))):
            resp = await client.complete(
                [{"role": "user", "content": "hi"}], "m")
        assert resp.content == "hello"
        assert resp.total_tokens == 7


async def _capture(client: AIClient, response: httpx.Response) -> Exception:
    """Return the AIResponseFormatError raised for ``response``."""
    with patch.object(client, "_post_with_retry",
                      AsyncMock(return_value=response)):
        try:
            await client.complete([{"role": "user", "content": "hi"}], "m")
        except AIResponseFormatError as e:
            return e
    raise AssertionError("expected AIResponseFormatError")


# ---------------------------------------------------------------------------
# #72, for real this time: the recommender must not mask its own failure
# ---------------------------------------------------------------------------

class TestRealRecommenderSurfacesFailure:
    """Drives the genuine ``MethodRecommender``, not a mock of it.

    The original #72 fix was unreachable in production because
    ``recommend()`` caught everything and returned a stand-in. Every test
    here goes through the real class so that regression cannot come back
    while the suite stays green.
    """

    @pytest.mark.asyncio
    async def test_provider_outage_is_recorded_as_a_failure(
        self, tmp_db, discussion_with_entities,
    ):
        disc = discussion_with_entities
        mod = disc.moderator
        failing = MagicMock()
        failing.complete = AsyncMock(
            side_effect=_http_status_error(401, '{"error":"Invalid API key"}'))
        failing.close = AsyncMock()

        with patch("consensus.ai_client.AIClient", return_value=failing):
            detail = await run_triage_recommender(
                disc, mod, lambda pid, env: "key")

        assert detail, "a failed classifier must report a failure"
        assert "Invalid API key" in detail
        state = disc.method_state
        assert "Invalid API key" in state["recommender_error"]
        assert state["recommendations"] == []
        assert state["recommended_method"] == "open_discussion"

    @pytest.mark.asyncio
    async def test_unparseable_reply_is_recorded_as_a_failure(
        self, tmp_db, discussion_with_entities,
    ):
        """A refusal is not a recommendation of Open Discussion."""
        disc = discussion_with_entities
        mod = disc.moderator
        resp = MagicMock()
        resp.content = "I cannot help with that."
        client = MagicMock()
        client.complete = AsyncMock(return_value=resp)
        client.close = AsyncMock()

        with patch("consensus.ai_client.AIClient", return_value=client):
            detail = await run_triage_recommender(
                disc, mod, lambda pid, env: "key")

        assert detail
        assert disc.method_state["recommender_error"]
        assert disc.method_state["recommendations"] == []

    @pytest.mark.asyncio
    async def test_a_real_recommendation_is_not_reported_as_a_failure(
        self, tmp_db, discussion_with_entities,
    ):
        """The guard against over-correction: success must stay silent."""
        disc = discussion_with_entities
        mod = disc.moderator
        resp = MagicMock()
        resp.content = json.dumps({"recommendations": [
            {"method_name": "delphi", "display_name": "Delphi",
             "confidence": 0.9, "reasoning": "Forecasting fit.",
             "fit_factors": []},
        ]})
        client = MagicMock()
        client.complete = AsyncMock(return_value=resp)
        client.close = AsyncMock()

        with patch("consensus.ai_client.AIClient", return_value=client):
            detail = await run_triage_recommender(
                disc, mod, lambda pid, env: "key")

        assert detail is None
        assert "recommender_error" not in disc.method_state
        assert disc.method_state["recommended_method"] == "delphi"

    @pytest.mark.asyncio
    async def test_key_resolver_failure_does_not_escape(
        self, tmp_db, discussion_with_entities,
    ):
        """A raise here used to destroy the moderator's completed turn.

        ``key_resolver`` and client construction sat outside the ``try``,
        so the exception reached ``generate_ai_turn``'s handler, which
        discards the already-generated characterization.
        """
        disc = discussion_with_entities
        mod = disc.moderator

        def boom(provider_id, env):
            raise RuntimeError("no such provider")

        detail = await run_triage_recommender(disc, mod, boom)

        assert detail
        assert "no such provider" in detail
        assert disc.method_state["recommender_error"]

    @pytest.mark.asyncio
    async def test_client_close_failure_does_not_lose_a_success(
        self, tmp_db, discussion_with_entities,
    ):
        """Cleanup must never be able to fail a turn that already worked."""
        disc = discussion_with_entities
        mod = disc.moderator
        resp = MagicMock()
        resp.content = json.dumps({"recommendations": [
            {"method_name": "delphi", "display_name": "Delphi",
             "confidence": 0.9, "reasoning": "r", "fit_factors": []},
        ]})
        client = MagicMock()
        client.complete = AsyncMock(return_value=resp)
        client.close = AsyncMock(side_effect=RuntimeError("socket already gone"))

        with patch("consensus.ai_client.AIClient", return_value=client):
            detail = await run_triage_recommender(
                disc, mod, lambda pid, env: "key")

        assert detail is None
        assert disc.method_state["recommended_method"] == "delphi"


# ---------------------------------------------------------------------------
# post_notice must disclose a failed write
# ---------------------------------------------------------------------------

class TestPostNoticeDisclosesUnsavedNotices:
    """A notice that silently vanishes on reload is still a silent failure."""

    def test_successful_write_reports_persisted(
        self, tmp_db, discussion_with_entities,
    ):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        msg, persisted = post_notice(
            disc, tmp_db, disc.moderator, "something happened")
        assert persisted is True
        assert UNSAVED_NOTICE_SUFFIX not in msg.content
        rows = tmp_db.get_messages(disc.id)
        assert any("something happened" in r["content"] for r in rows)

    def test_failed_write_is_disclosed_in_the_notice(
        self, tmp_db, discussion_with_entities,
    ):
        disc = discussion_with_entities
        broken = MagicMock()
        broken.add_message.side_effect = RuntimeError("database is locked")

        msg, persisted = post_notice(
            disc, broken, disc.moderator, "something happened")

        assert persisted is False
        # Still visible now...
        assert "something happened" in msg.content
        # ...and honest about not surviving a reload.
        assert UNSAVED_NOTICE_SUFFIX in msg.content
        assert disc.messages[-1] is msg

    def test_failed_write_does_not_raise(
        self, tmp_db, discussion_with_entities,
    ):
        """The whole point: the DB failure must not replace the notice."""
        disc = discussion_with_entities
        broken = MagicMock()
        broken.add_message.side_effect = RuntimeError("database is locked")
        post_notice(disc, broken, disc.moderator, "visible anyway")
        assert "visible anyway" in disc.messages[-1].content


# ---------------------------------------------------------------------------
# Conclusion and mediation notices must match what actually happened
# ---------------------------------------------------------------------------

class TestConclusionNoticeMatchesReality:
    """"Could not be generated" must not sit under a generated synthesis."""

    @pytest.mark.asyncio
    async def test_generation_failure_says_not_generated(
        self, tmp_db, discussion_with_entities,
    ):
        disc = discussion_with_entities
        moderator = MagicMock()
        moderator.generate_conclusion = AsyncMock(
            side_effect=_http_status_error(500, "upstream exploded"))

        result = await conclude_discussion(
            disc, moderator, tmp_db, PricingCacheStub())

        assert result["conclusion_error"]
        notice = disc.messages[-1].content
        assert "could not be generated" in notice
        assert "could not be fully saved" not in notice

    @pytest.mark.asyncio
    async def test_persist_failure_says_generated_but_not_saved(
        self, tmp_db, discussion_with_entities,
    ):
        """The synthesis is on screen; claiming otherwise is simply false."""
        disc = discussion_with_entities
        moderator = MagicMock()
        resp = MagicMock()
        resp.content = "Here is what we concluded."
        resp.model = "m"
        resp.prompt_tokens = resp.completion_tokens = resp.total_tokens = 1
        resp.latency_ms = 5
        moderator.generate_conclusion = AsyncMock(return_value=resp)

        broken = MagicMock()
        broken.add_message.side_effect = RuntimeError("disk full")
        broken.update_discussion = MagicMock()

        result = await conclude_discussion(
            disc, moderator, broken, PricingCacheStub())

        assert result["conclusion_error"]
        contents = [m.content for m in disc.messages]
        # The synthesis really is in the transcript...
        assert any("Here is what we concluded." in c for c in contents)
        # ...so the notice must say "not saved", not "not generated".
        notice = contents[-1]
        assert "could not be fully saved" in notice
        assert "could not be generated" not in notice

    @pytest.mark.asyncio
    async def test_success_posts_no_notice(
        self, tmp_db, discussion_with_entities,
    ):
        """Guard against over-correction."""
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        moderator = MagicMock()
        resp = MagicMock()
        resp.content = "All good."
        resp.model = "m"
        resp.prompt_tokens = resp.completion_tokens = resp.total_tokens = 1
        resp.latency_ms = 5
        moderator.generate_conclusion = AsyncMock(return_value=resp)

        result = await conclude_discussion(
            disc, moderator, tmp_db, PricingCacheStub())

        assert result == {"concluded": True}
        assert not any(m.role == MessageRole.SYSTEM for m in disc.messages)


class TestMediationFailureLeavesATrace:
    """A toast is not a record — it is gone in four seconds."""

    @pytest.mark.asyncio
    async def test_failure_is_posted_to_the_transcript(
        self, tmp_db, discussion_with_entities,
    ):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        moderator = MagicMock()
        moderator.mediate = AsyncMock(
            side_effect=_http_status_error(429, "rate limited"))

        result = await mediate(
            disc, moderator, tmp_db, PricingCacheStub(), "please help")

        assert "rate limited" in result["error"]
        notice = disc.messages[-1].content
        assert "mediation could not be generated" in notice
        # And it is durable, not just in memory.
        rows = tmp_db.get_messages(disc.id)
        assert any("mediation could not be generated" in r["content"]
                   for r in rows)


class PricingCacheStub:
    """Minimal pricing stand-in — cost is not what these tests are about."""

    def calculate_cost_with_refresh(self, *args, **kwargs) -> float:
        return 0.0


class TestClassificationIsNotOverBroad:
    """Guards against the fix over-reaching in the other direction.

    Widening the provider bucket is tempting and wrong: a file error in a
    document tool is ours to fix, and quietly relabelling it "API error"
    would recreate #74 with the blame reversed.
    """

    def test_file_errors_are_not_provider_faults(self):
        """``OSError`` is ``ConnectionError``'s base — do not match on it."""
        assert classify_flow_error(
            FileNotFoundError("/tmp/missing.pdf")) == ERROR_KIND_INTERNAL

    def test_permission_errors_are_not_provider_faults(self):
        assert classify_flow_error(
            PermissionError("denied")) == ERROR_KIND_INTERNAL

    def test_type_errors_are_not_provider_faults(self):
        assert classify_flow_error(
            TypeError("NoneType is not iterable")) == ERROR_KIND_INTERNAL
