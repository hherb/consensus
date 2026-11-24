"""A failed ``method_state`` save leaves memory as the DB has it (issue #152).

``serialize_method_state`` raises on a value JSON cannot encode (#150).  Every
flow that saves ``method_state`` had already changed memory by then, so the
live session and a reload disagreed.  Each save now goes through
``helpers.save_method_state``, which on that failure puts the DB-backed state
back as a reload would rebuild it and re-raises.  A method bug is the only way
to reach this, so the tests register one that writes a set.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import consensus.methods as methods_registry
from consensus.app_discussion_flow import (
    generate_ai_turn,
    submit_human_message,
    submit_human_structured_message,
    switch_discussion_method,
)
from consensus.app_discussion_flow import method_switch
from consensus.app_discussion_flow.method_switch import handle_triage_handoff
from consensus.app_discussion_setup import add_to_discussion, start_discussion
from consensus.methods.base import DiscussionMethod, Phase
from consensus.methods.phase_handler import PhaseHandler
from consensus.methods.phase_types import OutputToolSpec, ProcessedResponse
from consensus.models import Discussion, MessageRole
from consensus.moderator import Moderator
from consensus.pricing import PricingCache

from tests.test_app_discussion_rotation import _complete_turn, _reload, _start

BAD = {1, 2}
METHOD = "_unencodable"
BAD_INIT = "_unencodable_init"
SPEC = OutputToolSpec(
    "submit_x", "test", {"type": "object",
                         "properties": {"x": {"type": "string"}},
                         "required": ["x"]})


class _Recording(PhaseHandler):
    """First phase: every response it records is one JSON cannot encode."""

    phase = Phase("p1", "P1", rounds=1)
    spec = None

    def get_system_prompt(self, entity, discussion):
        return ""

    def get_turn_prompt(self, entity, discussion):
        return ""

    def get_output_tool(self, entity, discussion):
        return self.spec

    def process_response(self, content, entity, discussion):
        discussion.method_state["votes"] = BAD
        return ProcessedResponse(display_content=content)

    def process_structured_response(self, payload, entity, discussion):
        return self.process_response(json.dumps(payload), entity, discussion)


class _Next(PhaseHandler):
    phase = Phase("p2", "P2", rounds=1)

    def get_system_prompt(self, entity, discussion):
        return ""

    def get_turn_prompt(self, entity, discussion):
        return ""


class _Method(DiscussionMethod):
    display_name = "Unencodable"
    description = "test"
    phase_handlers = (_Recording(), _Next())


class _BadInit(DiscussionMethod):
    display_name = "Unencodable init"
    description = "test"
    phase_handlers = (_Next(),)

    def init_state(self, discussion):
        return {**super().init_state(discussion), "votes": BAD}


_Method.name = METHOD
_BadInit.name = BAD_INIT


@pytest.fixture(autouse=True)
def _register(monkeypatch):
    monkeypatch.setitem(methods_registry._METHODS, METHOD, _Method)
    monkeypatch.setitem(methods_registry._METHODS, BAD_INIT, _BadInit)


def _stored(db, disc) -> dict:
    return json.loads(db.get_discussion(disc.id)["method_state"])


def _assert_matches_reload(db, disc) -> None:
    """Memory holds exactly what a reload rebuilds from the DB."""
    fresh = _reload(db, disc)
    assert disc.method_state == fresh.method_state
    assert disc.discussion_method == fresh.discussion_method
    assert disc.turn_order == fresh.turn_order
    assert disc.current_turn_index == fresh.current_turn_index
    assert disc.turn_number == fresh.turn_number
    assert disc.status == fresh.status
    assert ([m.content for m in disc.messages]
            == [m.content for m in fresh.messages])


def _ai_response():
    resp = MagicMock()
    resp.content = "My contribution."
    resp.model = "test-model"
    resp.prompt_tokens = resp.completion_tokens = resp.total_tokens = 1
    resp.latency_ms = 1
    resp.tool_calls = []
    resp.warning = None
    resp.structured_output = None
    resp.finish_reason = "stop"
    return resp


async def test_ai_turn_is_skipped_without_recording_the_vote(tmp_db):
    """The turn is reported skipped, so its vote must not stay in memory."""
    mod = tmp_db.add_entity("Mod", "human", "#000000")
    ai = tmp_db.add_entity("AI", "ai", "#111111")
    human = tmp_db.add_entity("P", "human", "#222222")
    disc = Discussion(topic="t")
    add_to_discussion(disc, tmp_db, mod, is_moderator=True)
    add_to_discussion(disc, tmp_db, ai)
    add_to_discussion(disc, tmp_db, human)
    disc.discussion_method = METHOD
    assert "error" not in start_discussion(disc, tmp_db, Moderator(disc, tmp_db))
    assert disc.current_speaker.id == ai
    moderator = Moderator(disc, tmp_db)
    moderator.generate_turn = AsyncMock(return_value=_ai_response())
    moderator.prompt_id = MagicMock(return_value=None)

    result = await generate_ai_turn(
        disc, moderator, tmp_db, PricingCache(tmp_db.conn, tmp_db._lock))

    assert result["skipped"] is True
    assert result["error_kind"] == "internal"
    assert "votes" not in disc.method_state
    # Not _assert_matches_reload: the skip notice is a participant message,
    # so a reload already shows the turn advanced; complete_turn does that.
    assert disc.method_state == _stored(tmp_db, disc)


async def test_phase_transition_is_not_announced_when_unsaved(tmp_db):
    """The transition message is posted only after the state is saved."""
    disc, _mod, _parts = _start(tmp_db, METHOD)
    await _complete_turn(disc, tmp_db)
    disc.method_state["votes"] = BAD

    with pytest.raises(ValueError, match="method_state"):
        await _complete_turn(disc, tmp_db)

    assert disc.method_state["current_phase"] == "p1"
    assert not any(m.role == MessageRole.SYSTEM for m in disc.messages)
    _assert_matches_reload(tmp_db, disc)


async def test_method_switch_keeps_the_old_method(tmp_db):
    disc, _mod, _parts = _start(tmp_db)

    with pytest.raises(ValueError, match="method_state"):
        switch_discussion_method(disc, tmp_db, BAD_INIT)

    assert disc.discussion_method == "open_discussion"
    _assert_matches_reload(tmp_db, disc)


async def test_blocked_handoff_posts_nothing_when_unsaved(tmp_db, monkeypatch):
    disc, _mod, _parts = _start(tmp_db)
    disc.discussion_method = "triage"
    disc.method_state["chosen_method"] = METHOD
    tmp_db.update_discussion(disc.id, discussion_method="triage",
                             method_state=json.dumps(disc.method_state))
    disc.method_state["votes"] = BAD
    monkeypatch.setattr(method_switch, "switch_discussion_method",
                        lambda *a: {"error": "blocked"})

    with pytest.raises(ValueError, match="method_state"):
        handle_triage_handoff(disc, tmp_db, lambda: {})

    assert "_switch_error_posted" not in disc.method_state
    assert not any(m.role == MessageRole.SYSTEM for m in disc.messages)
    _assert_matches_reload(tmp_db, disc)


@pytest.mark.parametrize("spec", [None, SPEC], ids=["free-text", "with-spec"])
async def test_human_free_text_turn_is_not_recorded(tmp_db, monkeypatch, spec):
    """A resend must not stack the mutation again (#152)."""
    monkeypatch.setattr(_Recording, "spec", spec)
    disc, _mod, (p1, _p2) = _start(tmp_db, METHOD)

    for _ in range(2):
        with pytest.raises(ValueError, match="method_state"):
            submit_human_message(disc, tmp_db, p1, '{"x": "hi"}')
        assert "votes" not in disc.method_state
    _assert_matches_reload(tmp_db, disc)


async def test_human_structured_turn_is_not_recorded(tmp_db, monkeypatch):
    monkeypatch.setattr(_Recording, "spec", SPEC)
    disc, _mod, (p1, _p2) = _start(tmp_db, METHOD)

    with pytest.raises(ValueError, match="method_state"):
        submit_human_structured_message(disc, tmp_db, p1, {"x": "hi"})

    assert "votes" not in disc.method_state
    _assert_matches_reload(tmp_db, disc)


def test_setup_leaves_no_live_discussion_behind(tmp_db):
    """A start that cannot save its initial state starts nothing."""
    mod = tmp_db.add_entity("Mod", "human", "#000000")
    p1 = tmp_db.add_entity("P1", "human", "#111111")
    p2 = tmp_db.add_entity("P2", "human", "#111111")
    disc = Discussion(topic="t")
    for eid in (p1, p2):
        add_to_discussion(disc, tmp_db, eid)
    add_to_discussion(disc, tmp_db, mod, is_moderator=True)
    disc.discussion_method = BAD_INIT
    before = {d["id"] for d in tmp_db.get_discussions()}

    with pytest.raises(ValueError, match="method_state"):
        start_discussion(disc, tmp_db, Moderator(disc, tmp_db))

    assert disc.id is None
    assert disc.status == "setup"
    assert not disc.is_active
    assert {d["id"] for d in tmp_db.get_discussions()} == before
