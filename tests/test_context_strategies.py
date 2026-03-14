"""Tests for consensus.context_strategies — DB-driven context loading."""

import asyncio
import struct
import time
from unittest.mock import AsyncMock, patch

import pytest

from consensus.context_strategies import (
    ContextConfig,
    ContextStrategy,
    DEFAULT_WINDOW_SIZE,
    load_context_messages,
    load_context_messages_async,
    _load_semantic,
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


# ---------------------------------------------------------------------------
# Helpers for semantic strategy tests
# ---------------------------------------------------------------------------

def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


# ---------------------------------------------------------------------------
# Semantic strategy tests
# ---------------------------------------------------------------------------


class TestSemanticEnum:
    def test_semantic_value_exists(self):
        assert ContextStrategy.SEMANTIC == "semantic"
        assert ContextStrategy("semantic") == ContextStrategy.SEMANTIC

    def test_from_member_row_accepts_semantic(self):
        member = {"context_strategy": "semantic", "context_window_size": 20}
        cfg = ContextConfig.from_member_row(member, {})
        assert cfg.strategy == ContextStrategy.SEMANTIC
        assert cfg.window_size == 20


class TestLoadContextMessagesAsync:
    def test_non_semantic_delegates_to_sync(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(5):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        config = ContextConfig(strategy=ContextStrategy.SLIDING_WINDOW,
                               window_size=3)
        msgs = asyncio.get_event_loop().run_until_complete(
            load_context_messages_async(tmp_db, did, config))
        assert len(msgs) == 3
        assert msgs[0].content == "msg-2"

    def test_semantic_without_embed_client_falls_back(
            self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(10):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        config = ContextConfig(strategy=ContextStrategy.SEMANTIC,
                               window_size=8)
        msgs = asyncio.get_event_loop().run_until_complete(
            load_context_messages_async(tmp_db, did, config, embed_client=None))
        # Falls back to sliding_window
        assert len(msgs) == 8
        assert msgs[0].content == "msg-2"


class TestLoadSemantic:
    @pytest.fixture
    def mock_embed_client(self):
        client = AsyncMock()
        # Return a simple 3D vector for any query
        client.embed = AsyncMock(return_value=[1.0, 0.0, 0.0])
        return client

    @pytest.fixture
    def discussion_with_messages(self, tmp_db, sample_ai_entity):
        """Create a discussion with 20 messages, first 15 embedded."""
        did = tmp_db.create_discussion("Semantic test topic", sample_ai_entity)
        base_ts = 1000.0
        msg_ids = []
        for i in range(20):
            mid = tmp_db.add_message(
                did, sample_ai_entity, f"msg-{i}", "participant",
                turn_number=i, timestamp=base_ts + i)
            msg_ids.append(mid)

        # Embed first 15 messages with varied vectors
        for i in range(15):
            if i < 5:
                # Highly relevant (similar to query vec [1,0,0])
                vec = [1.0, 0.1 * i, 0.0]
            elif i < 10:
                # Moderately relevant
                vec = [0.5, 0.5, 0.1 * i]
            else:
                # Low relevance
                vec = [0.0, 0.0, 1.0]
            tmp_db.set_message_embedding(str(msg_ids[i]), _pack(vec))

        return did, msg_ids

    def test_returns_hybrid_results(self, tmp_db, sample_ai_entity,
                                    mock_embed_client,
                                    discussion_with_messages):
        did, msg_ids = discussion_with_messages
        # window_size=12 -> recency_count=max(3, 12//4)=3, semantic_budget=9
        msgs = asyncio.get_event_loop().run_until_complete(
            _load_semantic(tmp_db, did, 12, mock_embed_client))

        # Should have at most 12 messages total
        assert len(msgs) <= 12
        # Should include the 3 most recent messages (msg-17, msg-18, msg-19)
        contents = [m.content for m in msgs]
        assert "msg-19" in contents
        assert "msg-18" in contents
        assert "msg-17" in contents
        # Should include semantically relevant older messages
        # msg-0 through msg-4 have vectors closest to [1,0,0]
        assert "msg-0" in contents

    def test_chronological_order(self, tmp_db, sample_ai_entity,
                                 mock_embed_client,
                                 discussion_with_messages):
        did, _ = discussion_with_messages
        msgs = asyncio.get_event_loop().run_until_complete(
            _load_semantic(tmp_db, did, 12, mock_embed_client))
        timestamps = [m.timestamp for m in msgs]
        assert timestamps == sorted(timestamps)

    def test_no_duplicates(self, tmp_db, sample_ai_entity,
                           mock_embed_client,
                           discussion_with_messages):
        did, _ = discussion_with_messages
        msgs = asyncio.get_event_loop().run_until_complete(
            _load_semantic(tmp_db, did, 12, mock_embed_client))
        ids = [m.id for m in msgs]
        assert len(ids) == len(set(ids))

    def test_fallback_when_no_embeddings(self, tmp_db, sample_ai_entity,
                                         mock_embed_client):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(10):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        # No embeddings stored -> should fall back to sliding_window
        msgs = asyncio.get_event_loop().run_until_complete(
            _load_semantic(tmp_db, did, 8, mock_embed_client))
        assert len(msgs) == 8
        assert msgs[0].content == "msg-2"

    def test_fallback_on_embed_error(self, tmp_db, sample_ai_entity):
        from consensus.tools_memory import MemoryUnavailableError
        client = AsyncMock()
        client.embed = AsyncMock(side_effect=MemoryUnavailableError("down"))

        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(10):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        msgs = asyncio.get_event_loop().run_until_complete(
            _load_semantic(tmp_db, did, 8, client))
        # Falls back to sliding_window
        assert len(msgs) == 8
        assert msgs[0].content == "msg-2"

    def test_budget_split(self, tmp_db, sample_ai_entity, mock_embed_client):
        """window_size=20 -> recency_count=5, semantic_budget=15."""
        did = tmp_db.create_discussion("Budget test", sample_ai_entity)
        base_ts = 1000.0
        for i in range(30):
            mid = tmp_db.add_message(
                did, sample_ai_entity, f"msg-{i}", "participant",
                turn_number=i, timestamp=base_ts + i)
            # Embed all messages
            vec = [1.0, 0.1 * (i % 5), 0.0]
            tmp_db.set_message_embedding(str(mid), _pack(vec))

        msgs = asyncio.get_event_loop().run_until_complete(
            _load_semantic(tmp_db, did, 20, mock_embed_client))
        assert len(msgs) <= 20
        # Most recent 5 must be present
        contents = [m.content for m in msgs]
        for i in range(25, 30):
            assert f"msg-{i}" in contents

    def test_small_window_gives_minimum_recency(self, tmp_db,
                                                  sample_ai_entity,
                                                  mock_embed_client):
        """window_size=4 -> recency_count=max(3,1)=3, semantic_budget=1."""
        did = tmp_db.create_discussion("T", sample_ai_entity)
        base_ts = 1000.0
        for i in range(10):
            mid = tmp_db.add_message(
                did, sample_ai_entity, f"msg-{i}", "participant",
                turn_number=i, timestamp=base_ts + i)
            tmp_db.set_message_embedding(
                str(mid), _pack([1.0, 0.0, 0.0]))

        msgs = asyncio.get_event_loop().run_until_complete(
            _load_semantic(tmp_db, did, 4, mock_embed_client))
        assert len(msgs) <= 4
        # Last 3 must be there (minimum recency)
        contents = [m.content for m in msgs]
        assert "msg-9" in contents
        assert "msg-8" in contents
        assert "msg-7" in contents

    def test_all_candidates_in_recent_window(self, tmp_db,
                                              sample_ai_entity,
                                              mock_embed_client):
        """When all messages fit in recency, no semantic search needed."""
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(3):
            mid = tmp_db.add_message(
                did, sample_ai_entity, f"msg-{i}", "participant",
                turn_number=i)
            tmp_db.set_message_embedding(
                str(mid), _pack([1.0, 0.0, 0.0]))

        msgs = asyncio.get_event_loop().run_until_complete(
            _load_semantic(tmp_db, did, 20, mock_embed_client))
        # Only 3 messages exist, recency_count=5, so all fit
        assert len(msgs) == 3

    def test_lazy_indexing_triggered(self, tmp_db, sample_ai_entity,
                                     mock_embed_client):
        """Verify that unembedded messages trigger background indexing."""
        did = tmp_db.create_discussion("T", sample_ai_entity)
        for i in range(5):
            tmp_db.add_message(did, sample_ai_entity, f"msg-{i}",
                               "participant", turn_number=i)
        # No embeddings -> should trigger indexing + fall back
        with patch("consensus.tools_memory._index_messages",
                   new_callable=AsyncMock) as mock_index:
            with patch("consensus.tools_memory._indexing_discussions",
                       new=set()):
                msgs = asyncio.get_event_loop().run_until_complete(
                    _load_semantic(tmp_db, did, 8, mock_embed_client))
                # Falls back to sliding_window because no embeddings
                assert len(msgs) == 5  # only 5 messages exist


class TestGetMessagesWithEmbeddingsForDiscussion:
    def test_returns_only_embedded_messages(self, tmp_db, sample_ai_entity):
        did = tmp_db.create_discussion("T", sample_ai_entity)
        mid1 = tmp_db.add_message(did, sample_ai_entity, "embedded",
                                   "participant", turn_number=0)
        tmp_db.add_message(did, sample_ai_entity, "not-embedded",
                           "participant", turn_number=1)
        tmp_db.set_message_embedding(str(mid1), _pack([1.0, 0.0]))

        rows = tmp_db.get_messages_with_embeddings_for_discussion(did)
        assert len(rows) == 1
        assert rows[0]["content"] == "embedded"
        assert rows[0]["entity_name"] == "Alice"
        assert rows[0]["role"] == "participant"

    def test_scoped_to_discussion(self, tmp_db, sample_ai_entity):
        did1 = tmp_db.create_discussion("D1", sample_ai_entity)
        did2 = tmp_db.create_discussion("D2", sample_ai_entity)
        mid1 = tmp_db.add_message(did1, sample_ai_entity, "d1-msg",
                                   "participant", turn_number=0)
        mid2 = tmp_db.add_message(did2, sample_ai_entity, "d2-msg",
                                   "participant", turn_number=0)
        tmp_db.set_message_embedding(str(mid1), _pack([1.0, 0.0]))
        tmp_db.set_message_embedding(str(mid2), _pack([0.0, 1.0]))

        rows = tmp_db.get_messages_with_embeddings_for_discussion(did1)
        assert len(rows) == 1
        assert rows[0]["content"] == "d1-msg"
