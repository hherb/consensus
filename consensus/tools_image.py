"""Image tool provider for Consensus.

Provides image storage, multimodal context support, and tools for AI participants:
- describe_image: Get a detailed description of an image (for non-vision models)
- list_images: List images in the current discussion
- add_image_url: Add an image from a URL to the discussion

Optional: Pillow (PIL) for image dimension detection and resizing.
"""

import base64
import io
import json
import logging
import os
import uuid
from typing import Optional

import httpx

from .ai_client import AIClient
from .config import get_images_dir
from .models import AIConfig
from .tools import PythonToolProvider, ToolContext, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

# Max image dimension — resize if larger
MAX_IMAGE_DIMENSION = 2048

# Max upload/fetch size in bytes (20 MB)
MAX_IMAGE_BYTES = 20 * 1024 * 1024

# Timeout for URL fetching
URL_FETCH_TIMEOUT = 30.0

# Supported MIME types
SUPPORTED_IMAGE_TYPES = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
})


# ---------------------------------------------------------------------------
# Vision model detection
# ---------------------------------------------------------------------------

VISION_MODEL_PATTERNS = [
    "gpt-4o", "gpt-4-vision", "gpt-4-turbo", "gpt-4.1",
    "claude-3", "claude-sonnet", "claude-opus", "claude-haiku",
    "gemini", "llava", "pixtral", "qwen-vl", "qwen2-vl",
    "internvl",
]


def is_vision_capable(model: str) -> bool:
    """Check if a model is likely vision-capable based on its name."""
    model_lower = model.lower()
    return any(p in model_lower for p in VISION_MODEL_PATTERNS)


# ---------------------------------------------------------------------------
# Image storage
# ---------------------------------------------------------------------------

def save_image_file(
    content_bytes: bytes, filename: str, mime_type: str,
) -> tuple[str, Optional[int], Optional[int], int]:
    """Save image bytes to disk, optionally resizing.

    Returns (storage_path, width, height, file_size).
    storage_path is relative to the images directory.
    """
    # Generate unique filename
    ext = _extension_for_mime(mime_type)
    safe_name = f"{uuid.uuid4().hex[:12]}_{_sanitize_filename(filename)}"
    if not safe_name.lower().endswith(ext):
        safe_name += ext

    images_dir = get_images_dir()
    full_path = os.path.join(images_dir, safe_name)

    width, height = None, None

    try:
        from PIL import Image as PILImage

        img = PILImage.open(io.BytesIO(content_bytes))
        width, height = img.size

        # Resize if too large
        if max(width, height) > MAX_IMAGE_DIMENSION:
            img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))
            width, height = img.size
            buf = io.BytesIO()
            fmt = "PNG" if mime_type == "image/png" else "JPEG"
            if mime_type == "image/webp":
                fmt = "WEBP"
            img.save(buf, format=fmt)
            content_bytes = buf.getvalue()
    except ImportError:
        logger.debug("Pillow not available; saving image without resize")
    except Exception as e:
        logger.warning("Pillow processing failed: %s; saving raw bytes", e)

    with open(full_path, "wb") as f:
        f.write(content_bytes)

    file_size = len(content_bytes)
    return safe_name, width, height, file_size


def _safe_image_path(storage_path: str) -> str:
    """Resolve storage_path within the images directory, preventing traversal."""
    images_dir = os.path.realpath(get_images_dir())
    full_path = os.path.realpath(os.path.join(images_dir, storage_path))
    if not full_path.startswith(images_dir + os.sep) and full_path != images_dir:
        raise ValueError("Path traversal detected")
    return full_path


def load_image_file(storage_path: str) -> bytes:
    """Load image bytes from storage."""
    full_path = _safe_image_path(storage_path)
    with open(full_path, "rb") as f:
        return f.read()


def delete_image_file(storage_path: str) -> None:
    """Delete an image file from storage."""
    full_path = _safe_image_path(storage_path)
    if os.path.isfile(full_path):
        os.remove(full_path)


