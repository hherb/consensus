"""A failed ``method_state`` save leaves memory as the DB has it (issue #152).

``serialize_method_state`` raises on a value JSON cannot encode (#150).  Every
flow that saves ``method_state`` had already changed memory by then, so the
live session and a reload disagreed.  Each live save now goes through
``helpers.save_method_state``, which on that failure puts the DB-backed state
back as a reload would rebuild it and re-raises.  A method bug is the only way
to reach this, so the tests register one that writes a set.
"""

import json
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest

import consensus.methods as methods_registry
from consensus.app_discussion_flow import (
    generate_ai_turn,
    submit_human_message,
    submit_human_structured_message,
    switch_discussion_method,
)
from consensus.app_discussion_flow import helpers, method_switch
from consensus.app_discussion_flow.helpers import save_method_state
from consensus.app_discussion_flow.method_switch import (
    handle_triage_handoff, retry_method_switch,
)
from consensus.app_discussion_setup import add_to_discussion, start_discussion
from consensus.app_discussion_state import reopen_discussion
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
NARROW = "_unencodable_order"
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


class _KeyErrorInit(_BadInit):
    def init_state(self, discussion):
        return {}["missing"]


class _Narrowing(PhaseHandler):
    """Its turn order narrows the roster and records a value JSON cannot encode."""

    phase = Phase("n1", "N1", rounds=1)

    def get_system_prompt(self, entity, discussion):
        return ""

    def get_turn_prompt(self, entity, discussion):
        return ""

    def get_turn_order(self, entity_ids, discussion):
        discussion.method_state["votes"] = BAD
        return entity_ids[:1]


class _NarrowMethod(DiscussionMethod):
    display_name = "Unencodable order"
    description = "test"
    phase_handlers = (_Narrowing(),)


_Method.name = METHOD
_BadInit.name = BAD_INIT
_NarrowMethod.name = NARROW
KEY_INIT = "_key_error_init"
_KeyErrorInit.name = KEY_INIT


@pytest.fixture(autouse=True)
def _register(monkeypatch):
    monkeypatch.setitem(methods_registry._METHODS, METHOD, _Method)
    monkeypatch.setitem(methods_registry._METHODS, BAD_INIT, _BadInit)
    monkeypatch.setitem(methods_registry._METHODS, NARROW, _NarrowMethod)
    monkeypatch.setitem(methods_registry._METHODS, KEY_INIT, _KeyErrorInit)


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


def _system_notices(disc) -> list[str]:
    return [m.content for m in disc.messages if m.role == MessageRole.SYSTEM]


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


async def test_phase_transition_is_announced_once_saved(tmp_db):
    """Happy path: posting after the save still posts, and it is stored."""
    disc, _mod, _parts = _start(tmp_db, METHOD)
    await _complete_turn(disc, tmp_db)
    await _complete_turn(disc, tmp_db)

    assert disc.method_state["current_phase"] == "p2"
    assert _system_notices(disc)
    assert _system_notices(disc) == _system_notices(_reload(tmp_db, disc))


async def test_method_end_is_not_announced_when_unsaved(tmp_db):
    """``_complete_message_posted`` must not stay set without its notice."""
    disc, _mod, _parts = _start(tmp_db, METHOD)
    for _ in range(3):
        await _complete_turn(disc, tmp_db)
    notices = _system_notices(disc)
    disc.method_state["votes"] = BAD

    with pytest.raises(ValueError, match="method_state"):
        await _complete_turn(disc, tmp_db)

    assert "_complete_message_posted" not in disc.method_state
    assert _system_notices(disc) == notices
    _assert_matches_reload(tmp_db, disc)


@pytest.mark.parametrize("path", ["handoff", "retry"])
async def test_switch_keeps_the_stored_turn_order(tmp_db, path):
    """The new method's narrowed order is not installed when unsaved."""
    disc, _mod, parts = _start(tmp_db)
    disc.discussion_method = "triage"
    if path == "handoff":
        disc.method_state["chosen_method"] = NARROW
    else:
        disc.method_state["_pending_method_switch"] = {"target_method": NARROW}
    tmp_db.update_discussion(disc.id, discussion_method="triage",
                             method_state=json.dumps(disc.method_state))
    run = handle_triage_handoff if path == "handoff" else retry_method_switch

    with pytest.raises(ValueError, match="method_state"):
        run(disc, tmp_db, lambda: {})

    assert "votes" not in disc.method_state
    assert disc.turn_order == parts
    _assert_matches_reload(tmp_db, disc)


def test_reopen_keeps_the_stored_budget(tmp_db):
    """``_increase_budgets`` saves through the helper too."""
    disc, _mod, _parts = _start(tmp_db)
    disc.max_rounds, disc.status, disc.is_active = 2, "concluded", False
    tmp_db.update_discussion(disc.id, max_rounds=2, status="concluded")
    disc.method_state["votes"] = BAD

    with pytest.raises(ValueError, match="method_state"):
        reopen_discussion(disc, tmp_db)

    assert disc.max_rounds == 2
    assert "_continuation_count" not in disc.method_state
    _assert_matches_reload(tmp_db, disc)


def test_failed_reload_keeps_the_original_error(tmp_db, monkeypatch, caplog):
    """A DB fault during the reset is logged, not raised over the cause."""
    disc, _mod, _parts = _start(tmp_db)

    def broken(*_args):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(helpers, "discussion_from_db", broken)
    disc.method_state["votes"] = BAD

    with pytest.raises(ValueError, match="method_state"):
        save_method_state(disc, tmp_db)
    assert "not restored" in caplog.text


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


@pytest.mark.parametrize("method, error", [
    (BAD_INIT, ValueError), (KEY_INIT, KeyError),
], ids=["unencodable", "init-bug"])
def test_setup_leaves_no_live_discussion_behind(tmp_db, method, error):
    """A start whose method state cannot be built or saved starts nothing."""
    mod = tmp_db.add_entity("Mod", "human", "#000000")
    p1 = tmp_db.add_entity("P1", "human", "#111111")
    p2 = tmp_db.add_entity("P2", "human", "#111111")
    disc = Discussion(topic="t")
    for eid in (p1, p2):
        add_to_discussion(disc, tmp_db, eid)
    add_to_discussion(disc, tmp_db, mod, is_moderator=True)
    disc.discussion_method = method
    disc.pending_image_ids = [7]
    before = {d["id"] for d in tmp_db.get_discussions()}

    with pytest.raises(error):
        start_discussion(disc, tmp_db, Moderator(disc, tmp_db))

    assert disc.id is None
    assert disc.status == "setup"
    assert not disc.is_active
    # Every later get_state() serialises the discussion.
    assert "votes" not in disc.method_state
    json.dumps(disc.to_dict())
    # Setup uploads wait for the retried start, not the discarded row.
    assert disc.pending_image_ids == [7]
    assert {d["id"] for d in tmp_db.get_discussions()} == before
