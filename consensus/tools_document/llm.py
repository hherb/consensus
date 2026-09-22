"""LLM helper for document interpretation."""

import logging

from ..ai_client import AIClient
from ..models import AIConfig
from ..tools import ToolContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# LLM helper for interpretation
# ---------------------------------------------------------------------------

async def _call_interpretation_llm(
    app, context: ToolContext,
    system_prompt: str, user_prompt: str,
) -> str:
    """Call an LLM for document interpretation using the caller entity's config."""
    entity = app.db.get_entity(context.caller_entity_id)
    if not entity:
        return "(Error: could not resolve caller entity for LLM call)"

    ai_config = AIConfig.from_db_row(entity)
    # Resolve API key via app's key resolver
    api_key = app._resolve_key_for_moderator(
        ai_config.provider_id, entity.get("api_key_env", ""),
    )

    client = AIClient(base_url=ai_config.base_url, api_key=api_key)
    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        response = await client.complete(
            messages=messages,
            model=ai_config.model,
            temperature=0.3,
            max_tokens=ai_config.max_tokens,
        )
        return response.content
    except Exception as e:
        logger.warning("Interpretation LLM call failed: %s", e)
        return f"(LLM call failed: {e})"
    finally:
        await client.close()
