"""Tests for consensus.token_utils — lightweight token estimation."""

from consensus.token_utils import (
    estimate_tokens,
    estimate_message_tokens,
    estimate_messages_tokens,
)


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 1  # min 1

    def test_short_string(self):
        assert estimate_tokens("hi") == 1  # 2 chars // 4 = 0 -> max(1, 0) = 1

    def test_known_length(self):
        text = "a" * 100
        assert estimate_tokens(text) == 25  # 100 // 4

    def test_returns_int(self):
        assert isinstance(estimate_tokens("hello world"), int)


class TestEstimateMessageTokens:
    def test_simple_message(self):
        msg = {"role": "user", "content": "Hello"}
        tokens = estimate_message_tokens(msg)
        # 5 chars // 4 = 1 + 4 overhead = 5
        assert tokens == 5

    def test_empty_content(self):
        msg = {"role": "system", "content": ""}
        tokens = estimate_message_tokens(msg)
        # max(1,0) + 4 = 5
        assert tokens == 5

    def test_missing_content(self):
        msg = {"role": "user"}
        tokens = estimate_message_tokens(msg)
        assert tokens == 5  # empty string + overhead

    def test_multimodal_content(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe this image"},
                {"type": "image_url", "image_url": {"url": "data:..."}},
            ],
        }
        tokens = estimate_message_tokens(msg)
        # "Describe this image" = 19 chars // 4 = 4, + 4 overhead = 8
        assert tokens == 8


class TestEstimateMessagesTokens:
    def test_single_message(self):
        msgs = [{"role": "user", "content": "Hello"}]
        tokens = estimate_messages_tokens(msgs)
        # estimate_message_tokens("Hello") = 5, + 3 priming = 8
        assert tokens == 8

    def test_multiple_messages(self):
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        tokens = estimate_messages_tokens(msgs)
        # msg1: 16 chars // 4 = 4, + 4 = 8
        # msg2: 2 chars // 4 = max(1,0) = 1, + 4 = 5  (actually 2//4=0, max(1,0)=1, +4=5)
        # total: 8 + 5 + 3 = 16
        assert tokens == 16

    def test_empty_list(self):
        assert estimate_messages_tokens([]) == 3  # just priming
