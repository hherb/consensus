"""Handlers for the ``describe_image``, ``list_images`` and ``add_image_url`` tools."""

import json
import logging

from ..http_fetch import FetchError, redact_url
from ..tools import TOOL_FETCH_DEADLINE, ToolContext, ToolResult
from .constants import VISION_IMAGE_TYPES
from .fetch import fetch_image_from_url
from .storage import image_to_base64_url, ingest_image
from .vision import _call_vision_llm

logger = logging.getLogger(__name__)


async def _describe_image_handler(
    arguments: dict, context: ToolContext,
    db, app,
) -> ToolResult:
    """Describe an image using a vision-capable model."""
    image_id = arguments.get("image_id")
    question = arguments.get("question", "").strip()
    if not question:
        question = "Describe this image in detail."

    if image_id is None:
        return ToolResult(content="image_id is required.", is_error=True)

    image = db.get_image(int(image_id))
    if not image:
        return ToolResult(content=f"Image {image_id} not found.", is_error=True)
    if image["mime_type"] not in VISION_IMAGE_TYPES:
        # Vision providers reject the whole request for such a block.
        return ToolResult(
            content=(f"Image {image_id} is {image['mime_type']}, which vision "
                     "models cannot view."),
            is_error=True)

    try:
        data_url = image_to_base64_url(image["storage_path"], image["mime_type"])
    except Exception as e:
        logger.warning("Could not load image %s: %s", image_id, e)
        return ToolResult(content=f"Failed to load image: {e}", is_error=True)

    try:
        description = await _call_vision_llm(app, context, data_url, question)
    except Exception as e:
        # Never returned as a description: the AI would read it as fact and
        # the cache below would persist it as the image's description.
        # exc_info: a bug here (not just a provider fault) must keep its
        # traceback, which the registry would otherwise have logged.
        logger.warning("Vision LLM call failed for image %s: %s", image_id, e,
                       exc_info=True)
        return ToolResult(content=f"Vision LLM call failed: {e}",
                          is_error=True)

    # Cache the description if it was a general describe request
    if "describe" in question.lower() and not image.get("description"):
        try:
            db.update_image_description(int(image_id), description)
        except Exception as e:
            # The description is still returned; only the cache is lost.
            logger.warning("Could not cache description of image %s: %s",
                           image_id, e)

    result = {
        "image_id": image_id,
        "title": image.get("title", ""),
        "description": description,
    }
    return ToolResult(
        content=json.dumps(result, indent=2),
        metadata=result,
    )


async def _list_images_handler(
    arguments: dict, context: ToolContext,
    db, app,
) -> ToolResult:
    """List images attached to the current discussion."""
    images = db.get_discussion_images(context.discussion_id)
    if not images:
        return ToolResult(content="No images attached to this discussion.")

    lines = [f"Images in this discussion — {len(images)} total:\n"]
    for img in images:
        desc = img.get("description", "")
        desc_snippet = (desc[:100] + "...") if len(desc) > 100 else desc
        dims = ""
        if img.get("width") and img.get("height"):
            dims = f", {img['width']}x{img['height']}"
        lines.append(
            f"  [ID {img['id']}] {img['title']} "
            f"({img['mime_type']}{dims}, {img['file_size']} bytes)"
        )
        if desc_snippet:
            lines.append(f"    {desc_snippet}")
    return ToolResult(content="\n".join(lines), metadata={"count": len(images)})


async def _add_image_url_handler(
    arguments: dict, context: ToolContext,
    db, app,
) -> ToolResult:
    """Add an image from a URL to the current discussion."""
    url = arguments.get("url", "").strip()
    title = arguments.get("title", "").strip() or None

    if not url:
        return ToolResult(content="url is required.", is_error=True)

    try:
        content_bytes, filename, mime_type = await fetch_image_from_url(
            url, deadline=TOOL_FETCH_DEADLINE)
    except FetchError as e:
        # Expected: rejected URL, oversize or non-image body, HTTP fault.
        logger.warning("Image fetch failed for %s: %s", redact_url(url), e)
        return ToolResult(content=f"Failed to fetch image: {e}", is_error=True)
    except Exception as e:
        # A bug in the fetch path, not a bad URL: keep the traceback.
        logger.exception("Image fetch failed unexpectedly for %s",
                         redact_url(url))
        return ToolResult(content=f"Failed to fetch image: {e}", is_error=True)

    result = await ingest_image(
        db=db,
        content_bytes=content_bytes,
        filename=filename,
        mime_type=mime_type,
        discussion_id=context.discussion_id,
        source_url=url,
        title=title,
        source_type="url",
        uploader_entity_id=context.caller_entity_id,
    )

    if "error" in result:
        return ToolResult(content=result["error"], is_error=True)

    return ToolResult(
        content=json.dumps(result, indent=2),
        metadata=result,
    )
