"""Per-request BYOK API keys shared by ``ConsensusApp`` and its mixins.

Kept in its own module so the mixins can read it without importing
``consensus.app`` (which imports them).
"""

import contextvars

# Per-request BYOK API keys, isolated via contextvars (no cross-request leakage)
_request_api_keys_var: contextvars.ContextVar[dict[str, str]] = contextvars.ContextVar(
    "_request_api_keys_var", default={},
)

