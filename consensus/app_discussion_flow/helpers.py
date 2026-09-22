"""Shared helpers for active-discussion flow — pass detection, error
classification and description, transcript notices, cost, and turn-order
bookkeeping.

These are the leaf of the ``app_discussion_flow`` package: they depend on
no other flow module, so submissions, turns, method switching and
conclusion can all import from here without a cycle.  Most are pure;
``post_notice`` is the deliberate exception, since a notice has to reach
both the in-memory transcript and the database to be of any use.
"""

import json
import logging
import re

import httpx

from ..database import Database
from ..methods import get_active_method
from ..models import Discussion, Entity, Message, MessageRole

logger = logging.getLogger(__name__)

# Matches the *entire* formatted pass message ("*Name passed this round.*"),
# anchored so the phrase appearing inside a longer real contribution does not
# count as a pass.
_FORMATTED_PASS_RE = re.compile(r"^.+ passed this round\.$")

#: Maximum length of the provider error body echoed into a skip notice.
_ERROR_DETAIL_LENGTH = 200


#: ``error_kind`` values attached to failed-turn results.  ``provider``
#: means the model or the network misbehaved, ``config`` means this
#: deployment is set up wrong, and ``internal`` means Consensus itself has
#: a bug.  Only the last warrants asking the user to file a report, which
#: is the whole point of separating them (issue #74).
ERROR_KIND_PROVIDER = "provider"
ERROR_KIND_CONFIG = "config"
ERROR_KIND_INTERNAL = "internal"


def _unwrap_flow_error(e: Exception) -> Exception:
    """Return the underlying fault behind a wrapper exception.

    ``RecommenderError`` exists to give the classifier a single failure
    type, but it says nothing about *whose* fault the failure was — that
    is carried by its cause.  Classifying and describing the wrapper
    instead of the cause would report a provider outage as a Consensus
    bug and hide the provider's own error message.
    """
    from ..methods.recommender import RecommenderError

    if isinstance(e, RecommenderError) and e.__cause__ is not None:
        return e.__cause__
    return e


def classify_flow_error(e: Exception) -> str:
    """Classify a caught flow error as provider, config, or internal.

    ``generate_ai_turn`` and friends wrap large blocks that include method
    handlers, serialisation and DB writes, so a bare ``except Exception``
    cannot honestly call every failure an "API error" — a ``KeyError`` in
    a phase handler is a bug in Consensus, and telling the user their
    provider failed sends debugging the wrong way (issue #74).

    ``internal`` is the default bucket, and the classification is a
    heuristic rather than a partition: a provider fault can only be
    recognised here if it arrives as one of the listed types.  That is why
    ``AIClient`` raises :class:`~consensus.ai_response.AIResponseFormatError`
    at its parse sites — a gateway answering HTTP 200 with an HTML body
    would otherwise surface as an internal ``KeyError``.

    ``ConnectionError`` is listed rather than its ``OSError`` base on
    purpose: a tool reading a missing file also raises ``OSError``, and
    that is our problem, not the provider's.

    Returns one of :data:`ERROR_KIND_PROVIDER`, :data:`ERROR_KIND_CONFIG`
    or :data:`ERROR_KIND_INTERNAL`.
    """
    from ..ai_response import AIResponseFormatError
    from ..methods.recommender import RecommenderError
    from ..models import ConfigurationError
    from ..structured_output import StructuredOutputError

    e = _unwrap_flow_error(e)
    if isinstance(e, ConfigurationError):
        return ERROR_KIND_CONFIG
    if isinstance(
        e, (httpx.HTTPError, TimeoutError, ConnectionError,
            AIResponseFormatError, StructuredOutputError, RecommenderError),
    ):
        return ERROR_KIND_PROVIDER
    return ERROR_KIND_INTERNAL


def is_provider_error(e: Exception) -> bool:
    """Report whether ``e`` came from the provider or the network.

    Thin predicate over :func:`classify_flow_error` for the call sites
    that only need "is this our fault or theirs".
    """
    return classify_flow_error(e) == ERROR_KIND_PROVIDER


def describe_internal_error(e: Exception) -> str:
    """Return a user-facing description of a non-provider (internal) error.

    The exception type is always named: ``str(KeyError("positions"))`` is
    just ``'positions'``, which on its own tells a user nothing about what
    went wrong (issue #74).  The message is truncated on the same budget
    as a provider body, since an internal exception can carry a very long
    repr and it has to stay readable in a transcript notice and a toast.
    """
    message = str(e).strip()[:_ERROR_DETAIL_LENGTH].strip()
    name = type(e).__name__
    return f"{name}: {message}" if message else name


def describe_flow_error(e: Exception) -> str:
    """Describe any caught flow error, whatever its kind.

    Dispatches to :func:`describe_turn_error` for provider/network faults
    (which knows how to dig the actionable message out of an HTTP response
    body) and to :func:`describe_internal_error` otherwise.
    """
    unwrapped = _unwrap_flow_error(e)
    if is_provider_error(unwrapped):
        return describe_turn_error(unwrapped)
    return describe_internal_error(unwrapped)


#: Appended to a notice that could not be written to the database, so the
#: reader knows the transcript they are looking at will not match the one
#: they get back after a reload (golden rule 6).
UNSAVED_NOTICE_SUFFIX = (
    "\n\n*(This notice could not be saved — it will disappear when the "
    "discussion is reloaded. See the application log for details.)*"
)


