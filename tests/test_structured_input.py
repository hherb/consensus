from consensus.structured_input import schema_is_renderable, build_input_spec

POLL = {"type": "object", "properties": {
    "belief": {"type": "number", "minimum": 0, "maximum": 1},
    "reasoning": {"type": "string"}}, "required": ["belief", "reasoning"]}

ENUM = {"type": "object", "properties": {
    "stance": {"type": "string", "enum": ["updated", "unchanged"]}}}

ARRAY_OF_OBJ = {"type": "object", "properties": {
    "cruxes": {"type": "array", "items": {"type": "object", "properties": {
        "claim": {"type": "string"}, "belief": {"type": "number"}}}}}}

RESOLVED_BELIEFS = {"type": "object", "properties": {
    "beliefs": {"type": "object", "properties": {
        "H1": {"type": "number"}, "H2": {"type": "number"}}}}}

MATRIX = {"type": "object", "properties": {
    "ratings": {"type": "object", "additionalProperties": {
        "type": "object", "additionalProperties": {"type": "string"}}}}}


class TestRenderable:
    def test_primitive_form_is_renderable(self):
        assert schema_is_renderable(POLL) is True

    def test_enum_is_renderable(self):
        assert schema_is_renderable(ENUM) is True

    def test_array_of_objects_is_renderable(self):
        assert schema_is_renderable(ARRAY_OF_OBJ) is True

    def test_resolved_beliefs_are_renderable(self):
        assert schema_is_renderable(RESOLVED_BELIEFS) is True

    def test_nested_additionalproperties_not_renderable(self):
        assert schema_is_renderable(MATRIX) is False

    def test_empty_schema_not_renderable(self):
        assert schema_is_renderable({"type": "object", "properties": {}}) is False


class TestBuildInputSpec:
    def test_returns_none_without_method(self):
        assert build_input_spec(None, None, None) is None

    def test_build_input_spec_happy_path(self, tmp_db):
        from consensus.methods import get_method
        from tests.flow_e2e_helpers import start_method_discussion

        disc, moderator, pricing, mod, parts = start_method_discussion(
            tmp_db, "belief_diffusion", n_participants=2,
            topic="Is remote work more productive?")
        disc.method_state["hypotheses"] = ["Yes", "No"]
        disc.method_state["current_phase"] = "prior"
        method = get_method("belief_diffusion")
        spec = build_input_spec(method, parts[0], disc)
        assert spec is not None
        assert spec["tool_name"] == "submit_beliefs"
        assert spec["renderable"] is True
        assert set(spec["schema"]["properties"]["beliefs"]["properties"]) == {"H1", "H2"}

    def test_build_input_spec_returns_none_when_no_output_tool(self):
        class _NoToolMethod:
            def get_output_tool(self, entity, discussion):
                return None

        assert build_input_spec(_NoToolMethod(), object(), object()) is None
