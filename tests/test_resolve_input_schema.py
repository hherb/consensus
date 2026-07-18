from consensus.methods import get_method
from tests.flow_e2e_helpers import start_method_discussion


def test_default_hook_returns_spec_parameters():
    from consensus.methods.phase_handler import PhaseHandler
    from consensus.methods.base import OutputToolSpec

    class Dummy(PhaseHandler):
        phase = None
        def get_system_prompt(self, e, d): return ""
        def get_turn_prompt(self, e, d): return ""

    spec = OutputToolSpec(name="t", description="d",
                          parameters={"type": "object", "properties": {}})
    assert Dummy().resolve_input_schema(spec, None, None) == spec.parameters


def test_belief_method_expands_dynamic_keys(tmp_db):
    disc, moderator, pricing, mod, parts = start_method_discussion(
        tmp_db, "belief_diffusion", n_participants=2,
        topic="Is remote work more productive?")
    disc.method_state["hypotheses"] = ["Yes", "No"]
    disc.method_state["current_phase"] = "prior"
    method = get_method("belief_diffusion")
    entity = parts[0]
    spec = method.get_output_tool(entity, disc)
    schema = method.resolve_input_schema(spec, entity, disc)
    assert set(schema["properties"]["beliefs"]["properties"]) == {"H1", "H2"}
