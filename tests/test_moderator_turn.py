"""Characterization tests for ``Moderator.generate_turn`` (issue #61 net).

They drive the real ``AIClient`` parsing through ``ScriptedLLM`` (faked at
``_post_with_retry``) and patch collaborators where they are *defined*, so
they survive the split of ``moderator.py`` into a package.
"""

import logging

import pytest

from consensus.ai_client import AIResponse
from consensus.moderator import Moderator
from consensus.tools import MAX_TOOL_ITERATIONS, ToolResult
from .document_helpers import patch_where_defined
from .moderator_helpers import (  # noqa: F401 — fixtures
    COMPLETION_TOKENS, PROMPT_TOKENS, dsml_call, install_registry, mod,
    scripted_llm, tool_call,
)

#: Every method that calls ``get_active_method``.
_METHOD_CALLERS = (Moderator.generate_turn, Moderator._format_messages,
                   Moderator.generate_summary, Moderator.generate_conclusion)


class FakeMethod:
    """A discussion method whose hooks return fixed prompts."""

    def __init__(self, system: str = "", turn: str = "", output=None,
                 summary: str = "", conclusion: str = "") -> None:
        self.system, self.turn, self.output = system, turn, output
        self.summary, self.conclusion = summary, conclusion
        self.summary_args: tuple = ()

    def get_system_prompt(self, entity, discussion) -> str:
        return self.system

    def get_turn_prompt(self, entity, discussion) -> str:
        return self.turn

    def get_output_tool(self, entity, discussion):
        return self.output

    def filter_context_message(self, name, content, role, discussion,
                               current_entity_id=None) -> str:
        return "" if "secret" in content else content.upper()

    def get_summary_prompt(self, discussion, speaker, next_speaker) -> str:
        self.summary_args = (speaker, next_speaker)
        return self.summary

    def get_conclusion_prompt(self, discussion) -> str:
        return self.conclusion


def use_method(monkeypatch, method) -> None:
    """Make *method* the active discussion method everywhere it is read."""
    for anchor in _METHOD_CALLERS:
        patch_where_defined(monkeypatch, anchor, "get_active_method",
                            lambda discussion: method)


def system_of(payload: dict) -> str:
    return payload["messages"][0]["content"]


def task_of(payload: dict) -> str:
    return payload["messages"][-1]["content"]