def post_notice(
    discussion: Discussion, db: Database, entity: Entity, content: str,
    role: MessageRole = MessageRole.SYSTEM,
) -> tuple[Message, bool]:
    """Append a notice to the transcript and persist it best-effort.

    Notices exist to make a caught error visible (golden rule 6), so the
    persistence step must never be able to suppress them: when the error
    being reported *is* a database failure, ``db.add_message`` raises too
    and would otherwise replace the user-facing notice with a second
    exception escaping the handler (issue #74).  The in-memory message is
    appended first and always returned.

    A failed write is logged *and* disclosed in the notice itself, because
    the transcript is the durable record the rest of the flow relies on:
    silently showing a notice that vanishes on the next reload would just
    move the silent failure one step later.

    Returns:
        The appended message and whether it was persisted.
    """
    msg = Message(
        entity_id=entity.id, entity_name=entity.name,
        content=content, role=role,
    )
    discussion.messages.append(msg)
    try:
        db.add_message(
            discussion.id, entity.id, content, role.value,
            turn_number=discussion.turn_number,
        )
    except Exception:
        logger.exception(
            "Could not persist the %s notice for discussion %s",
            role.value, discussion.id,
        )
        msg.content += UNSAVED_NOTICE_SUFFIX
        return msg, False
    return msg, True


def describe_turn_error(e: Exception) -> str:
    """Return a user-facing description of a failed AI turn's error.

    ``str(httpx.HTTPStatusError)`` does not include the response body,
    which is where providers put the actionable message ("Insufficient
    balance", "tool_choice ... incompatible", ...) — so for HTTP errors
    the body is parsed (OpenAI-style ``{"error": {"message": ...}}``,
    with raw-snippet fallback) and prefixed with the status code.  Other
    exceptions use their message, falling back to the class name when
    empty (golden rule 6: caught errors must reach the UI).
    """
    if isinstance(e, httpx.HTTPStatusError):
        body = (e.response.text or "").strip()
        detail = body
        try:
            err = json.loads(body).get("error")
            if isinstance(err, dict):
                detail = err.get("message") or body
            elif isinstance(err, str):
                detail = err
        except (json.JSONDecodeError, AttributeError):
            pass
        prefix = f"HTTP {e.response.status_code}"
        detail = detail[:_ERROR_DETAIL_LENGTH].strip()
        return f"{prefix}: {detail}" if detail else prefix
    return str(e).strip() or type(e).__name__


def is_pass(content: str) -> bool:
    """Check if a participant's response is a pass (raw AI output or formatted).

    Recognises bracket notation ([PASS]), plain PASS, and the formatted
    '*Name passed this round.*' variant (which must be the whole message).
    """
    stripped = content.strip().strip("*_").strip()
    if stripped.upper() in ("[PASS]", "PASS"):
        return True
    # Match the formatted version only when it is the entire message, so a
    # participant mentioning the phrase mid-sentence is not misread as a pass.
    return bool(_FORMATTED_PASS_RE.match(stripped))


def calculate_discussion_cost(discussion: Discussion) -> float:
    """Sum the cost of all messages in the discussion."""
    return sum(m.cost or 0.0 for m in discussion.messages)


def method_roster(discussion: Discussion) -> list[int]:
    """Return the full eligible roster for method turn-order hooks.

    ``get_turn_order`` hooks must always receive the complete rotation
    roster from setup, never the current (possibly phase-narrowed) order —
    otherwise one phase's narrowing cascades into the next and can empty
    the turn order entirely (issue #13).  Falls back to the current order
    for discussions created before ``base_turn_order`` existed.
    """
    return list(discussion.base_turn_order or discussion.turn_order)


def apply_method_turn_order(
    discussion: Discussion, reset_index: bool = False,
) -> None:
    """Apply the active method's turn order for the current phase.

    Derives the order from the full roster and installs it only when it
    is non-empty and differs from the current order (resetting the turn
    index).  An empty result keeps the full roster instead — a discussion
    must never be left without speakers.

    ``reset_index=True`` restarts the rotation at index 0 even when the
    order is unchanged.  Pass it on every phase transition: condition-based
    phases can end mid-round, and a same-order successor phase would
    otherwise start at index k>0 with its first round truncated (issue #19).
    """
    roster = method_roster(discussion)
    method = get_active_method(discussion)
    new_order = roster
    if method:
        new_order = method.get_turn_order(roster, discussion) or roster
    if new_order and new_order != list(discussion.turn_order):
        discussion.turn_order = new_order
        discussion.current_turn_index = 0
        # Record the phase order in method_state so a reload restores it
        # instead of the setup roster (issue #16).  Underscore-prefixed
        # keys are internal bookkeeping, not method data.
        discussion.method_state["_turn_order"] = list(new_order)
    elif reset_index:
        discussion.current_turn_index = 0


def stamp_turn_index(discussion: Discussion) -> None:
    """Record the live turn index in method_state bookkeeping.

    ``load_discussion`` re-derives the index from the last participant
    message's position (+1), which cannot see the rotation reset a
    mid-round phase transition performs (issue #19) and would undo it
    after a crash/restart.  Stamping the index together with the turn it
    was recorded at lets a reload restore the exact live value when no
    participant has spoken since.  Underscore-prefixed keys are internal
    bookkeeping, not method data.
    """
    discussion.method_state["_turn_index"] = discussion.current_turn_index
    discussion.method_state["_turn_index_turn"] = discussion.turn_number

