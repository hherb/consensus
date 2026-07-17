"""Shared give-up/abort behaviour for anonymised generation phases.

``generate_ideas`` (NGT) and ``propose_thoughts`` (ToT) share the same
containment shape: hold the phase open until something is collected,
give up after a bounded number of rounds, and abort the whole method
(``next_phase -> None``) when generation produced nothing — every
later phase would be degenerate, consolidating and voting over an
empty list while burning API spend.  The shape lives here once,
parametrised by declarative class attributes, so fixes stay in sync.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from ..base import LINEAR_NEXT

if TYPE_CHECKING:
    from ...models import Discussion

logger = logging.getLogger(__name__)


class GenerationGiveUpMixin:
    """Advancement/abort policy for a bounded generation phase.

    Mix in ahead of :class:`..phase_handler.PhaseHandler` and declare:

    * ``giveup_state_key`` — ``method_state`` key holding the collected
      contributions (``"ideas"`` / ``"thoughts"``).
    * ``giveup_max_rounds`` — give up and advance after this many
      rounds without contributions.
    * ``giveup_generation_label`` — log label (``"Idea generation"``).
    * ``giveup_collected_noun`` — log noun (``"idea"`` / ``"thought"``).
    * ``giveup_method_short`` — method name for the abort log
      (``"NGT"`` / ``"Tree of Thoughts"``).
    * ``giveup_method_title`` — method title for the user-facing
      early-end message (``"Nominal Group Technique"``).
    * ``giveup_usable_noun`` — contribution noun for that message
      (``"ideas"`` / ``"approaches"``).
    * ``giveup_skipped_phases`` — later-phase list for that message
      (``"the clustering, clarification, and voting phases"``).
    """

    giveup_state_key: ClassVar[str]
    giveup_max_rounds: ClassVar[int]
    giveup_generation_label: ClassVar[str]
    giveup_collected_noun: ClassVar[str]
    giveup_method_short: ClassVar[str]
    giveup_method_title: ClassVar[str]
    giveup_usable_noun: ClassVar[str]
    giveup_skipped_phases: ClassVar[str]

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Reject subclasses missing a ``giveup_*`` declaration.

        The annotations above are the required attribute set; checking
        them at class definition turns a forgotten declaration into an
        import-time ``TypeError`` instead of an ``AttributeError`` on
        the rarest runtime path (give-up, after the round budget).
        """
        super().__init_subclass__(**kwargs)
        missing = [name for name in GenerationGiveUpMixin.__annotations__
                   if not hasattr(cls, name)]
        if missing:
            raise TypeError(
                f"{cls.__name__} must declare the GenerationGiveUpMixin "
                f"class attribute(s): {', '.join(missing)}")

    def should_advance(self, discussion: Discussion) -> bool:
        """Advance once anything was collected, or when out of rounds."""
        state = discussion.method_state
        phase_round = state.get("phase_round", 1)
        if phase_round > self.giveup_max_rounds:
            logger.warning(
                "%s reached round %d; advancing with %d %s(s) collected.",
                self.giveup_generation_label, phase_round,
                len(state.get(self.giveup_state_key, [])),
                self.giveup_collected_noun,
            )
            return True
        return bool(state.get(self.giveup_state_key)) and phase_round > 1

    def _gave_up(self, discussion: Discussion) -> bool:
        """True if generation exhausted its rounds without contributions."""
        state = discussion.method_state
        return (not state.get(self.giveup_state_key)
                and state.get("phase_round", 1) > self.giveup_max_rounds)

    def next_phase(self, discussion: Discussion) -> str | None:
        """Abort the method when generation produced nothing.

        Without contributions the remaining phases are degenerate —
        they would consolidate, rank, and vote over an empty list and
        burn API spend producing nothing usable.
        """
        if self._gave_up(discussion):
            logger.warning(
                "%s produced no %ss — ending the %s method early",
                self.giveup_generation_label, self.giveup_collected_noun,
                self.giveup_method_short,
            )
            return None
        return LINEAR_NEXT

    def get_method_complete_message(self, discussion: Discussion) -> str:
        """User-facing explanation when the method ended early."""
        if not self._gave_up(discussion):
            return ""
        return (
            f"⚠️ **{self.giveup_method_title} ended early.** The "
            "generation phase collected no usable "
            f"{self.giveup_usable_noun} after {self.giveup_max_rounds} "
            f"rounds, so {self.giveup_skipped_phases} were skipped.  "
            "Consider rephrasing the topic as an open 'How might we…' "
            "question and starting a new discussion."
        )
