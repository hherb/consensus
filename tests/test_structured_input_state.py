"""Tests for current_input_spec exposure in ConsensusApp.get_state (#57).

When the current speaker is a human participant (not the moderator) and
the active phase declares a forced output tool, get_state() must expose
the tool schema under "current_input_spec" so the frontend can render a
form. In every other case (AI speaker, free-text phase, human
moderator) the key must be present but None.
"""

import pytest

from consensus.app import ConsensusApp


@pytest.fixture
def app(tmp_path):
    """Create a ConsensusApp with a temporary database."""
    db_path = str(tmp_path / "app_test.db")
    return ConsensusApp(db_path=db_path)


@pytest.fixture
def app_with_entities(app):
    """Create an app with a provider, AI moderator, AI participant, and
    human participant."""
    pid = app.db.add_provider("Local", "http://localhost:11434/v1", "")
    mod_id = app.db.add_entity("Moderator", "ai", "#aaa", pid, "llama3", 0.5, 512, "")
    p1_id = app.db.add_entity("Alice", "ai", "#bbb", pid, "llama3", 0.7, 1024, "")
    p2_id = app.db.add_entity("Bob", "human", "#ccc")

    app.add_to_discussion(mod_id, is_moderator=True)
    app.add_to_discussion(p1_id)
    app.add_to_discussion(p2_id)
    app.set_topic("Should AI be regulated?")
    return app, mod_id, p1_id, p2_id


def _drive_to_prior_phase(app, speaker_id):
    """Put the discussion in Belief Diffusion's 'prior' phase (which
    declares the submit_beliefs output tool) with speaker_id as the sole
    entry in the turn order."""
    app.discussion.discussion_method = "belief_diffusion"
    app.discussion.method_state["current_phase"] = "prior"
    app.discussion.method_state["hypotheses"] = ["Yes", "No"]
    app.discussion.turn_order = [speaker_id]
    app.discussion.current_turn_index = 0


class TestCurrentInputSpec:
    def test_present_for_human_participant_in_structured_phase(
        self, app_with_entities,
    ):
        app, mod_id, p1_id, p2_id = app_with_entities
        _drive_to_prior_phase(app, p2_id)  # Bob, human, not moderator

        spec = app.get_state()["current_input_spec"]

        assert spec is not None
        assert spec["tool_name"] == "submit_beliefs"
        assert spec["renderable"] is True
        assert "beliefs" in spec["schema"]["properties"]

    def test_none_for_ai_speaker(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        _drive_to_prior_phase(app, p1_id)  # Alice, AI

        assert app.get_state()["current_input_spec"] is None

    def test_none_for_freetext_method(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.discussion.discussion_method = "open_discussion"
        app.discussion.turn_order = [p2_id]  # Bob, human, not moderator
        app.discussion.current_turn_index = 0

        assert app.get_state()["current_input_spec"] is None

    def test_none_for_human_moderator(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        # Make Bob (human) the moderator so the "not the moderator" gate
        # is what's under test.
        app.discussion.moderator_id = p2_id
        _drive_to_prior_phase(app, p2_id)

        assert app.get_state()["current_input_spec"] is None


class TestPauseResumePreservesInputSpec:
    """Regression test for #57: pause_discussion/resume_discussion used to
    return discussion.to_dict(), which lacks get_state()-only fields such
    as current_input_spec. The web frontend's onStateUpdate replaces state
    wholesale, so resuming a paused structured-phase turn wiped the input
    spec and the schema-driven form fell back to the plain textarea."""

    def test_resume_returns_current_input_spec(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        _drive_to_prior_phase(app, p2_id)  # Bob, human, not moderator

        pause_result = app.pause_discussion()
        assert "error" not in pause_result
        assert pause_result.get("current_input_spec") is not None
        assert pause_result["current_input_spec"]["tool_name"] == "submit_beliefs"

        resumed = app.resume_discussion()
        assert "error" not in resumed
        assert resumed.get("current_input_spec") is not None
        assert resumed["current_input_spec"]["tool_name"] == "submit_beliefs"

    def test_reopen_returns_current_input_spec(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.start_discussion()
        _drive_to_prior_phase(app, p2_id)  # Bob, human, not moderator

        # Manually conclude the discussion to satisfy reopen's precondition
        app.discussion.status = "concluded"

        reopened = app.reopen_discussion()
        assert "error" not in reopened
        assert reopened.get("current_input_spec") is not None
        assert reopened["current_input_spec"]["tool_name"] == "submit_beliefs"
