"""Vision-model detection, multimodal context blocks and the vision LLM call."""

import logging

from ..ai_client import AIClient
from ..models import AIConfig
from ..tools import ToolContext
from .constants import VISION_IMAGE_TYPES
from .storage import image_to_base64_url

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vision model detection
# ---------------------------------------------------------------------------

# Fallback patterns used when a model isn't found in the OpenRouter cache.
_VISION_MODEL_PATTERNS = [
    "gpt-4o", "gpt-4-vision", "gpt-4-turbo", "gpt-4.1", "gpt-5",
    "claude-3", "claude-sonnet", "claude-opus", "claude-haiku",
    "gemini", "llava", "pixtral", "qwen-vl", "qwen2-vl",
    "internvl", "kimi", "moonshot-v", "grok",
    "step-2v", "glm-4v", "yi-vision", "phi-3-vision", "phi-4",
]


def is_vision_capable(model: str, pricing_cache=None,
                      base_url: str = "") -> bool:
    """Check if a model supports image input.

    First consults the OpenRouter-backed pricing cache for authoritative
    modality data.  Falls back to pattern matching on the model name when
    the model isn't in the cache.
    """
    if pricing_cache is not None:
        result = pricing_cache.has_input_modality(model, "image", base_url)
        if result is not None:
            return result
    # Fallback: pattern matching
    model_lower = model.lower()
    return any(p in model_lower for p in _VISION_MODEL_PATTERNS)


# ---------------------------------------------------------------------------
# Multimodal context helpers
# ---------------------------------------------------------------------------

def build_image_content_blocks(
    images: list[dict],
) -> list[dict]:
    """Build OpenAI-format multimodal content blocks for a list of images.

    Each image dict must have 'id', 'storage_path' and 'mime_type', and may
    have 'title' or 'original_filename' for its label. Every image is
    labelled in a leading text block; only vision-viewable ones that load
    get an image block, and the label says why the others have none.

    Returns:
        Content blocks for the 'content' field of a message, or ``[]`` when
        *images* is empty.
    """
    blocks: list[dict] = []
    labels = []
    for img in images:
        label = img.get("title") or img.get("original_filename") or f"Image {img['id']}"
        if img["mime_type"] not in VISION_IMAGE_TYPES:
            # Listed so the model knows it exists, but never sent: one
            # unsupported block makes the provider reject the whole turn.
            labels.append(f"[Image {img['id']}] {label} ({img['mime_type']}, "
                          "not viewable by vision models)")
            continue
        try:
            data_url = image_to_base64_url(img["storage_path"], img["mime_type"])
            blocks.append({
                "type": "image_url",
                "image_url": {"url": data_url, "detail": "auto"},
            })
            labels.append(f"[Image {img['id']}] {label}")
        except Exception as e:
            logger.warning("Failed to load image %s: %s", img.get("id"), e)
            labels.append(f"[Image {img['id']}] {label} (could not be loaded)")

    if not labels:
        return []

    # Prepend a text block describing the images
    text = "Images shared in this discussion:\n" + "\n".join(labels)
    return [{"type": "text", "text": text}] + blocks


# ---------------------------------------------------------------------------
# LLM helper for image description
# ---------------------------------------------------------------------------

async def _call_vision_llm(
    app, context: ToolContext,
    image_data_url: str,
    question: str,
) -> str:
    """Call a vision-capable LLM to describe or answer questions about an image.

    Uses the caller's model if it is vision-capable, else the moderator's
    if that one is. When neither is, the caller's model is still tried:
    capability detection can miss a model that does accept images, and a
    provider refusal is reported to the caller as an error.

    Raises:
        LookupError: The calling entity is not in the database.
        Exception: Whatever the AI client raises; the caller reports it.
    """
    entity = app.db.get_entity(context.caller_entity_id)
    if not entity:
        raise LookupError("could not resolve the caller entity")

    ai_config = AIConfig.from_db_row(entity)

    # If the caller's model supports vision, use it; otherwise try the moderator
    pc = app.db.pricing
    if not is_vision_capable(ai_config.model, pricing_cache=pc,
                             base_url=ai_config.base_url):
        # Try the moderator
        moderator_id = None
        if hasattr(app, 'discussion') and app.discussion:
            moderator_id = app.discussion.moderator_id
        if moderator_id:
            mod_entity = app.db.get_entity(moderator_id)
            if mod_entity:
                mod_config = AIConfig.from_db_row(mod_entity)
                if is_vision_capable(mod_config.model, pricing_cache=pc,
                                     base_url=mod_config.base_url):
                    ai_config = mod_config
                    entity = mod_entity

    api_key = app._resolve_key_for_moderator(
        ai_config.provider_id, entity.get("api_key_env", ""),
    )

    client = AIClient(base_url=ai_config.base_url, api_key=api_key)
    try:
        messages = [
            {"role": "system", "content": (
                "You are a visual analysis expert. Describe images in detail, "
                "noting key elements, text, diagrams, data, and any relevant context."
            )},
            {"role": "user", "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": image_data_url, "detail": "high"}},
            ]},
        ]
        response = await client.complete(
            messages=messages,
            model=ai_config.model,
            temperature=0.3,
            max_tokens=ai_config.max_tokens,
        )
        return response.content
    finally:
        await client.close()