def image_to_base64_url(storage_path: str, mime_type: str) -> str:
    """Convert a stored image to a base64 data URL for API use."""
    data = load_image_file(storage_path)
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{b64}"


def _extension_for_mime(mime_type: str) -> str:
    """Get file extension for a MIME type."""
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
    }
    return mapping.get(mime_type, ".png")


def _sanitize_filename(filename: str) -> str:
    """Remove path separators and limit length."""
    name = os.path.basename(filename)
    # Keep only safe characters
    safe = "".join(c for c in name if c.isalnum() or c in "._-")
    return safe[:100] or "image"


# ---------------------------------------------------------------------------
# Image ingestion
# ---------------------------------------------------------------------------

async def ingest_image(
    db,
    content_bytes: bytes,
    filename: str,
    mime_type: str,
    discussion_id: Optional[int] = None,
    source_url: Optional[str] = None,
    title: Optional[str] = None,
    source_type: str = "upload",
    uploader_entity_id: Optional[int] = None,
) -> dict:
    """Save an image to disk, store metadata in DB, associate with discussion.

    Returns image metadata dict.
    """
    if len(content_bytes) > MAX_IMAGE_BYTES:
        return {"error": f"Image too large ({len(content_bytes)} bytes). Maximum is {MAX_IMAGE_BYTES} bytes."}
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        return {"error": f"Unsupported image type: {mime_type}"}

    storage_path, width, height, file_size = save_image_file(
        content_bytes, filename, mime_type,
    )

    if not title:
        title = os.path.splitext(os.path.basename(filename))[0]

    image_id = db.add_image(
        filename=storage_path,
        original_filename=filename,
        title=title,
        description="",
        mime_type=mime_type,
        width=width,
        height=height,
        file_size=file_size,
        storage_path=storage_path,
        source_type=source_type,
        source_url=source_url,
        uploader_entity_id=uploader_entity_id,
    )

    if discussion_id:
        db.add_discussion_image(discussion_id, image_id)

    return {
        "image_id": image_id,
        "title": title,
        "filename": storage_path,
        "width": width,
        "height": height,
        "file_size": file_size,
        "mime_type": mime_type,
    }


def _is_private_ip(hostname: str) -> bool:
    """Check if a hostname resolves to a private/internal IP address."""
    import ipaddress
    import socket
    try:
        addr = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _, _, _, sockaddr in addr:
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except (socket.gaierror, ValueError):
        return True  # treat unresolvable as blocked
    return False


