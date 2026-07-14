"""Flow-level wiring for evidence-tracked phases (#28)."""
from consensus.app_discussion_flow import submit_human_message
from consensus.methods.base import Phase, ProcessedResponse
from consensus.models import Discussion, Entity


class _FakePhaseMethod:
    """Minimal active method exposing a track_evidence phase."""

    def __init__(self, track):
        self._phase = Phase(name="test_crux", display_name="Crux",
                            track_evidence=track)

    def current_phase(self, discussion):
        return self._phase

    def process_response(self, content, entity, discussion):
        return ProcessedResponse(display_content=content)


def _human_turn_discussion(db, phase="test_crux"):
    """An active discussion whose current speaker is a human."""
    eid = db.add_entity("Alice", "human", "#123456")
    alice = Entity.from_db_row(db.get_entity(eid))
    disc = Discussion(
        topic="t", entities=[alice],
        turn_order=[alice.id], base_turn_order=[alice.id],
        current_turn_index=0, turn_number=1,
        is_active=True, status="active", discussion_method="double_crux",
    )
    disc.id = db.create_discussion(disc.topic, alice.id)
    disc.method_state = {"current_phase": phase}
    return disc, alice


def test_human_turn_in_tracked_phase_is_logged(tmp_db, monkeypatch):
    disc, alice = _human_turn_discussion(tmp_db, phase="test_crux")
    monkeypatch.setattr(
        "consensus.app_discussion_flow.get_active_method",
        lambda d: _FakePhaseMethod(track=True))
    submit_human_message(disc, tmp_db, alice.id,
                         "It holds, see https://a.example/x")
    log = disc.method_state["evidence_log"]
    assert log and log[0]["grounded"] is True
    assert log[0]["sources"][0]["url"] == "https://a.example/x"


def test_human_turn_untracked_phase_no_log(tmp_db, monkeypatch):
    disc, alice = _human_turn_discussion(tmp_db, phase="positions")
    monkeypatch.setattr(
        "consensus.app_discussion_flow.get_active_method",
        lambda d: _FakePhaseMethod(track=False))
    submit_human_message(disc, tmp_db, alice.id, "Just my opinion.")
    assert "evidence_log" not in disc.method_state
