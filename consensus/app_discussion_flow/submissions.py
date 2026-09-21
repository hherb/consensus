"""Human and moderator message submission.

Every path by which a *person* puts a message into an active discussion
lives here: free-text participant turns, structured-payload participant
turns (issue #57), and the human moderator's own messages.  All human
participant paths share one precondition gate
(:func:`_check_human_turn_preconditions`) so they cannot drift apart
(issue #59).
"""

import logging
from typing import TYPE_CHECKING

from ..database import Database
from ..evidence import record_and_annotate_evidence
from ..methods import get_active_method, serialize_method_state
from ..methods.parsing import check_payload_schema, extract_json_block
from ..models import Discussion, Entity, Message, MessageRole
from .helpers import is_pass

if TYPE_CHECKING:
    from ..methods.base import DiscussionMethod, OutputToolSpec

logger = logging.getLogger(__name__)


def _structured_freetext_unreadable(
    method: "DiscussionMethod", spec: "OutputToolSpec", entity: Entity,
    discussion: Discussion, raw_content: str,
) -> bool:
    """Decide whether an unchanged-state structured turn was truly unread.

    Called only when a structured-phase free-text turn left method_state
    byte-identical.  That can mean either (a) extraction found nothing (a
    genuine silent drop to surface, golden rule 6) or (b) a valid payload
    that recorded idempotently (e.g. Tree of Thoughts re-scoring the same
    values — no net change though it WAS read).  Distinguish by the
    payload's *structural* shape: a fenced JSON block whose data fields are
    present with the right primitive types/ranges is treated as understood;
    a missing block, a non-mapping, or one missing a required data key
    (parses, but wrong shape — e.g. a ``poll_belief`` submission with no
    ``belief`` key) is unreadable and must be surfaced, not dropped.

    Two things a *forced-tool* payload must satisfy are deliberately not
    enforced here:

    - ``reasoning`` — required by nearly every phase schema, and
      required here too by default: a free-text block that omits it is
      unreadable (#57 — a blanket exclusion let a valid-looking-but-
      unrecorded ``poll_belief`` submission slip through as a silent
      drop).  It is excluded from the check ONLY when the active
      handler sets ``reasoning_outside_block = True``, for phases whose
      free-text fallback routinely carries the rationale as prose
      *outside* the fenced block (e.g. score_thoughts.py's
      ``"My scores:\\n```json\\n"`` — the reasoning is the prefix, not a
      key inside the fence).  Requiring it inside the block there would
      misclassify an ordinary re-score as unreadable.
    - the handler's semantic ``validate_output`` — some handlers
      (``ScoreThoughtsHandler``) intentionally tolerate stale/pruned
      references in the free-text path (``record_thought_scores`` drops
      unknown labels silently) while rejecting the same payload in the
      strict forced-tool validator used to drive AI retries.  Consulting
      that stricter validator here would flag the free-text path's
      documented tolerance as a silent drop it is not.

    Residual: a structured phase whose free-text fallback is a non-JSON
    format (numbered lists, etc.) has no block to inspect here, so a valid
    submission that legitimately dedupes to a no-op is reported unreadable
    (a rare false positive on the legacy free-text path).  The primary
    input path is the structured form, which never reaches this branch.
    """
    block = extract_json_block(raw_content)
    if not isinstance(block, dict):
        return True
    schema = method.resolve_input_schema(spec, entity, discussion)
    required = schema.get("required", [])
    if method.reasoning_outside_block(discussion):
        required = [key for key in required if key != "reasoning"]
    return bool(check_payload_schema(block, {**schema, "required": required}))


