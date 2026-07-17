"""Tests for parsing utilities."""

from consensus.methods.parsing import check_payload_schema

POLL_SCHEMA = {
    "type": "object",
    "properties": {
        "belief": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
    },
    "required": ["belief", "reasoning"],
}


class TestCheckPayloadSchema:
    def test_accepts_valid_payload(self):
        assert check_payload_schema(
            {"belief": 0.7, "reasoning": "because"}, POLL_SCHEMA) == ""

    def test_missing_required_field_named(self):
        msg = check_payload_schema({"reasoning": "x"}, POLL_SCHEMA)
        assert "belief" in msg

    def test_number_out_of_range(self):
        msg = check_payload_schema(
            {"belief": 5, "reasoning": "x"}, POLL_SCHEMA)
        assert msg != ""

    def test_wrong_type(self):
        msg = check_payload_schema(
            {"belief": "high", "reasoning": "x"}, POLL_SCHEMA)
        assert "belief" in msg

    def test_enum_rejects_unknown_value(self):
        schema = {"type": "object",
                  "properties": {"stance": {"type": "string",
                                            "enum": ["updated", "unchanged"]}},
                  "required": ["stance"]}
        assert check_payload_schema({"stance": "updated"}, schema) == ""
        assert check_payload_schema({"stance": "maybe"}, schema) != ""

    def test_array_of_objects_recurses(self):
        schema = {"type": "object", "properties": {
            "cruxes": {"type": "array", "items": {"type": "object",
                       "properties": {"claim": {"type": "string"},
                                      "belief": {"type": "number",
                                                 "minimum": 0, "maximum": 1}},
                       "required": ["claim", "belief"]}}},
            "required": ["cruxes"]}
        assert check_payload_schema(
            {"cruxes": [{"claim": "c", "belief": 0.5}]}, schema) == ""
        assert check_payload_schema(
            {"cruxes": [{"claim": "c", "belief": 9}]}, schema) != ""

    def test_object_additionalproperties_values_checked(self):
        schema = {"type": "object", "properties": {
            "beliefs": {"type": "object", "additionalProperties": {
                "type": "number", "minimum": 0, "maximum": 1}}},
            "required": ["beliefs"]}
        assert check_payload_schema({"beliefs": {"H1": 0.5}}, schema) == ""
        assert check_payload_schema({"beliefs": {"H1": 2}}, schema) != ""

    def test_non_dict_payload_rejected(self):
        assert check_payload_schema([], POLL_SCHEMA) != ""

    def test_integer_rejects_fractional(self):
        schema = {"type": "object",
                  "properties": {"n": {"type": "integer"}},
                  "required": ["n"]}
        assert check_payload_schema({"n": 3}, schema) == ""
        assert check_payload_schema({"n": 3.0}, schema) == ""   # integral float ok
        assert check_payload_schema({"n": 2.5}, schema) != ""   # fractional rejected

    def test_integer_still_range_checked(self):
        schema = {"type": "object", "properties": {
            "n": {"type": "integer", "minimum": 1, "maximum": 5}}}
        assert check_payload_schema({"n": 6}, schema) != ""