async def fetch_image_from_url(url: str) -> tuple[bytes, str, str]:
    """Fetch an image from a URL. Returns (content_bytes, filename, mime_type)."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
    if _is_private_ip(parsed.hostname or ""):
        raise ValueError("Cannot fetch images from private/internal addresses")

    async with httpx.AsyncClient(
        timeout=URL_FETCH_TIMEOUT, follow_redirects=True,
    ) as client:
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > MAX_IMAGE_BYTES:
            raise ValueError(f"Image too large ({len(response.content)} bytes). Maximum is {MAX_IMAGE_BYTES} bytes.")
        content_type = response.headers.get("content-type", "image/png")
        mime_type = content_type.split(";")[0].strip()
        if mime_type not in SUPPORTED_IMAGE_TYPES:
            raise ValueError(f"URL does not point to a supported image type: {mime_type}")
        path = parsed.path
        filename = path.split("/")[-1] or "image"
        return response.content, filename, mime_type


# ---------------------------------------------------------------------------
# Multimodal context helpers
# ---------------------------------------------------------------------------

def build_image_content_blocks(
    images: list[dict],
) -> list[dict]:
    """Build OpenAI-format multimodal content blocks for a list of images.

    Each image dict must have 'storage_path', 'mime_type', and optionally 'title'.
    Returns a list of content blocks suitable for the 'content' field of a message.
    """
    blocks: list[dict] = []
    labels = []
    for img in images:
        try:
            data_url = image_to_base64_url(img["storage_path"], img["mime_type"])
            blocks.append({
                "type": "image_url",
                "image_url": {"url": data_url, "detail": "auto"},
            })
            label = img.get("title") or img.get("original_filename") or f"Image {img['id']}"
            labels.append(f"[Image {img['id']}] {label}")
        except Exception as e:
            logger.warning("Failed to load image %s: %s", img.get("id"), e)

    if not blocks:
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
    """Call a vision-capable LLM to describe or answer questions about an image."""
    # Find a vision-capable model to use
    entity = app.db.get_entity(context.caller_entity_id)
    if not entity:
        return "(Error: could not resolve caller entity for vision LLM call)"

    ai_config = AIConfig.from_db_row(entity)

    # If the caller's model supports vision, use it; otherwise try the moderator
    if not is_vision_capable(ai_config.model):
        # Try the moderator
        moderator_id = None
        if hasattr(app, 'discussion') and app.discussion:
            moderator_id = app.discussion.moderator_id
        if moderator_id:
            mod_entity = app.db.get_entity(moderator_id)
            if mod_entity:
                mod_config = AIConfig.from_db_row(mod_entity)
                if is_vision_capable(mod_config.model):
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
    except Exception as e:
        logger.warning("Vision LLM call failed: %s", e)
        return f"(Vision LLM call failed: {e})"
    finally:
        await client.close()


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_DESCRIBE_IMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "image_id": {
            "type": "integer",
            "description": "ID of the image to describe.",
        },
        "question": {
            "type": "string",
            "description": (
                "Optional question about the image. "
                "If not provided, a general description is given."
            ),
        },
    },
    "required": ["image_id"],
}

_LIST_IMAGES_SCHEMA = {
    "type": "object",
    "properties": {},
}

_ADD_IMAGE_URL_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {
            "type": "string",
            "description": "URL of the image to add to the discussion.",
        },
        "title": {
            "type": "string",
            "description": "Title for the image.",
        },
    },
    "required": ["url"],
}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

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

    try:
        data_url = image_to_base64_url(image["storage_path"], image["mime_type"])
    except Exception as e:
        return ToolResult(content=f"Failed to load image: {e}", is_error=True)

    description = await _call_vision_llm(app, context, data_url, question)

    # Cache the description if it was a general describe request
    if "describe" in question.lower() and not image.get("description"):
        try:
            db.update_image_description(int(image_id), description)
        except Exception:
            pass

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
        content_bytes, filename, mime_type = await fetch_image_from_url(url)
    except Exception as e:
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


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

def create_image_provider(db, app=None) -> PythonToolProvider:
    """Create and return the image tool provider.

    Args:
        db: Database instance for image storage.
        app: ConsensusApp instance for AI client access (needed by describe_image).
    """
    provider = PythonToolProvider(name="images")

    def _make_handler(fn):
        async def handler(arguments: dict, context: ToolContext) -> ToolResult:
            return await fn(arguments, context, db, app)
        return handler

    provider.register(
        ToolDefinition(
            name="describe_image",
            description=(
                "Get a detailed description of an image in the discussion. "
                "Uses a vision-capable AI model to analyze the image. "
                "Optionally provide a specific question about the image."
            ),
            parameters=_DESCRIBE_IMAGE_SCHEMA,
        ),
        _make_handler(_describe_image_handler),
    )

    provider.register(
        ToolDefinition(
            name="list_images",
            description=(
                "List all images attached to this discussion. "
                "Returns image ID, title, dimensions, and description for each."
            ),
            parameters=_LIST_IMAGES_SCHEMA,
        ),
        _make_handler(_list_images_handler),
    )

    provider.register(
        ToolDefinition(
            name="add_image_url",
            description=(
                "Add an image from a URL to the current discussion. "
                "Use this to share images you find during web searches, "
                "or to add diagrams and visualizations for other participants."
            ),
            parameters=_ADD_IMAGE_URL_SCHEMA,
        ),
        _make_handler(_add_image_url_handler),
    )

    return provider