def _check_human_turn_preconditions(
    discussion: Discussion, entity_id: int,
) -> Entity | dict:
    """Validate the shared preconditions for a human turn submission.

    Both human submit paths (``submit_human_message`` and
    ``submit_human_structured_message``) must reject the same conditions so
    one path cannot be tightened in isolation (issue #59): an unknown
    entity, a concluded discussion, or a turn that belongs to someone else.

    A *paused* discussion is deliberately NOT rejected here. The UI keeps
    the composer open while paused so a participant can interject before
    Resume (``onSendMessage``'s paused branch in
    ``static/discussion-actions.js``), and that path has always been
    allowed. What a paused interjection must not do is count as a method
    turn — see ``submit_human_message`` (skips method post-processing while
    paused) and ``submit_human_structured_message`` (rejects outright).

    A not-yet-started discussion needs no explicit check: ``turn_order`` is
    empty until start, so ``current_speaker`` is ``None`` and the turn check
    below rejects it.

    Returns the resolved :class:`Entity` on success, or an
    ``{"error": ...}`` dict for the caller to return verbatim.
    """
    entity = discussion.get_entity(entity_id)
    if not entity:
        return {"error": "Entity not found"}
    if discussion.status == "concluded":
        return {"error": "Discussion is not active"}
    current = discussion.current_speaker
    if not current or current.id != entity_id:
        return {"error": f"It's not {entity.name}'s turn"}
    return entity


def submit_human_message(
    discussion: Discussion, db: Database, entity_id: int, content: str,
) -> dict:
    """Submit a message from a human participant.

    Returns a dict with the message data, or an error dict if the entity
    is not found, the discussion has concluded, or it is not their turn.

    While the discussion is *paused* the message is recorded as a plain
    interjection: the method post-processing below is skipped. The turn does
    not advance while paused, so a participant can send more than once from
    the still-open composer — and re-running ``process_response`` for each
    send would let a handler record the same vote/estimate twice. Their
    actual method contribution is processed on their real turn after Resume,
    exactly once.
    """
    guard = _check_human_turn_preconditions(discussion, entity_id)
    if isinstance(guard, dict):
        return guard
    entity = guard

    # Method-specific response post-processing — human responses carry
    # method data too (votes, estimates, ...), exactly like AI turns.
    # Skipped while paused (see docstring): a paused send is chat, not a turn.
    method = get_active_method(discussion) if discussion.is_active else None
    if method and not is_pass(content):
        # Safety net for structured phases (#57): a phase that declares a
        # forced output tool expects free-text extraction to actually
        # write into method_state.  Snapshot before process_response and
        # compare after — if nothing changed, the turn *might* have been
        # silently dropped and must not vanish without a trace (golden
        # rule 6).  Non-structured phases are unaffected (spec is None,
        # no snapshot taken).
        #
        # A bare state-equality check is too blunt on its own: a resubmitted
        # (or re-scored-identically) fenced JSON payload can legitimately
        # leave method_state byte-identical — e.g. Tree of Thoughts'
        # re-score pass, where a participant's later scores happen to match
        # their earlier ones for the same thoughts, so ``thought_scores``
        # and ``scores_by_pass`` end up unchanged even though the payload
        # WAS read and recorded.  So the unchanged-state check does not
        # stop at *presence* of a JSON block either — a valid-but-wrong-
        # shape block (parses, but missing required keys, e.g. a
        # ``poll_belief`` submission with no ``belief`` key) would then
        # slip through unreported.  ``_structured_freetext_unreadable``
        # instead validates the extracted block's structural *shape*
        # against the phase's schema (required data keys present,
        # correct primitive types/ranges): a block that satisfies it is
        # treated as understood (idempotent resubmission, not an error);
        # an absent, non-mapping, or wrong-shape block is surfaced.  See
        # the helper's docstring for why it deliberately stops at
        # structural shape rather than the handler's full semantic
        # ``validate_output``.
        spec = method.get_output_tool(entity, discussion)
        before = (serialize_method_state(discussion.method_state)
                  if spec is not None else None)
        raw_content = content
        processed = method.process_response(content, entity, discussion)
        content = processed.display_content
        if (spec is not None
                and serialize_method_state(discussion.method_state) == before
                and _structured_freetext_unreadable(
                    method, spec, entity, discussion, raw_content)):
            # Structured phase, but free-text extraction recorded nothing
            # readable — surface it (golden rule 6) instead of the old
            # silent drop.
            logger.warning(
                "Structured phase: could not read %s's free-text turn as "
                "'%s' data.", entity.name, spec.name)
            return {"error": (
                f"This phase needs structured input. Your message could not "
                f"be read as '{spec.name}' data — please use the input form.")}
        phase = method.current_phase(discussion)
        if phase is not None and phase.track_evidence:
            content = record_and_annotate_evidence(
                discussion, entity, discussion.turn_number, content,
                tool_calls=[])
        if discussion.id:
            db.update_discussion(
                discussion.id,
                method_state=serialize_method_state(discussion.method_state),
            )

    msg = Message(
        entity_id=entity_id, entity_name=entity.name,
        content=content, role=MessageRole.PARTICIPANT,
    )
    discussion.messages.append(msg)
    db.add_message(
        discussion.id, entity_id, content, "participant",
        turn_number=discussion.turn_number,
    )
    return msg.to_dict()


