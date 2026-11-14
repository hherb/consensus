"""Characterization tests for the moderator's own calls (issue #61 net).

Covers summary / conclusion / mediation, AI-client caching and BYOK key
rotation, per-participant context configuration and the turn helpers.
"""

import asyncio
import logging

import pytest

from consensus.ai_client import AIClient
from consensus.context_strategies import ContextConfig, ContextStrategy
from consensus.models import EntityType
from consensus.moderator import (
    CONCLUSION_MAX_TOKENS, MEDIATION_MAX_TOKENS, MODERATOR_TEMPERATURE,
    SUMMARY_MAX_TOKENS, Moderator,
)
from consensus.models import Message
from .document_helpers import patch_where_defined
from .moderator_helpers import mod, scripted_llm  # noqa: F401 — fixtures
from .test_moderator_turn import FakeMethod, system_of, task_of, use_method


def _moderator_system(moderator, disc, ai) -> str:
    return moderator.resolve_prompt(
        "moderator", "ai", "system", entity_name=ai.name, topic=disc.topic,
        participants=moderator._participant_names())


class TestSummary:
    async def test_db_prompt_and_settings(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        disc.messages.append(Message(entity_id=human.id,
                                     entity_name=human.name, content="hi"))
        resp = await moderator.generate_summary(next_speaker_name="Next")

        payload = scripted_llm.payloads[0]
        assert resp.content == scripted_llm.DEFAULT_REPLY
        assert system_of(payload) == _moderator_system(moderator, disc, ai)
        assert task_of(payload) == moderator.resolve_prompt(
            "moderator", "ai", "summarize", entity_name=ai.name,
            topic=disc.topic, speaker_name=human.name, turn_number="1",
            participants=moderator._participant_names(),
            next_speaker_name="Next")
        assert payload["temperature"] == MODERATOR_TEMPERATURE
        assert payload["max_tokens"] == SUMMARY_MAX_TOKENS

    async def test_method_prompt_wins(self, mod, scripted_llm, monkeypatch):
        moderator, disc, ai, human = mod
        method = FakeMethod(summary="M-SUM")
        use_method(monkeypatch, method)
        await moderator.generate_summary(next_speaker_name="N")
        assert task_of(scripted_llm.payloads[0]) == "M-SUM"
        assert method.summary_args == ("Unknown", "N")

    async def test_fallback_task(self, mod, scripted_llm, tmp_db):
        moderator, disc, ai, human = mod
        tmp_db.conn.execute("DELETE FROM prompts")
        await moderator.generate_summary()
        assert task_of(scripted_llm.payloads[0]) == "Summarize turn 1 by Unknown."


class TestConclusion:
    async def test_db_prompt_and_settings(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        await moderator.generate_conclusion()
        payload = scripted_llm.payloads[0]
        assert system_of(payload) == _moderator_system(moderator, disc, ai)
        assert task_of(payload) == moderator.resolve_prompt(
            "moderator", "ai", "conclude", topic=disc.topic,
            participants=moderator._participant_names())
        assert payload["temperature"] == MODERATOR_TEMPERATURE
        assert payload["max_tokens"] == CONCLUSION_MAX_TOKENS

    async def test_method_prompt_wins(self, mod, scripted_llm, monkeypatch):
        moderator, disc, ai, human = mod
        use_method(monkeypatch, FakeMethod(conclusion="M-CON"))
        await moderator.generate_conclusion()
        assert task_of(scripted_llm.payloads[0]) == "M-CON"

    async def test_fallback_task(self, mod, scripted_llm, tmp_db):
        moderator, disc, ai, human = mod
        tmp_db.conn.execute("DELETE FROM prompts")
        await moderator.generate_conclusion()
        assert task_of(scripted_llm.payloads[0]) == (
            f"Conclude the discussion on '{disc.topic}'.")


class TestMediate:
    async def test_db_prompt_and_settings(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        await moderator.mediate("They disagree on X")
        payload = scripted_llm.payloads[0]
        assert system_of(payload) == _moderator_system(moderator, disc, ai)
        expected = moderator.resolve_prompt(
            "moderator", "ai", "mediate", context="They disagree on X",
            topic=disc.topic, participants=moderator._participant_names())
        assert "They disagree on X" in expected
        assert task_of(payload) == expected
        assert payload["temperature"] == MODERATOR_TEMPERATURE
        assert payload["max_tokens"] == MEDIATION_MAX_TOKENS

    async def test_fallback_task_default_context(self, mod, scripted_llm, tmp_db):
        moderator, disc, ai, human = mod
        tmp_db.conn.execute("DELETE FROM prompts")
        await moderator.mediate()
        assert task_of(scripted_llm.payloads[0]) == (
            "Mediate: A disagreement has arisen.")


@pytest.mark.parametrize("call", ["generate_summary", "generate_conclusion",
                                  "mediate"])
async def test_non_ai_moderator_makes_no_call(mod, scripted_llm, call):
    moderator, disc, ai, human = mod
    disc.moderator_id = human.id
    assert disc.moderator.entity_type == EntityType.HUMAN
    resp = await getattr(moderator, call)()
    assert resp.content == ""
    assert scripted_llm.payloads == []


class TestClients:
    def test_client_cached_per_entity(self, mod):
        moderator, disc, ai, human = mod
        assert moderator._get_client(ai) is moderator._get_client(ai)

    async def test_key_resolver_used(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        seen: list = []
        moderator._key_resolver = lambda pid, env: seen.append((pid, env)) or "k1"
        await moderator.generate_turn(ai)
        assert seen == [(ai.ai_config.provider_id, "")]
        assert scripted_llm.api_keys == ["k1"]

    def test_resolve_key_without_config(self, mod):
        moderator, disc, ai, human = mod
        assert moderator._resolve_api_key(human) == ""
        ai.ai_config.api_key = "env-key"
        assert moderator._resolve_api_key(ai) == "env-key"

    async def test_key_change_replaces_and_closes_client(self, mod, monkeypatch):
        moderator, disc, ai, human = mod
        closed: list = []

        async def fake_close(self):
            closed.append(self.api_key)

        monkeypatch.setattr(AIClient, "close", fake_close)
        keys = iter(["k1", "k2"])
        moderator._key_resolver = lambda pid, env: next(keys)
        first = moderator._get_client(ai)
        second = moderator._get_client(ai)
        await asyncio.sleep(0)

        assert first is not second and second.api_key == "k2"
        assert closed == ["k1"]

    def test_key_change_without_loop_drops_client(self, mod):
        moderator, disc, ai, human = mod
        keys = iter(["k1", "k2"])
        moderator._key_resolver = lambda pid, env: next(keys)
        first = moderator._get_client(ai)
        assert moderator._get_client(ai) is not first

    async def test_close_swallows_errors(self, mod, monkeypatch, caplog):
        moderator, disc, ai, human = mod

        async def broken_close(self):
            raise RuntimeError("socket gone")

        moderator._get_client(ai)
        monkeypatch.setattr(AIClient, "close", broken_close)
        with caplog.at_level(logging.DEBUG, logger="consensus.moderator"):
            await moderator.close()
        assert moderator._clients == {}
        assert "Error closing AI client" in caplog.text


class TestContextConfig:
    def test_member_strategy_and_defaults(self, mod, tmp_db):
        moderator, disc, ai, human = mod
        tmp_db.update_member_context_strategy(disc.id, ai.id, "full", 7)
        config = moderator._get_context_config(ai.id)
        assert config.strategy == ContextStrategy.FULL
        assert moderator._get_context_config(None) == ContextConfig()
        assert moderator._get_context_config(999) == ContextConfig()

    def test_token_window_reads_model_limits(self, mod, tmp_db, monkeypatch):
        moderator, disc, ai, human = mod
        tmp_db.update_member_context_strategy(disc.id, ai.id, "token_window")
        asked: list = []
        monkeypatch.setattr(tmp_db.pricing, "get_context_length",
                            lambda m, b: asked.append((m, b)) or 8000)
        config = moderator._get_context_config(ai.id)
        assert asked == [(ai.ai_config.model, ai.ai_config.base_url)]
        assert config.model_context_length == 8000
        assert config.reserved_output_tokens == ai.ai_config.max_tokens

    async def test_token_window_counts_overhead(self, mod, scripted_llm, tmp_db,
                                                monkeypatch):
        from consensus.token_utils import estimate_tokens
        moderator, disc, ai, human = mod
        tmp_db.update_member_context_strategy(disc.id, ai.id, "token_window")
        seen: list = []
        patch_where_defined(
            monkeypatch, Moderator._load_context_messages,
            "load_context_messages", lambda db, did, cfg: seen.append(cfg) or [])
        use_method(monkeypatch, FakeMethod(system="S", turn="T"))
        await moderator.generate_turn(ai)
        assert seen[0].system_tokens == (
            estimate_tokens("S") + 4
            + estimate_tokens(f"Discussion topic: {disc.topic}") + 4
            + estimate_tokens("T") + 4 + 3)

    async def test_semantic_uses_embedding_loader(self, mod, scripted_llm,
                                                  tmp_db, monkeypatch):
        moderator, disc, ai, human = mod
        tmp_db.update_member_context_strategy(disc.id, ai.id, "semantic")
        seen: list = []

        async def fake_async(db, did, cfg, embed_client):
            seen.append((did, cfg.strategy, type(embed_client).__name__))
            return [Message(entity_id=human.id, entity_name=human.name,
                            content="recalled")]

        patch_where_defined(monkeypatch, Moderator._load_context_messages,
                            "load_context_messages_async", fake_async)
        await moderator.generate_turn(ai)
        assert seen == [(disc.id, ContextStrategy.SEMANTIC, "EmbeddingClient")]
        assert {"role": "user", "content": f"[{human.name}]: recalled"} in (
            scripted_llm.payloads[0]["messages"])


class TestTurnHelpers:
    def test_peek_does_not_advance(self, mod):
        moderator, disc, ai, human = mod
        assert moderator.peek_next_speaker() is human
        assert disc.current_turn_index == 0
        disc.turn_order = []
        assert moderator.peek_next_speaker() is None

    def test_guidance_and_prompt_id(self, mod):
        moderator, disc, ai, human = mod
        guidance = moderator.get_human_guidance("moderator")
        assert guidance == moderator.resolve_prompt(
            "moderator", "human", "guidance", topic=disc.topic,
            participants=moderator._participant_names())
        assert moderator.prompt_id("moderator", "ai", "system") > 0
        assert moderator.prompt_id("nobody", "ai", "system") == 0
