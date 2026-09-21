"""Active discussion operations — message submission, turn management,
method switching, mediation and conclusion.

Split out of the former single ``app_discussion_flow.py`` (issue #61,
golden rule 8) into four cohesive layers plus their shared leaf helpers:

``helpers``
    Pure/near-pure utilities — pass detection, provider-error description,
    discussion cost, and turn-order bookkeeping. The leaf of the package:
    imported by ``submissions``, ``turns``, ``method_switch`` and
    ``conclusion``, and importing none of them, so the internal graph
    stays acyclic.
``submissions``
    Everything a *person* submits: free-text human turns, structured human
    payloads (#57), and human-moderator messages.
``turns``
    AI turn generation, turn completion (summary → storyboard → rotation →
    method round/phase lifecycle), and turn reassignment.
``method_switch``
    The Triage handoff: the recommender call, the switch itself with its
    tool-capability gate, and the blocked-switch recovery path.
``conclusion``
    Moderator mediation and final synthesis.

The public names are re-exported here, so ``from consensus.app_discussion_flow
import complete_turn`` keeps working exactly as before the split.

Patch targets in tests must name the *defining* submodule
(``consensus.app_discussion_flow.turns.get_active_method``), since that is
where the reference the code uses actually lives. Most stale targets fail
loudly — names the code imports but this facade does not re-export (such as
``get_active_method``) raise ``AttributeError`` when patched on the package.
The exceptions are the names that are both re-exported here *and* bound
inside a submodule: patching one of these on the package succeeds and
intercepts nothing, leaving a silently vacuous test. They are
``apply_method_turn_order``, ``calculate_discussion_cost``,
``describe_turn_error``, ``handle_triage_handoff``, ``is_pass`` and
``stamp_turn_index``.

Note also that ``helpers.apply_method_turn_order`` resolves
``get_active_method`` in the *helpers* namespace while ``complete_turn``
resolves it in *turns*, so patching only one of the two gives a test the fake
method for phase/round logic and the real one for turn-order computation.
Patch both when driving ``complete_turn``.

``tests/test_app_discussion_flow_facade.py`` guards the re-export list
itself: ``ConsensusApp`` reaches these functions by attribute access, so a
dropped name here is an ``AttributeError`` in production, not an import error.
"""

from .conclusion import conclude_discussion, mediate
from .helpers import (
    apply_method_turn_order,
    calculate_discussion_cost,
    describe_turn_error,
    is_pass,
    method_roster,
    stamp_turn_index,
)
from .method_switch import (
    handle_triage_handoff,
    refresh_ai_configs,
    retry_method_switch,
    switch_discussion_method,
)
from .submissions import (
    submit_human_message,
    submit_human_structured_message,
    submit_moderator_message,
)
from .turns import complete_turn, generate_ai_turn, reassign_turn

__all__ = [
    "apply_method_turn_order",
    "calculate_discussion_cost",
    "complete_turn",
    "conclude_discussion",
    "describe_turn_error",
    "generate_ai_turn",
    "handle_triage_handoff",
    "is_pass",
    "mediate",
    "method_roster",
    "reassign_turn",
    "refresh_ai_configs",
    "retry_method_switch",
    "stamp_turn_index",
    "submit_human_message",
    "submit_human_structured_message",
    "submit_moderator_message",
    "switch_discussion_method",
]
