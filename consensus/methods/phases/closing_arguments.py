"""Closing Arguments phase handler for Court of Law.

Each side summarizes their strongest points.  Teams with multiple
members privately huddle (up to 2 rounds) before a spokesperson
delivers the closing argument.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import Phase, ProcessedResponse
from ..phase_handler import PhaseHandler
from ._court_helpers import (
    HUDDLE_PREFIX,
    advance_huddle_state,
    auto_skip_solo_huddles,
    extract_spokesperson,
    filter_huddle_message,
    get_accusation_ids,
    get_accusation_label,
    get_defense_ids,
    get_huddle_state,
    get_team_for_entity,
    get_trial_type,
    huddle_turn_order,
    init_huddle_state,
)

if TYPE_CHECKING:
    from ...models import Discussion, Entity


class ClosingArgumentsHandler(PhaseHandler):
    """Phase 5: Closing arguments with optional team huddles."""

    phase = Phase(
        name="closing_arguments",
        display_name="Closing Arguments",
        description=(
            "Each side delivers a closing argument summarizing their "
            "strongest points.  Teams with multiple members may consult "
            "privately before a spokesperson delivers the closing."
        ),
        rounds=1,  # overridden by should_advance
    )

    _huddle_key = "closing_huddle"

    # ── State ─────────────────────────────────────────────────────────

    def init_state(self, discussion: Discussion) -> dict:
        return {self._huddle_key: init_huddle_state()}

    # ── Turn order ────────────────────────────────────────────────────

    def get_turn_order(self, entity_ids: list[int],
                       discussion: Discussion) -> list[int]:
        auto_skip_solo_huddles(discussion, self._huddle_key)
        huddle = get_huddle_state(discussion, self._huddle_key)
        if not huddle or huddle.get("sub_state") == "done":
            acc = get_accusation_ids(discussion)
            dfn = get_defense_ids(discussion)
            return acc + dfn
        return huddle_turn_order(discussion, self._huddle_key)

    # ── Round lifecycle ───────────────────────────────────────────────

    def on_round_complete(self, discussion: Discussion) -> None:
        advance_huddle_state(discussion, self._huddle_key)

    def should_advance(self, discussion: Discussion) -> bool:
        huddle = get_huddle_state(discussion, self._huddle_key)
        return huddle.get("sub_state") == "done"

    # ── Context filtering (huddle privacy) ────────────────────────────

    def filter_context_message(self, entity_name: str, content: str,
                               role: str,
                               discussion: Discussion, *,
                               current_entity_id: int | None = None) -> str:
        return filter_huddle_message(
            entity_name, content, discussion,
            current_entity_id=current_entity_id)

    # ── Prompts ───────────────────────────────────────────────────────

    def get_system_prompt(self, entity: Entity,
                          discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        trial = get_trial_type(discussion)
        team = get_team_for_entity(entity.id, discussion)
        huddle = get_huddle_state(discussion, self._huddle_key)
        sub = huddle.get("sub_state", "done")

        base = (
            f"You are {entity.name} in a Court of Law "
            f"{'criminal trial' if trial == 'criminal' else 'civil proceeding'}.\n"
            f"Case: {discussion.topic}\n\n"
        )

        if sub in ("accusation_huddle", "defense_huddle"):
            team_name = label if team == "accusation" else "Defense"
            return base + (
                f"TEAM HUDDLE — {team_name} Team (PRIVATE)\n\n"
                "You are consulting privately with your teammates.  "
                "The opposing side and the judge CANNOT see this "
                "conversation.\n\n"
                "Discuss your closing argument strategy:\n"
                "- What were your strongest pieces of evidence?\n"
                "- What weaknesses did the other side expose?\n"
                "- How should the closing address those weaknesses?\n"
                "- Who should deliver the closing?\n\n"
                "Indicate the spokesperson by including: "
                "SPOKESPERSON: [name]\n"
                "And outline the key points they should make."
            )

        if sub in ("accusation_speaks", "defense_speaks"):
            team_name = label if team == "accusation" else "Defense"
            if team == "accusation":
                return base + (
                    f"CLOSING ARGUMENT — {team_name}\n\n"
                    "Deliver your closing argument.  Address the judge:\n"
                    "- Summarize the strongest evidence supporting your case\n"
                    "- Address weaknesses exposed during cross-examination\n"
                    "- Explain why the charges/claims should be upheld\n"
                    "- Be compelling and conclusive"
                )
            return base + (
                f"CLOSING ARGUMENT — {team_name}\n\n"
                "Deliver your closing argument.  Address the judge:\n"
                "- Summarize the strongest points in your defense\n"
                "- Highlight failures in the prosecution's case\n"
                "- Address evidence presented against you\n"
                "- Argue why the charges/claims should be dismissed\n"
                "- Be compelling and conclusive"
            )

        return base + "CLOSING ARGUMENTS PHASE\n\n"

    def get_turn_prompt(self, entity: Entity,
                        discussion: Discussion) -> str:
        huddle = get_huddle_state(discussion, self._huddle_key)
        sub = huddle.get("sub_state", "done")
        label = get_accusation_label(discussion)
        team = get_team_for_entity(entity.id, discussion)
        team_name = label if team == "accusation" else "Defense"

        if sub in ("accusation_huddle", "defense_huddle"):
            return (
                f"{team_name} team member {entity.name}: consult with "
                "your teammates on closing argument strategy.  "
                "Include SPOKESPERSON: [name] to nominate a speaker."
            )
        return (
            f"{team_name} spokesperson ({entity.name}): deliver the "
            "closing argument.  Address the judge."
        )

    def get_summary_prompt(self, discussion: Discussion,
                           speaker_name: str,
                           next_speaker_name: str) -> str:
        huddle = get_huddle_state(discussion, self._huddle_key)
        sub = huddle.get("sub_state", "done")
        if sub in ("accusation_huddle", "defense_huddle"):
            return (
                f"{speaker_name} has contributed to the team consultation.  "
                f"Next: {next_speaker_name}."
            )
        return (
            f"{speaker_name} has delivered a closing argument.  "
            "As presiding judge, note the key arguments and their "
            f"effectiveness.  Next: {next_speaker_name}."
        )

    # ── Response processing ───────────────────────────────────────────

    def process_response(self, content: str, entity: Entity,
                         discussion: Discussion) -> ProcessedResponse:
        huddle = get_huddle_state(discussion, self._huddle_key)
        sub = huddle.get("sub_state", "done")

        if sub in ("accusation_huddle", "defense_huddle"):
            team = get_team_for_entity(entity.id, discussion)
            if team:
                nominee = extract_spokesperson(content, discussion, team)
                if nominee is not None:
                    huddle["spokesperson_id"] = nominee

            return ProcessedResponse(
                display_content=f"{HUDDLE_PREFIX}{content}")

        return ProcessedResponse(display_content=content)

    def get_transition_message(self, discussion: Discussion) -> str:
        label = get_accusation_label(discussion)
        acc_count = len(get_accusation_ids(discussion))
        def_count = len(get_defense_ids(discussion))
        parts = [f"**Phase: {self.phase.display_name}**\n"]
        if acc_count > 1 or def_count > 1:
            parts.append(
                "Teams with multiple members will huddle privately "
                "before a spokesperson delivers the closing argument.\n"
            )
        parts.append(
            f"The {label} will close first, followed by the Defense."
        )
        return "\n".join(parts)
