"""Tests for consensus.context_strategies — DB-driven context loading."""

import time

import pytest

from consensus.context_strategies import (
    ContextConfig,
    ContextStrategy,
    DEFAULT_WINDOW_SIZE,
    load_context_messages,
)
from consensus.models import MessageRole


class TestContextConfig:
    def test_defaults(self):
        cfg = ContextConfig()
        assert cfg.strategy == ContextStrategy.SLIDING_WINDOW
        assert cfg.window_size == DEFAULT_WINDOW_SIZE

    def test_from_member_row_uses_member_values(self):
        member = {"context_strategy": "full", "context_window_size": 50}
        discussion = {"default_context_strategy": "sliding_window",
                      "default_context_window_size": 20}
        cfg = ContextConfig.from_member_row(member, discussion)
        assert cfg.strategy == ContextStrategy.FULL
        assert cfg.window_size == 50

    def test_from_member_row_falls_back_to_discussion(self):
        member = {"context_strategy": None, "context_window_size": None}
        discussion = {"default_context_strategy": "summary",
                      "default_context_window_size": 10}
        cfg = ContextConfig.from_member_row(member, discussion)
        assert cfg.strategy == ContextStrategy.SUMMARY
        assert cfg.window_size == 10

    def test_from_member_row_unknown_strategy_falls_back(self):
        member = {"context_strategy": "nonexistent", "context_window_size": 5}
        discussion = {}
        cfg = ContextConfig.from_member_row(member, discussion)
        assert cfg.strategy == ContextStrategy.SLIDING_WINDOW
        assert cfg.window_size == 5

    def test_from_member_row_empty_dicts(self):
        cfg = ContextConfig.from_member_row({}, {})
        assert cfg.strategy == ContextStrategy.SLIDING_WINDOW
        assert cfg.window_size == DEFAULT_WINDOW_SIZE


class TestLoadFull:
    def test_returns_all_messages(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(5):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        config = ContextConfig(strategy=ContextStrategy.FULL)
        msgs = load_context_messages(tmp_db, did, config)
        assert len(msgs) == 5
        assert msgs[0].content == "msg-0"
        assert msgs[4].content == "msg-4"

    def test_empty_discussion(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        config = ContextConfig(strategy=ContextStrategy.FULL)
        msgs = load_context_messages(tmp_db, did, config)
        assert msgs == []


class TestLoadSlidingWindow:
    def test_returns_last_n_messages(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(10):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        config = ContextConfig(strategy=ContextStrategy.SLIDING_WINDOW,
                               window_size=3)
        msgs = load_context_messages(tmp_db, did, config)
        assert len(msgs) == 3
        assert msgs[0].content == "msg-7"
        assert msgs[2].content == "msg-9"

    def test_fewer_than_window(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(2):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        config = ContextConfig(strategy=ContextStrategy.SLIDING_WINDOW,
                               window_size=10)
        msgs = load_context_messages(tmp_db, did, config)
        assert len(msgs) == 2

    def test_default_window_size(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(30):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        config = ContextConfig()  # default sliding_window of 20
        msgs = load_context_messages(tmp_db, did, config)
        assert len(msgs) == DEFAULT_WINDOW_SIZE
        assert msgs[0].content == "msg-10"


class TestLoadSummary:
    def test_falls_back_to_full_when_within_window(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(3):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        config = ContextConfig(strategy=ContextStrategy.SUMMARY, window_size=10)
        msgs = load_context_messages(tmp_db, did, config)
        assert len(msgs) == 3
        assert all(m.role == MessageRole.PARTICIPANT for m in msgs)

    def test_falls_back_to_sliding_when_no_storyboard(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(10):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        config = ContextConfig(strategy=ContextStrategy.SUMMARY, window_size=3)
        msgs = load_context_messages(tmp_db, did, config)
        # No storyboard -> falls back to sliding window (last 3)
        assert len(msgs) == 3
        assert msgs[0].content == "msg-7"

    def test_includes_storyboard_summaries(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        base_ts = time.time()
        # Early messages with timestamps well before the recent ones
        for i in range(7):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i,
                               timestamp=base_ts - 100 + i)
        # Storyboard summaries for turns 1 and 2 (timestamps in the "old" range)
        tmp_db.add_storyboard_entry(did, 1, "Summary of turn 1",
                                     speaker_entity_id=sample_ai_entity)
        tmp_db.add_storyboard_entry(did, 2, "Summary of turn 2",
                                     speaker_entity_id=sample_ai_entity)
        # Patch storyboard timestamps to be in the old range (before recent messages)
        tmp_db._execute_write(
            "UPDATE storyboard_entries SET timestamp=? WHERE discussion_id=? AND turn_number=1",
            (base_ts - 99, did),
        )
        tmp_db._execute_write(
            "UPDATE storyboard_entries SET timestamp=? WHERE discussion_id=? AND turn_number=2",
            (base_ts - 98, did),
        )
        # Recent messages with timestamps near base_ts
        for i in range(7, 10):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i,
                               timestamp=base_ts - 10 + i)

        config = ContextConfig(strategy=ContextStrategy.SUMMARY, window_size=3)
        msgs = load_context_messages(tmp_db, did, config)

        # Should have storyboard summaries + 3 recent messages
        assert len(msgs) > 3
        # First messages should be summary synthetics
        summary_msgs = [m for m in msgs if "Summary of earlier discussion" in m.content]
        assert len(summary_msgs) == 2
        # Last 3 should be the recent verbatim messages
        assert msgs[-1].content == "msg-9"
        assert msgs[-3].content == "msg-7"


class TestDBQueries:
    def test_get_messages_windowed(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(5):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        rows = tmp_db.get_messages_windowed(did, 2)
        assert len(rows) == 2
        # Should be in chronological order
        assert rows[0]["content"] == "msg-3"
        assert rows[1]["content"] == "msg-4"

    def test_get_messages_windowed_with_offset(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(5):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        rows = tmp_db.get_messages_windowed(did, 2, offset=1)
        assert len(rows) == 2
        # Skip most recent, get next 2 most recent
        assert rows[0]["content"] == "msg-2"
        assert rows[1]["content"] == "msg-3"

    def test_get_messages_count(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        assert tmp_db.get_messages_count(did) == 0
        for i in range(3):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        assert tmp_db.get_messages_count(did) == 3

    def test_get_messages_windowed_empty(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        rows = tmp_db.get_messages_windowed(did, 5)
        assert rows == []


class TestUpdateMemberContextStrategy:
    def test_update_and_read_back(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity, is_moderator=True)
        tmp_db.update_member_context_strategy(
            did, sample_ai_entity, "full", 100,
        )
        member = tmp_db.get_discussion_member(did, sample_ai_entity)
        assert member["context_strategy"] == "full"
        assert member["context_window_size"] == 100

    def test_default_values(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.add_discussion_member(did, sample_ai_entity)
        member = tmp_db.get_discussion_member(did, sample_ai_entity)
        assert member["context_strategy"] == "sliding_window"
        assert member["context_window_size"] == 20


class TestDiscussionContextDefaults:
    def test_update_discussion_context_fields(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        tmp_db.update_discussion(
            did,
            default_context_strategy="summary",
            default_context_window_size=50,
        )
        disc = tmp_db.get_discussion(did)
        assert disc["default_context_strategy"] == "summary"
        assert disc["default_context_window_size"] == 50

    def test_discussion_defaults(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        disc = tmp_db.get_discussion(did)
        assert disc["default_context_strategy"] == "sliding_window"
        assert disc["default_context_window_size"] == 20
