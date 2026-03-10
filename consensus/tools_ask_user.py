"""Interactive tool provider: ask_user.

Allows an AI participant to pause its turn and request input from the
human user.  The tool execution blocks (via asyncio.Future) until the
frontend submits the user's response, which is then returned as a
ToolResult so the AI's tool loop continues seamlessly.
"""

import asyncio
import logging
import uuid

from .tools import PythonToolProvider, ToolContext, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

ASK_USER_TIMEOUT = 300.0  # 5 minutes

ASK_USER_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": (
                "The question or request to present to the human user. "
                "Be specific about what information you need."
            ),
        },
        "context": {
            "type": "string",
            "description": (
                "Optional additional context explaining why the input is "
                "needed, shown alongside the question."
            ),
        },
    },
    "required": ["question"],
}


def create_ask_user_provider(app) -> PythonToolProvider:
    """Create the interactive ask_user tool provider.

    Parameters
    ----------
    app : ConsensusApp
        Application instance used to store pending futures and emit events.
    """
    provider = PythonToolProvider(name="interactive")

    async def ask_user_handler(arguments: dict, context: ToolContext) -> ToolResult:
        question = arguments.get("question", "").strip()
        if not question:
            return ToolResult(content="No question provided.", is_error=True)

        request_id = f"{context.discussion_id}_{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        # Resolve entity name for frontend display
        entity_name = ""
        if app.db:
            entity = app.db.get_entity(context.caller_entity_id)
            if entity:
                entity_name = entity.get("name", "")

        request_data = {
            "request_id": request_id,
            "discussion_id": context.discussion_id,
            "entity_id": context.caller_entity_id,
            "entity_name": entity_name,
            "question": question,
            "context": arguments.get("context", ""),
        }

        app._pending_user_inputs[request_id] = (future, request_data)

        logger.info("ask_user: %s requests input (request_id=%s): %s",
                     entity_name, request_id, question)

        # Notify frontend
        app.emit("user_input_request", request_data)

        try:
            user_answer = await asyncio.wait_for(future, timeout=ASK_USER_TIMEOUT)
            return ToolResult(
                content=user_answer,
                metadata={"request_id": request_id},
            )
        except asyncio.TimeoutError:
            logger.warning("ask_user timed out (request_id=%s)", request_id)
            return ToolResult(
                content="The user did not respond within the time limit.",
                is_error=True,
            )
        except asyncio.CancelledError:
            logger.info("ask_user cancelled (request_id=%s)", request_id)
            return ToolResult(
                content="The input request was cancelled.",
                is_error=True,
            )
        finally:
            app._pending_user_inputs.pop(request_id, None)

    provider.register(
        ToolDefinition(
            name="ask_user",
            description=(
                "Ask the human user a question and wait for their response. "
                "Use this when you need clarification, a preference, or a "
                "decision from the user before continuing your response."
            ),
            parameters=ASK_USER_SCHEMA,
        ),
        ask_user_handler,
    )

    return provider
