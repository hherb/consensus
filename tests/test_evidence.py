"""Tests for evidence-tracked phases (#28)."""
from consensus.methods.base import Phase


class TestPhaseTrackEvidence:
    def test_defaults_to_false(self):
        p = Phase(name="x", display_name="X")
        assert p.track_evidence is False

    def test_can_opt_in(self):
        p = Phase(name="x", display_name="X", track_evidence=True)
        assert p.track_evidence is True
