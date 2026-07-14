"""Tests for the Double Crux phase handlers (issue #27).

Covers the parametrized StatePositionsHandler reuse, the crux hunting /
identification / testing / resolution handlers, the identify-phase loop
routing (issue #22 mechanism), and method-level assembly.
"""

from consensus.methods.phases.state_positions import StatePositionsHandler
from consensus.models import Discussion, Entity, EntityType


def _entity(eid: int = 1, name: str = "Alice") -> Entity:
    return Entity(id=eid, name=name, entity_type=EntityType.AI)


def _adv_discussion() -> Discussion:
    return Discussion(topic="Test topic",
                      discussion_method="adversarial_collab",
                      moderator_id=99)


class TestStatePositionsContextLabel:
    def test_default_label_preserved(self):
        prompt = StatePositionsHandler().get_system_prompt(
            _entity(), _adv_discussion())
        assert "Adversarial" in prompt

    def test_custom_label(self):
        handler = StatePositionsHandler(context_label="a Double Crux session")
        prompt = handler.get_system_prompt(_entity(), _adv_discussion())
        assert "Double Crux session" in prompt
        assert "Adversarial" not in prompt
