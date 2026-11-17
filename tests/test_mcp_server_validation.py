"""Tool arguments are checked against the tool's ``inputSchema`` (issue #124).

A missing or mistyped argument used to reach the handler and fail there as
a ``KeyError`` / ``ValueError`` / ``AttributeError``, logged at ERROR with a
traceback as if it were a bug. ``validate_arguments`` rejects it with a
``ToolError`` naming the argument. Numeric strings such as ``"5"`` are
coerced, as ``int()`` accepted them before (repo owner's decision).
"""

import logging

import pytest

from consensus.mcp_server import server as server_module
from consensus.mcp_server.errors import ToolError
from consensus.mcp_server.validation import validate_arguments
from tests.mcp_server_helpers import _ai_entities, call, server  # noqa: F401

SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "query": {"type": "string"},
        "flag": {"type": "boolean"},
        "cost": {"type": "number"},
        "mode": {"type": "string", "enum": ["search", "neighbors"]},
        "ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["id"],
}


@pytest.mark.parametrize("given,expected", [
    ({"id": 5}, {"id": 5}),
    ({"id": "5"}, {"id": 5}),
    ({"id": " -7 "}, {"id": -7}),
    ({"id": 5.0}, {"id": 5}),
    ({"id": 1, "cost": "0.5"}, {"id": 1, "cost": 0.5}),
    ({"id": 1, "cost": 2}, {"id": 1, "cost": 2}),
    ({"id": 1, "ids": ["3", 4]}, {"id": 1, "ids": [3, 4]}),
    ({"id": 1, "mode": "neighbors"}, {"id": 1, "mode": "neighbors"}),
    ({"id": 1, "flag": False}, {"id": 1, "flag": False}),
    # An explicit null for an optional argument means "not given".
    ({"id": 1, "query": None}, {"id": 1}),
    # Arguments the schema does not declare pass through untouched.
    ({"id": 1, "extra": [1]}, {"id": 1, "extra": [1]}),
])
def test_valid_arguments_are_coerced(given, expected):
    assert validate_arguments(SCHEMA, given) == expected


def test_input_is_not_mutated():
    given = {"id": "5", "query": None}
    validate_arguments(SCHEMA, given)
    assert given == {"id": "5", "query": None}


@pytest.mark.parametrize("given,message", [
    ({}, "id is required"),
    ({"id": None}, "id is required"),
    ({"id": "x"}, "id must be an integer"),
    ({"id": "5.5"}, "id must be an integer"),
    ({"id": 5.5}, "id must be an integer"),
    ({"id": True}, "id must be an integer"),
    ({"id": 1, "cost": True}, "cost must be a number"),
    ({"id": 1, "cost": "nan"}, "cost must be a number"),
    ({"id": 1, "cost": "inf"}, "cost must be a number"),
    ({"id": 1, "query": 3}, "query must be a string"),
    ({"id": 1, "flag": "false"}, "flag must be a boolean"),
    ({"id": 1, "flag": 1}, "flag must be a boolean"),
    ({"id": 1, "mode": "path"},
     "mode must be one of: search, neighbors"),
    ({"id": 1, "ids": 3}, "ids must be an array"),
    ({"id": 1, "ids": [1, "y"]}, "ids[1] must be an integer"),
    # Beyond SQLite's 64-bit INTEGER, or Python's int() digit limit.
    ({"id": 2**63}, "id must be an integer"),
    ({"id": -2**63 - 1}, "id must be an integer"),
    ({"id": "9" * 30}, "id must be an integer"),
    ({"id": "9" * 5000}, "id must be an integer"),
    ({"id": 1e300}, "id must be an integer"),
    ({"id": 1, "cost": 10**400}, "cost must be a number"),
])
def test_invalid_arguments_raise_tool_error(given, message):
    with pytest.raises(ToolError) as exc:
        validate_arguments(SCHEMA, given)
    assert str(exc.value) == message


def test_every_problem_is_reported_at_once():
    with pytest.raises(ToolError) as exc:
        validate_arguments(SCHEMA, {"query": 3})
    assert str(exc.value) == "id is required; query must be a string"


# -- Through the real server --------------------------------------------------


async def test_mistyped_argument_is_a_warning_not_a_traceback(server, caplog):
    with caplog.at_level(logging.WARNING, logger=server_module.__name__):
        text, is_error = await call(server, "read_discussion",
                                    {"discussion_id": "not-a-number"})
    assert is_error
    assert text == "Error: discussion_id must be an integer"
    [record] = [r for r in caplog.records if r.name == server_module.__name__]
    assert record.levelno == logging.WARNING
    assert record.exc_info is None


async def test_missing_required_argument_is_named(server):
    text, is_error = await call(server, "read_document", {})
    assert is_error
    assert text == "Error: document_id is required"


async def test_numeric_string_reaches_the_handler_as_an_int(server):
    a, b = _ai_entities(server._db)
    did = server._db.create_discussion("T", a)
    text, is_error = await call(server, "read_discussion",
                                {"discussion_id": str(did)})
    assert not is_error, text
    assert '"topic": "T"' in text


async def test_handler_gets_the_validated_arguments(server):
    """The handler sees the coerced copy: a dropped null, not ``int(None)``."""
    text, is_error = await call(server, "list_discussions", {"limit": None})
    assert not is_error, text


async def test_every_advertised_schema_is_supported(server):
    """A tool def using a keyword or type the validator ignores fails here."""
    from consensus.mcp_server.validation import SUPPORTED_KEYWORDS, TYPE_CHECKS
    for name, tool in server._tools.items():
        schema = tool["definition"]["inputSchema"]
        assert set(schema) <= {"type", "properties", "required"}, name
        assert set(schema.get("required", [])) <= set(schema["properties"]), name
        for arg, spec in schema["properties"].items():
            for s in (spec, spec.get("items", {"type": "string"})):
                assert set(s) <= SUPPORTED_KEYWORDS, (name, arg)
                assert s["type"] in TYPE_CHECKS, (name, arg)
