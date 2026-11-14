"""A rejected tool call is an MCP ``isError`` result (issue #121).

Handlers used to return their validation failures as plain text, which
``_handle_tool_call`` wrapped in a *successful* result, so an agent that
branched on ``isError`` read ``"Error: ..."`` as content. Each case here
drives the real server through ``handle_request``.
"""

import logging

import pytest

from consensus.mcp_server import server as server_module
from tests.mcp_server_helpers import _ai_entities, call, server  # noqa: F401


def _human(db) -> int:
    return db.add_entity("Hu", "human")


#: ``(tool, arguments or a callable building them from the db, text)``.
REJECTED_CALLS = [
    ("store_memory", {"content": "  "}, "Error: No content provided."),
    ("delete_memory", {"memory_id": ""}, "Error: No memory_id provided."),
    ("delete_memory", {"memory_id": "nope"},
     "Error: Memory not found or does not belong to this agent."),
    ("assert_knowledge", {"subject": "A", "relation": "r"},
     "Error: subject, relation, and object are all required."),
    ("read_discussion", {"discussion_id": 999},
     "Error: Discussion not found"),
    ("read_document", {"document_id": 999}, "Error: Document not found."),
    ("run_discussion", {"topic": " "}, "Error: No topic provided."),
    ("run_discussion", {"topic": "T"},
     "Error: Need at least 2 AI entities configured. "
     "Create them in the Consensus UI first."),
    ("run_discussion",
     lambda db: {"topic": "T",
                 "entity_ids": [_ai_entities(db, 1)[0], _human(db)]},
     "Error: Entity 'Hu' is human. "
     "Only AI entities can participate in automated discussions."),
]


@pytest.mark.parametrize("tool,args,expected", REJECTED_CALLS,
                         ids=[f"{t}-{i}" for i, (t, _, _)
                              in enumerate(REJECTED_CALLS)])
async def test_rejected_call_is_an_error_result(server, tool, args, expected):
    if callable(args):
        args = args(server._db)
    text, is_error = await call(server, tool, args)
    assert is_error
    assert text == expected


async def test_rejection_logs_a_warning_without_traceback(server, caplog):
    with caplog.at_level(logging.WARNING, logger=server_module.__name__):
        await call(server, "store_memory", {"content": ""})
    [record] = [r for r in caplog.records if r.name == server_module.__name__]
    assert record.levelno == logging.WARNING
    assert record.exc_info is None
    assert "store_memory" in record.getMessage()


async def test_unexpected_failure_still_logs_its_traceback(server, caplog):
    with caplog.at_level(logging.WARNING, logger=server_module.__name__):
        _, is_error = await call(server, "read_discussion",
                                 {"discussion_id": "not-a-number"})
    assert is_error
    [record] = [r for r in caplog.records if r.name == server_module.__name__]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
