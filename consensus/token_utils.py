"""Lightweight token estimation without external dependencies.

Uses a chars/4 heuristic which is a standard approximation for English
text across most tokenizers.  Not exact, but sufficient for budget
allocation where a safety margin is applied.
"""


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.  ~4 chars per token."""
    return max(1, len(text) // 4)


def estimate_message_tokens(message: dict) -> int:
    """Estimate tokens for a single OpenAI-format message dict.

    Accounts for role overhead (~4 tokens per message for
    role/separators).
    """
    content = message.get("content", "")
    if isinstance(content, list):
        # Multimodal content blocks — estimate text parts only
        text_parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        content = " ".join(text_parts)
    return estimate_tokens(content) + 4  # +4 for role/formatting overhead


def estimate_messages_tokens(messages: list[dict]) -> int:
    """Estimate total tokens for a list of OpenAI-format messages."""
    return sum(estimate_message_tokens(m) for m in messages) + 3  # +3 for priming