def _record_structured_human_turn(
    discussion: Discussion, db: Database, entity: Entity,
    method: "DiscussionMethod", spec: "OutputToolSpec", payload: dict,
) -> dict:
    """Validate and record a human structured-turn payload.

    Mirrors the AI forced-tool branch (``generate_ai_turn``): a structural
    pre-check then the handler's semantic ``validate_output``, then
    ``process_structured_response`` writes into ``method_state``.  Returns
    the message dict, or ``{"error": ...}`` (recording nothing) on failure.
    """
    schema = method.resolve_input_schema(spec, entity, discussion)
    error = (check_payload_schema(payload, schema)
             or method.validate_output(payload, entity, discussion))
    if error:
        logger.warning("Rejected structured turn from %s: %s",
                       entity.name, error)
        return {"error": error}

    processed = method.process_structured_response(payload, entity, discussion)
    content = processed.display_content
    if discussion.id:
        db.update_discussion(
            discussion.id,
            method_state=serialize_method_state(discussion.method_state),
        )
    msg = Message(
        entity_id=entity.id, entity_name=entity.name,
        content=content, role=MessageRole.PARTICIPANT,
    )
    discussion.messages.append(msg)
    db.add_message(
        discussion.id, entity.id, content, "participant",
        turn_number=discussion.turn_number,
    )
    return msg.to_dict()


def submit_human_structured_message(
    discussion: Discussion, db: Database, entity_id: int, payload: dict,
) -> dict:
    """Submit a validated structured payload from a human participant (#57).

    The frontend form (or guided-JSON fallback) posts a typed ``payload``;
    it is validated and recorded on the same path an AI's forced tool call
    uses.  Returns the message dict or an error dict.

    Unlike the free-text path, this one also requires an *active*
    discussion. A structured payload is unambiguously a method turn — it
    writes into ``method_state`` — so it has no paused-interjection reading,
    and the form is never mounted while paused anyway (``updateInputArea``
    computes ``isActiveHumanTurn`` with ``status !== 'paused'``). The
    asymmetry with ``submit_human_message`` is deliberate, not drift.
    """
    guard = _check_human_turn_preconditions(discussion, entity_id)
    if isinstance(guard, dict):
        return guard
    entity = guard
    if not discussion.is_active:
        return {"error": "Discussion is not active"}
    method = get_active_method(discussion)
    if not method:
        return {"error": "No active discussion method"}
    spec = method.get_output_tool(entity, discussion)
    if spec is None:
        return {"error": "This phase does not take structured input."}
    return _record_structured_human_turn(
        discussion, db, entity, method, spec, payload)


def submit_moderator_message(
    discussion: Discussion, db: Database, content: str,
) -> dict:
    """Submit a message from the human moderator.

    Returns a dict with the message data, or an error dict if no moderator
    is configured.
    """
    mod = discussion.moderator
    if not mod:
        return {"error": "No moderator"}

    msg = Message(
        entity_id=mod.id, entity_name=mod.name,
        content=content, role=MessageRole.MODERATOR,
    )
    discussion.messages.append(msg)
    db.add_message(
        discussion.id, mod.id, content, "moderator",
        turn_number=discussion.turn_number,
    )
    return msg.to_dict()
