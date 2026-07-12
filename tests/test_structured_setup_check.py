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
from consensus.moderator import Moderator


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
    @pytest.mark.skip(reason="enabled by the Delphi conversion task")
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
