"""LLM helper for document interpretation."""

import logging

from ..ai_client import AIClient
from ..models import AIConfig
from ..tools import ToolContext
from .constants import INTERPRETATION_TEMPERATURE, LLM_TIMEOUT
from .errors import DocumentInterpretationError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM helper for interpretation
# ---------------------------------------------------------------------------

async def _call_interpretation_llm(
    app, context: ToolContext,
    system_prompt: str, user_prompt: str,
) -> str:
    """Call an LLM for document interpretation using the caller's config.

    Raises:
        DocumentInterpretationError: If the caller entity cannot be
            resolved, or the completion call fails.  It raises rather than
            returning a parenthesised error string, because callers used
            the return value verbatim: it became ``doc_ask``'s answer and,
            via ``ingest_document``, the document's persisted ``summary``
            (issue #78 defect 1).
    """
    entity = app.db.get_entity(context.caller_entity_id)
    if not entity:
        raise DocumentInterpretationError(
            f"Could not resolve caller entity {context.caller_entity_id} "
            "for the document interpretation call",
            hint="the entity may have been removed from the discussion",
        )

    ai_config = AIConfig.from_db_row(entity)
    api_key = app._resolve_key_for_moderator(
        ai_config.provider_id, entity.get("api_key_env", ""),
    )

    client = AIClient(
        base_url=ai_config.base_url, api_key=api_key, timeout=LLM_TIMEOUT,
    )
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        response = await client.complete(
            messages=messages,
            model=ai_config.model,
            temperature=INTERPRETATION_TEMPERATURE,
            max_tokens=ai_config.max_tokens,
        )
        return response.content
    except Exception as e:
        logger.warning("Interpretation LLM call failed: %s", e)
        raise DocumentInterpretationError(
            f"The interpretation model {ai_config.model} failed: {e}",
            hint="check the provider's API key, quota and base URL",
        ) from e
    finally:
        await client.close()
