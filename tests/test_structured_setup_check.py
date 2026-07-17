"""Setup-time tool-capability check for structured methods (issue #23).

Owner decision (2026-07-12): structured methods may require
tool-capable models; the failure must be a clear setup-time error,
never a silent degrade.  Unknown capability (local models) is allowed
through — the runtime path raises loudly instead.
"""

import time

import pytest

from consensus.app_discussion_setup import (
    _validate_structured_output_support,
    start_discussion,
)
from consensus.models import Entity
from consensus.moderator import Moderator
from consensus.structured_output import find_tool_blocked_entities


def _insert_model(tmp_db, model_id: str, supported: str) -> None:
    tmp_db.conn.execute(
        "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost,"
        " last_updated, input_modalities, context_length,"
        " supported_parameters) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (model_id, 0.0, 0.0, time.time(), "text", 8192, supported),
    )
    tmp_db.conn.commit()


def _start(disc, tmp_db):
    return start_discussion(disc, tmp_db, Moderator(disc, tmp_db))


class TestStructuredMethodSetupCheck:
    def test_blocks_model_without_tool_support(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc = discussion_with_entities
        disc.discussion_method = "delphi"

        result = _start(disc, tmp_db)

        assert "error" in result
        assert "test-model" in result["error"]
        assert "tool" in result["error"].lower()

    def test_allows_tool_capable_model(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,tools")
        disc = discussion_with_entities
        disc.discussion_method = "delphi"

        result = _start(disc, tmp_db)
        assert result.get("started") is True

    def test_allows_unknown_capability(
            self, tmp_db, discussion_with_entities, monkeypatch):
        # No pricing row at all — e.g. a local model
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        disc = discussion_with_entities
        disc.discussion_method = "delphi"

        result = _start(disc, tmp_db)
        assert result.get("started") is True

    def test_unstructured_method_never_blocks(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc = discussion_with_entities
        disc.discussion_method = "open_discussion"

        result = _start(disc, tmp_db)
        assert result.get("started") is True


def test_validate_helper_names_entity_and_method(
        tmp_db, discussion_with_entities, monkeypatch):
    import consensus.methods as methods_registry
    from consensus.methods.base import DiscussionMethod, Phase
    from consensus.methods.phase_handler import PhaseHandler

    class _H(PhaseHandler):
        phase = Phase("p", "P")
        requires_structured_output = True

        def get_system_prompt(self, entity, discussion):
            return ""

        def get_turn_prompt(self, entity, discussion):
            return ""

    class _M(DiscussionMethod):
        name = "_test_setup_check"
        display_name = "Setup Check Test"
        description = "test"
        phase_handlers = (_H(),)

    monkeypatch.setitem(methods_registry._METHODS,
                        "_test_setup_check", _M)
    monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
    _insert_model(tmp_db, "test/test-model", "temperature")
    disc = discussion_with_entities
    disc.discussion_method = "_test_setup_check"

    error = _validate_structured_output_support(disc, tmp_db)
    assert "Alice" in error and "test-model" in error


class TestFindToolBlockedEntities:
    """find_tool_blocked_entities returns structured offender info —
    the blocked-switch recovery dialog needs ALL offenders, not just
    the first (spec 2026-07-17)."""

    def _add_second_ai(self, tmp_db, disc, model: str) -> int:
        """Add a second AI member with the given model to the roster."""
        pid = tmp_db.add_provider("P2", "http://localhost:9999/v1", "")
        eid = tmp_db.add_entity(
            "Carol", "ai", "#00ffff", pid, model, 0.5, 512, "")
        disc.entities.append(Entity.from_db_row(tmp_db.get_entity(eid)))
        return eid

    def test_lists_all_blocked_ai_members(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test-model", "temperature,top_p")
        _insert_model(tmp_db, "test/other-model", "temperature,top_p")
        disc = discussion_with_entities
        carol_id = self._add_second_ai(tmp_db, disc, "test/other-model")
        disc.discussion_method = "delphi"

        blocked = find_tool_blocked_entities(disc, tmp_db)

        assert blocked == [
            {"entity_id": disc.entities[0].id, "name": "Alice",
             "model": "test-model"},
            {"entity_id": carol_id, "name": "Carol",
             "model": "test/other-model"},
        ]

    def test_empty_for_unstructured_method(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test-model", "temperature,top_p")
        disc = discussion_with_entities
        disc.discussion_method = "open_discussion"

        assert find_tool_blocked_entities(disc, tmp_db) == []

    def test_unknown_capability_not_blocked(
            self, tmp_db, discussion_with_entities, monkeypatch):
        """No pricing row (e.g. a local model) passes, as at setup."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        disc = discussion_with_entities
        disc.discussion_method = "delphi"

        assert find_tool_blocked_entities(disc, tmp_db) == []

    def test_explicit_method_name_overrides_discussion(
            self, tmp_db, discussion_with_entities, monkeypatch):
        """The prospective-switch case: discussion still holds the old
        method, the target is passed explicitly."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test-model", "temperature,top_p")
        disc = discussion_with_entities
        disc.discussion_method = "triage"

        blocked = find_tool_blocked_entities(disc, tmp_db, "delphi")

        assert len(blocked) == 1 and blocked[0]["name"] == "Alice"

    def test_error_string_names_all_offenders(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test-model", "temperature,top_p")
        _insert_model(tmp_db, "test/other-model", "temperature,top_p")
        disc = discussion_with_entities
        self._add_second_ai(tmp_db, disc, "test/other-model")
        disc.discussion_method = "delphi"

        error = _validate_structured_output_support(disc, tmp_db)

        assert "test-model" in error
        assert "test/other-model" in error
        assert "tool" in error.lower()
