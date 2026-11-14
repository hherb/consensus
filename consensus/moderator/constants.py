"""Moderator generation settings."""

from ..context_strategies import DEFAULT_WINDOW_SIZE

# Legacy constant — kept for backward compatibility with tests/docs.
# Actual window size is now per-participant via ContextConfig.
CONTEXT_MESSAGE_LIMIT = DEFAULT_WINDOW_SIZE

# Moderator AI generation settings
MODERATOR_TEMPERATURE = 0.5
SUMMARY_MAX_TOKENS = 2048
CONCLUSION_MAX_TOKENS = 4096
MEDIATION_MAX_TOKENS = 2048
