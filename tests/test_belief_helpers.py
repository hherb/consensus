"""Tests for belief_helpers module."""

from consensus.methods.phases._belief_helpers import expand_belief_schema


class TestExpandBeliefSchema:
    def test_expands_to_concrete_labels(self):
        schema = expand_belief_schema(["A", "B", "C"])
        beliefs = schema["properties"]["beliefs"]
        assert set(beliefs["properties"]) == {"H1", "H2", "H3"}
        assert beliefs["required"] == ["H1", "H2", "H3"]
        assert "additionalProperties" not in beliefs
        assert beliefs["properties"]["H1"]["type"] == "number"
        assert beliefs["properties"]["H1"]["maximum"] == 1.0

    def test_empty_hypotheses_yields_no_labels(self):
        beliefs = expand_belief_schema([])["properties"]["beliefs"]
        assert beliefs["properties"] == {}

    def test_does_not_mutate_the_template(self):
        from consensus.methods.phases._belief_helpers import (
            BELIEFS_TOOL_PARAMETERS)
        expand_belief_schema(["A"])
        assert "additionalProperties" in (
            BELIEFS_TOOL_PARAMETERS["properties"]["beliefs"])
