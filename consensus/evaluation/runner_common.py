"""Discussion driving shared by the CLI (``runner.py``) and web
(``runner_db.py``) evaluation runners.

Both paths used to carry their own copy of the turn loop and the conclusion
lookup; the copies drifted, and the web path silently dropped turn and
synthesis failures (issue #71's fix reached only the CLI copy). Keep the
logic here so a run's error reporting is the same on both paths.
"""

import logging
from typing import Callable

from consensus.models import EntityType, Message, MessageRole

logger = logging.getLogger(__name__)

#: System prompt of the AI moderator every evaluation discussion uses.
MODERATOR_SYSTEM_PROMPT = (
    "You are a medical discussion moderator. Summarise each "
    "participant's contribution concisely. In the final "
    "synthesis, state the most likely diagnosis clearly, list "
    "the key supporting findings, and note any significant "
    "disagreements or alternative diagnoses raised."
)

#: Sampling and colour settings of the entities an evaluation run creates.
MODERATOR_TEMPERATURE = 0.5
PARTICIPANT_TEMPERATURE = 0.7
MODERATOR_COLOR = "#6b7280"
PARTICIPANT_COLOR = "#3b82f6"
#: Reply cap for a discussion turn, and for the single-call baseline, which
#: has to fit the whole analysis in one reply.
TURN_MAX_TOKENS = 1024
SINGLE_CHAT_MAX_TOKENS = 2048

#: Heading ``conclude_discussion`` puts on the moderator's final synthesis.
SYNTHESIS_MARKER = "Final Synthesis"


def discussion_topic(presentation: str) -> str:
    """The discussion topic for a clinical case vignette."""
    return (
        f"Clinical Case Discussion\n\n{presentation}\n\n"
        f"Please discuss this case systematically. Consider the key "
        f"findings, generate a differential diagnosis, and work toward "
        f"the most likely diagnosis with supporting reasoning."
    )


def extract_conclusion(messages: list[Message]) -> str:
    """The final synthesis, else the last moderator message, else ``""``.

    Matches on the moderator role, not a substring alone: a failure notice
    also mentions the "Final Synthesis" and must not be taken for one.
    """
    moderator = [m for m in messages if m.role == MessageRole.MODERATOR]
    for msg in reversed(moderator):
        if SYNTHESIS_MARKER in msg.content:
            return msg.content
    return moderator[-1].content if moderator else ""


def join_errors(*errors: str) -> str:
    """Join the non-empty error messages of one run."""
    return "; ".join(e for e in errors if e)


async def drive_turns(app, total_turns: int,
                      on_turn: Callable[[object, dict], None]) -> tuple[int, str]:
    """Run up to ``total_turns`` AI turns of a started discussion.

    ``on_turn(speaker, turn_result)`` records each generated contribution
    before the turn is completed. The loop stops early when no AI is due
    to speak, or at the first failure.

    Returns:
        ``(turns completed, error message or "")``.
    """
    for turn_idx in range(total_turns):
        current = app.discussion.current_speaker
        if not current or current.entity_type != EntityType.AI:
            return turn_idx, ""

        logger.info("  Turn %d/%d: %s speaking...",
                    turn_idx + 1, total_turns, current.name)
        turn_result = await app.generate_ai_turn()
        if "error" in turn_result:
            logger.warning("  Turn error: %s", turn_result["error"])
            return turn_idx, turn_result["error"]

        on_turn(current, turn_result)

        complete_result = await app.complete_turn()
        if "error" in complete_result:
            logger.warning("  Complete turn error: %s",
                           complete_result["error"])
            return turn_idx, (
                f"Turn completion failed: {complete_result['error']}")
    return total_turns, ""


async def conclude(app) -> tuple[str, str]:
    """Conclude the discussion.

    Returns:
        ``(conclusion text, error message or "")`` — a failed synthesis is
        an error even though a fallback conclusion is still returned.
    """
    result = await app.conclude_discussion()
    error = ""
    if result.get("conclusion_error"):
        error = f"Conclusion failed: {result['conclusion_error']}"
    return extract_conclusion(app.discussion.messages), error