class TestPromptSelection:
    async def test_entity_system_prompt_and_db_turn_prompt(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        resp = await moderator.generate_turn(ai)

        payload = scripted_llm.payloads[0]
        assert resp.content == scripted_llm.DEFAULT_REPLY
        assert system_of(payload) == "You are Alice."
        assert task_of(payload) == moderator.resolve_prompt(
            "participant", "ai", "turn", entity_name=ai.name,
            topic=disc.topic, participants=moderator._participant_names())
        assert payload["model"] == ai.ai_config.model
        assert payload["temperature"] == ai.ai_config.temperature
        assert "tools" not in payload

    async def test_devils_advocate_uses_its_db_prompts(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        ai.ai_config.system_prompt = ""
        await moderator.generate_turn(ai, participant_role="devils_advocate")

        kwargs = dict(entity_name=ai.name, topic=disc.topic,
                      participants=moderator._participant_names())
        payload = scripted_llm.payloads[0]
        expected_system = moderator.resolve_prompt(
            "participant", "ai", "system_devils_advocate", **kwargs)
        assert expected_system and system_of(payload) == expected_system
        assert task_of(payload) == moderator.resolve_prompt(
            "participant", "ai", "turn_devils_advocate", **kwargs)

    async def test_method_prompts_win(self, mod, scripted_llm, monkeypatch):
        moderator, disc, ai, human = mod
        use_method(monkeypatch, FakeMethod(system="M-SYS", turn="M-TURN"))
        await moderator.generate_turn(ai)
        assert system_of(scripted_llm.payloads[0]) == "M-SYS"
        assert task_of(scripted_llm.payloads[0]) == "M-TURN"

    async def test_fallback_task_without_prompt_rows(self, mod, scripted_llm, tmp_db):
        moderator, disc, ai, human = mod
        tmp_db.conn.execute("DELETE FROM prompts")
        await moderator.generate_turn(ai)
        assert task_of(scripted_llm.payloads[0]) == (
            f"It is your turn to speak as {ai.name}. Be concise.")

    async def test_entity_without_ai_config_raises(self, mod, scripted_llm):
        from consensus.models import ConfigurationError
        moderator, disc, ai, human = mod
        with pytest.raises(ConfigurationError, match="Profiles tab"):
            await moderator.generate_turn(human)
        assert scripted_llm.payloads == []


class TestContextFormatting:
    async def test_roles_prefixes_filter_and_order(self, mod, scripted_llm,
                                                   tmp_db, monkeypatch):
        moderator, disc, ai, human = mod
        tmp_db.add_message(disc.id, ai.id, "mine", "participant")
        tmp_db.add_message(disc.id, human.id, "theirs", "participant")
        tmp_db.add_message(disc.id, human.id, "a secret", "participant")
        use_method(monkeypatch, FakeMethod(turn="T"))
        await moderator.generate_turn(ai)

        messages = scripted_llm.payloads[0]["messages"]
        assert messages[1] == {"role": "user",
                               "content": f"Discussion topic: {disc.topic}"}
        assert messages[2:] == [
            {"role": "assistant", "content": "MINE"},
            {"role": "user", "content": f"[{human.name.upper()}]: THEIRS"},
            {"role": "user", "content": "T"},
        ]

    async def test_images_sent_to_vision_models(self, mod, scripted_llm,
                                                monkeypatch, tmp_db):
        moderator, disc, ai, human = mod
        images = [{"id": 1, "filename": "x.png"}]
        monkeypatch.setattr(tmp_db, "get_discussion_images", lambda d: images)
        patch_where_defined(monkeypatch, Moderator.generate_turn,
                            "is_vision_capable", lambda *a, **k: True)
        seen: list = []
        block = {"type": "image_url", "image_url": {"url": "data:x"}}
        patch_where_defined(monkeypatch, Moderator._format_messages,
                            "build_image_content_blocks",
                            lambda imgs: seen.append(imgs) or [block])
        await moderator.generate_turn(ai)

        assert seen == [images]
        assert scripted_llm.payloads[0]["messages"][2] == {
            "role": "user", "content": [block]}

    async def test_images_skipped_for_text_models(self, mod, scripted_llm,
                                                  monkeypatch, tmp_db):
        moderator, disc, ai, human = mod
        monkeypatch.setattr(tmp_db, "get_discussion_images",
                            lambda d: [{"id": 1}])
        patch_where_defined(monkeypatch, Moderator.generate_turn,
                            "is_vision_capable", lambda *a, **k: False)
        await moderator.generate_turn(ai)
        assert all(isinstance(m["content"], str)
                   for m in scripted_llm.payloads[0]["messages"])


class TestStructuredTurn:
    async def test_output_spec_routes_to_structured_turn(self, mod, scripted_llm,
                                                         monkeypatch):
        moderator, disc, ai, human = mod
        spec = object()
        method = FakeMethod(turn="T", output=spec)
        use_method(monkeypatch, method)
        calls: list = []

        async def fake_structured(client, cfg, entity, discussion, messages,
                                  output_spec, active_method):
            calls.append((entity, output_spec, active_method, messages))
            return AIResponse(content="structured")

        patch_where_defined(monkeypatch, Moderator.generate_turn,
                            "generate_structured_turn", fake_structured)
        registry = install_registry(moderator, "web_search")
        resp = await moderator.generate_turn(ai)

        assert resp.content == "structured"
        assert calls[0][:3] == (ai, spec, method)
        assert calls[0][3][-1]["content"] == "T"
        registry.get_tools_for_entity.assert_not_called()
        assert scripted_llm.payloads == []


class TestToolLoop:
    async def test_tool_call_then_answer(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        registry = install_registry(moderator, "web_search", "calc")
        scripted_llm.replies = [
            {"content": "thinking", "tool_calls": [
                tool_call("web_search", '{"query": "x"}', "c1"),
                tool_call("calc", "not json", "c2")]},
            {"content": "final"},
        ]
        resp = await moderator.generate_turn(ai)

        first, second = scripted_llm.payloads
        assert [t["function"]["name"] for t in first["tools"]] == [
            "web_search", "calc"]
        assert "tools: web_search, calc" in system_of(first)
        assert [c.kwargs["arguments"] for c in
                registry.execute.call_args_list] == [{"query": "x"}, {}]
        assert registry.execute.call_args.kwargs["caller_entity_id"] == ai.id
        assert second["messages"][-3]["tool_calls"][0]["id"] == "c1"
        assert second["messages"][-2:] == [
            {"role": "tool", "tool_call_id": "c1", "content": "results"},
            {"role": "tool", "tool_call_id": "c2", "content": "results"}]
        assert resp.content == "thinking\n\nfinal"
        assert [r.tool_name for r in resp.tool_calls] == ["web_search", "calc"]
        assert resp.prompt_tokens == 2 * PROMPT_TOKENS
        assert resp.total_tokens == 2 * (PROMPT_TOKENS + COMPLETION_TOKENS)

    async def test_progress_reports_failure(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        install_registry(moderator, "web_search",
                         result=ToolResult(content="bad", is_error=True))
        events: list[dict] = []
        moderator._progress_callback = events.append
        scripted_llm.replies = [{"content": "", "tool_calls": [
            tool_call("web_search")]}, {"content": "ok"}]
        resp = await moderator.generate_turn(ai)

        assert [e["message"] for e in events] == [
            "Using web_search...", "web_search failed — continuing..."]
        assert resp.tool_calls[0].is_error is True

    async def test_first_call_failure_falls_back_to_plain(self, mod, scripted_llm,
                                                          caplog):
        moderator, disc, ai, human = mod
        install_registry(moderator, "web_search")
        scripted_llm.replies = [400, {"content": "plain"}]
        with caplog.at_level(logging.WARNING):
            resp = await moderator.generate_turn(ai)

        assert resp.content == "plain"
        assert "does not support tool use" in resp.warning
        assert "tools" not in scripted_llm.payloads[1]
        assert "Falling back to plain completion" in caplog.text

    async def test_later_failure_raises(self, mod, scripted_llm):
        import httpx
        moderator, disc, ai, human = mod
        install_registry(moderator, "web_search")
        scripted_llm.replies = [
            {"content": "", "tool_calls": [tool_call("web_search")]}, 500]
        with pytest.raises(httpx.HTTPStatusError):
            await moderator.generate_turn(ai)

    async def test_dsml_calls_use_plain_messages(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        registry = install_registry(moderator, "web_search")
        scripted_llm.replies = [
            {"content": dsml_call("web_search", query="x")}, {"content": "done"}]
        resp = await moderator.generate_turn(ai)

        assert registry.execute.call_args.kwargs["arguments"] == {"query": "x"}
        tail = scripted_llm.payloads[1]["messages"][-2:]
        assert tail[0] == {"role": "assistant", "content": "(Calling tools...)"}
        assert tail[1]["role"] == "user"
        assert "[Tool: web_search] (OK)\nresults" in tail[1]["content"]
        assert resp.content == "done"

    async def test_exhaustion_forces_final_plain_response(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        install_registry(moderator, "web_search")
        call = {"content": "", "tool_calls": [tool_call("web_search")]}
        scripted_llm.replies = [call] * MAX_TOOL_ITERATIONS + [
            {"content": "summary of research"}]
        resp = await moderator.generate_turn(ai)

        payloads = scripted_llm.payloads
        assert len(payloads) == MAX_TOOL_ITERATIONS + 1
        assert "tools" not in payloads[MAX_TOOL_ITERATIONS - 1]
        assert payloads[MAX_TOOL_ITERATIONS - 1]["messages"][-1]["content"] == (
            "Tool use limit reached. Provide your final response now.")
        assert "completed your research" in task_of(payloads[-1])
        assert resp.content == "summary of research"
        assert resp.model == ai.ai_config.model
        assert len(resp.tool_calls) == MAX_TOOL_ITERATIONS
        assert resp.prompt_tokens == (MAX_TOOL_ITERATIONS + 1) * PROMPT_TOKENS

    async def test_exhaustion_keeps_interim_content(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        install_registry(moderator, "web_search")
        scripted_llm.replies = [
            {"content": f"note {i}", "tool_calls": [tool_call("web_search")]}
            for i in range(MAX_TOOL_ITERATIONS)]
        resp = await moderator.generate_turn(ai)

        assert len(scripted_llm.payloads) == MAX_TOOL_ITERATIONS
        assert resp.content == "\n\n".join(
            f"note {i}" for i in range(MAX_TOOL_ITERATIONS))

    async def test_final_plain_failure_is_logged(self, mod, scripted_llm, caplog):
        moderator, disc, ai, human = mod
        install_registry(moderator, "web_search")
        call = {"content": "", "tool_calls": [tool_call("web_search")]}
        scripted_llm.replies = [call] * MAX_TOOL_ITERATIONS + [500]
        with caplog.at_level(logging.WARNING):
            resp = await moderator.generate_turn(ai)
        assert resp.content == ""
        assert "Final plain completion failed" in caplog.text

    async def test_no_registry_tools_takes_simple_path(self, mod, scripted_llm):
        moderator, disc, ai, human = mod
        install_registry(moderator)
        resp = await moderator.generate_turn(ai)
        assert resp.content == scripted_llm.DEFAULT_REPLY
        assert "tools" not in scripted_llm.payloads[0]
        assert "You have access" not in system_of(scripted_llm.payloads[0])


def test_fake_method_shape_matches_hooks():
    """FakeMethod must stay in step with the hooks Moderator calls."""
    from consensus.methods.base import DiscussionMethod
    hooks = {n for n in vars(FakeMethod) if n.startswith(("get_", "filter_"))}
    assert hooks <= set(dir(DiscussionMethod)), hooks - set(dir(DiscussionMethod))
