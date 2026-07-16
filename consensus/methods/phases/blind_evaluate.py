"""Blind Logical Evaluation phase handler for Recursive Self-Distillation.

Participants evaluate the logical skeleton WITHOUT access to the
original discussion. Context filtering strips all prior messages;
the skeleton is delivered exclusively through the system prompt.
Participants rate each inference step for logical validity.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import OutputToolSpec, Phase, ProcessedResponse
from ..parsing import coerce_str
from ..phase_handler import PhaseHandler
from ._distillation_helpers import (
    VALIDITY_TOOL_PARAMETERS,
    extract_overall_score,
    extract_validity_scores,
    format_validity_scores_display,
    validate_validity_scores_payload,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity

logger = logging.getLogger(__name__)


class BlindEvaluateHandler(PhaseHandler):
    """Phase 3: Blind evaluation of logical validity."""

    phase = Phase(
        name="blind_evaluate",
        display_name="Blind Logical Evaluation",
        description=(
            "Participants evaluate the logical skeleton for pure validity "
            "without seeing the original discussion. Each inference step "
            "is rated on a 1-5 scale."
        ),
        rounds=1,
        allow_tools=False,
    )

    def init_state(self, discussion: Discussion) -> dict:
        return {
            "validity_scores": {},   # {inference_id: {entity_name: score}}
            "overall_scores": {},    # {entity_name: score}
        }

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        """Participants only — exclude moderator."""
        return [eid for eid in entity_ids if eid != discussion.moderator_id]

    # ------------------------------------------------------------------
    # Context filtering — enforce blindness
    # ------------------------------------------------------------------

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        """Strip pre-evaluation messages to ensure blind evaluation.

        The skeleton is delivered entirely through get_system_prompt.
        Messages from Phase 3 itself (containing validity tags or the
        phase transition marker) are preserved so participants can see
        prior evaluations within this phase. All earlier messages are
        blanked — empty assistant messages are skipped by the
        moderator's _build_context, and empty user messages carry no
        meaningful content.
        """
        # Keep Phase 3 messages: evaluations and phase transition
        if "[VALIDITY" in content.upper():
            return content
        if self.phase.display_name in content:
            return content
        return ""

    # ------------------------------------------------------------------
    # Prompts
    # ------------------------------------------------------------------

    @staticmethod
    def _eval_item_ids(skeleton: dict) -> list[str]:
        """Return IDs of all inference and conclusion steps to evaluate."""
        ids: list[str] = []
        for inf in skeleton.get("inferences", []):
            ids.append(inf["id"])
        for con in skeleton.get("conclusions", []):
            ids.append(con["id"])
        return ids

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        state = discussion.method_state
        skeleton_display = state.get("skeleton_display", "")

        if not skeleton_display:
            return (
                f"You are {entity.name}. The skeleton extraction failed. "
                "Please provide a qualitative assessment of the logical "
                "structure based on the discussion you observed."
            )

        skeleton = state.get("skeleton", {})
        eval_items = self._eval_item_ids(skeleton)
        items_str = ", ".join(eval_items)

        return (
            f"You are {entity.name}, a logical evaluator.\n\n"
            "You are evaluating a LOGICAL ARGUMENT for pure validity. "
            "You have NOT seen the original discussion — only the bare "
            "logical skeleton below. Judge ONLY whether each inference "
            "follows logically from its stated premises. Do not assess "
            "whether premises are true — only whether the reasoning "
            "from them is valid.\n\n"
            f"## Logical Skeleton\n\n{skeleton_display}\n\n"
            "## Scoring\n\n"
            "For each inference and conclusion, rate its logical "
            "validity on a 1-5 scale:\n"
            "- **5** = Airtight — the step follows necessarily from "
            "its premises\n"
            "- **4** = Strong — follows with high confidence, minor "
            "gap at most\n"
            "- **3** = Moderate — plausible but relies on unstated "
            "assumptions\n"
            "- **2** = Weak — significant logical gap or unsupported "
            "leap\n"
            "- **1** = Fallacious — does not follow from the stated "
            "premises at all\n\n"
            f"You must evaluate: {items_str}\n\n"
            "Submit your scores by calling the submit_validity_scores "
            "tool, with one entry per step (its id and a 1-5 score), "
            "an overall 1-5 score for the whole argument, and your "
            "assessment rationale in the 'reasoning' field."
        )

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        skeleton = discussion.method_state.get("skeleton", {})
        eval_items = self._eval_item_ids(skeleton)
        items_str = ", ".join(eval_items)

        return (
            f"{entity.name}, evaluate the logical skeleton above.\n\n"
            "For each inference and conclusion step, judge whether "
            "it follows logically from its stated dependencies, then "
            "call the submit_validity_scores tool with a score for "
            f"each step ({items_str}), an overall score, and your "
            "reasoning.\n\n"
            "Focus on the LOGIC, not the truth of the premises."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        return (
            f"A logical evaluation has been received. "
            f"Invite {next_speaker_name} to provide their independent "
            f"evaluation of the logical skeleton."
        )

    # ------------------------------------------------------------------
    # Response processing
    # ------------------------------------------------------------------

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        scores = extract_validity_scores(content)
        overall = extract_overall_score(content)

        if scores:
            for item_id, score in scores.items():
                state.setdefault("validity_scores", {}).setdefault(
                    item_id, {}
                )[entity.name] = score

        if overall is not None:
            state.setdefault("overall_scores", {})[entity.name] = overall

        # Append summary bar
        if scores or overall is not None:
            parts: list[str] = []
            for item_id, score in sorted(scores.items()):
                parts.append(f"{item_id}: {score}/5")
            if overall is not None:
                parts.append(f"Overall: {overall}/5")
            bar = "\n\n---\n**Validity scores:** " + " | ".join(parts)
            display = content + bar
        else:
            logger.warning(
                "Could not extract validity scores from %s's response",
                entity.name,
            )
            display = content

        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Structured output (issue #23)
    # ------------------------------------------------------------------

    requires_structured_output = True

    def get_output_tool(self, entity: Entity,
                        discussion: Discussion) -> OutputToolSpec | None:
        skeleton = discussion.method_state.get("skeleton")
        eval_items = self._eval_item_ids(skeleton) if skeleton else []
        if not eval_items:
            # Skeleton extraction failed (or produced nothing scorable):
            # no submit_validity_scores payload could pass validation,
            # so forcing the tool would burn every retry.  Fall through
            # to the free-text path, which asks for a qualitative
            # assessment instead (see get_system_prompt).
            return None
        items_str = ", ".join(eval_items)
        return OutputToolSpec(
            name="submit_validity_scores",
            description=("Submit a 1-5 logical validity score for each "
                         f"step ({items_str}), an overall 1-5 score "
                         "for the whole argument, and your assessment "
                         "rationale."),
            parameters=VALIDITY_TOOL_PARAMETERS,
        )

    def validate_output(self, payload: dict, entity: Entity,
                        discussion: Discussion) -> str:
        skeleton = discussion.method_state.get("skeleton") or {}
        eval_items = self._eval_item_ids(skeleton)
        return validate_validity_scores_payload(payload, eval_items)

    def process_structured_response(self, payload: dict, entity: Entity,
                                    discussion: Discussion) -> ProcessedResponse:
        state = discussion.method_state
        scores = {str(entry["inference_id"]).upper(): int(entry["score"])
                  for entry in payload["scores"]}
        overall = int(payload["overall"])

        for item_id, score in scores.items():
            state.setdefault("validity_scores", {}).setdefault(
                item_id, {}
            )[entity.name] = score
        state.setdefault("overall_scores", {})[entity.name] = overall

        reasoning = coerce_str(payload, "reasoning")
        display = format_validity_scores_display(scores, overall, reasoning)
        return ProcessedResponse(display_content=display)

    # ------------------------------------------------------------------
    # Transition
    # ------------------------------------------------------------------

    def get_transition_message(self, discussion: Discussion) -> str:
        state = discussion.method_state
        skeleton_display = state.get("skeleton_display", "")

        if not skeleton_display:
            return (
                f"**Phase: {self.phase.display_name}**\n\n"
                "Skeleton extraction was unsuccessful. Participants will "
                "provide qualitative logical assessments instead."
            )

        return (
            f"**Phase: {self.phase.display_name}**\n\n"
            "The logical skeleton has been extracted. Participants will "
            "now evaluate it for pure logical validity — WITHOUT access "
            "to the original discussion.\n\n"
            f"{skeleton_display}\n\n"
            "Each participant will independently rate every inference "
            "step on a 1-5 validity scale."
        )
