"""Tests for the shared generation give-up mixin.

The mixin owns the advancement/abort containment shared by NGT's
generate phase and ToT's propose phase (HANDOVER dedup): advance once
anything was collected, give up after a bounded number of rounds, and
abort the whole method (``next_phase -> None``) when generation
produced nothing — every later phase would be degenerate.
"""

from consensus.methods.base import LINEAR_NEXT
from consensus.methods.phases._generation_giveup import GenerationGiveUpMixin


class _WidgetGeneration(GenerationGiveUpMixin):
    giveup_state_key = "widgets"
    giveup_max_rounds = 2
    giveup_generation_label = "Widget generation"
    giveup_collected_noun = "widget"
    giveup_method_short = "Widget"
    giveup_method_title = "Widget Method"
    giveup_usable_noun = "widgets"
    giveup_skipped_phases = "the polishing phases"


class _Discussion:
    """Minimal stand-in carrying only ``method_state``."""

    def __init__(self, state: dict):
        self.method_state = state


class TestShouldAdvance:
    def test_holds_on_first_round(self):
        disc = _Discussion({"widgets": [{"id": 1}], "phase_round": 1})
        assert _WidgetGeneration().should_advance(disc) is False

    def test_advances_once_collected_and_round_over_one(self):
        disc = _Discussion({"widgets": [{"id": 1}], "phase_round": 2})
        assert _WidgetGeneration().should_advance(disc) is True

    def test_holds_while_empty_within_budget(self):
        disc = _Discussion({"widgets": [], "phase_round": 2})
        assert _WidgetGeneration().should_advance(disc) is False

    def test_gives_up_after_max_rounds_even_when_empty(self):
        disc = _Discussion({"widgets": [], "phase_round": 3})
        assert _WidgetGeneration().should_advance(disc) is True


class TestAbortRouting:
    def test_next_phase_linear_when_something_collected(self):
        disc = _Discussion({"widgets": [{"id": 1}], "phase_round": 3})
        assert _WidgetGeneration().next_phase(disc) == LINEAR_NEXT

    def test_next_phase_none_when_gave_up_empty(self):
        disc = _Discussion({"widgets": [], "phase_round": 3})
        assert _WidgetGeneration().next_phase(disc) is None

    def test_no_give_up_before_budget_exhausted(self):
        disc = _Discussion({"widgets": [], "phase_round": 2})
        assert _WidgetGeneration().next_phase(disc) == LINEAR_NEXT


class TestCompleteMessage:
    def test_empty_when_not_given_up(self):
        disc = _Discussion({"widgets": [{"id": 1}], "phase_round": 3})
        assert _WidgetGeneration().get_method_complete_message(disc) == ""

    def test_names_method_and_skipped_phases_on_give_up(self):
        disc = _Discussion({"widgets": [], "phase_round": 3})
        message = _WidgetGeneration().get_method_complete_message(disc)
        assert "**Widget Method ended early.**" in message
        assert "no usable widgets after 2 rounds" in message
        assert "the polishing phases were skipped" in message
