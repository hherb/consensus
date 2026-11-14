"""Moderator logic for managing discussions.

Split into a package by concern (issue #61): ``core`` holds ``Moderator``,
composed of the ``context``, ``turn`` and ``synthesis`` mixins;
``constants`` the generation settings.
"""

from .constants import (
    CONCLUSION_MAX_TOKENS, CONTEXT_MESSAGE_LIMIT, MEDIATION_MAX_TOKENS,
    MODERATOR_TEMPERATURE, SUMMARY_MAX_TOKENS,
)
from .core import Moderator

__all__ = [
    "CONCLUSION_MAX_TOKENS", "CONTEXT_MESSAGE_LIMIT", "MEDIATION_MAX_TOKENS",
    "MODERATOR_TEMPERATURE", "Moderator", "SUMMARY_MAX_TOKENS",
]
