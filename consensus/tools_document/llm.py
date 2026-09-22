"""LLM helper for document interpretation."""

import logging

from ..ai_client import AIClient
from ..models import AIConfig
from ..tools import ToolContext
from .constants import INTERPRETATION_TEMPERATURE, LLM_TIMEOUT

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM helper for interpretation
# ---------------------------------------------------------------------------

async def _call_interpretation_llm(
    app, context: ToolContext,
    system_prompt: str, user_prompt: str,
) -> str:
    """Call an LLM for document interpretation using the caller entity's config.

    Never raises. Every failure — an unresolvable caller entity, or any error
    from the completion call — is returned as a parenthesised error *string*
    in place of the answer. Callers surface that string verbatim: it becomes
    ``doc_ask``'s answer, and via ``ingest_document`` it is persisted as the
    document's ``summary`` column. A caller that needs to distinguish failure
    from an answer must inspect the return value; a ``try`` around this
    function will never fire. See issue #78.
    """
    entity = app.db.get_entity(context.caller_entity_id)
    if not entity:
        logger.warning(
            "Interpretation LLM call: could not resolve caller entity %s",
            context.caller_entity_id,
        )
        return "(Error: could not resolve caller entity for LLM call)"

    ai_config = AIConfig.from_db_row(entity)
    # Resolve API key via app's key resolver
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
        return f"(LLM call failed: {e})"
    finally:
        await client.close()
