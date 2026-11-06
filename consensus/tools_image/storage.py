"""Image file storage (resize, traversal-safe paths) and ingestion."""

import base64
import io
import logging
import os
import uuid
from typing import Optional

from ..config import get_images_dir
from .constants import (
    _PIL_FORMATS, MAX_IMAGE_BYTES, MAX_IMAGE_DIMENSION, SUPPORTED_IMAGE_TYPES,
)

logger = logging.getLogger(__name__)


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

    content_bytes, width, height = _measure_and_shrink(content_bytes,
                                                      mime_type)

    with open(full_path, "wb") as f:
        f.write(content_bytes)

    file_size = len(content_bytes)
    return safe_name, width, height, file_size


def _measure_and_shrink(
    content_bytes: bytes, mime_type: str,
) -> tuple[bytes, Optional[int], Optional[int]]:
    """Return the bytes to store and their dimensions.

    An image larger than ``MAX_IMAGE_DIMENSION`` is shrunk and re-encoded in
    its own format. The dimensions returned always describe the bytes
    returned: when the re-encode fails the original is kept with its
    original size. Vector (SVG) or unmeasurable input yields ``None`` sizes.
    """
    fmt = _PIL_FORMATS.get(mime_type)
    if fmt is None:
        return content_bytes, None, None
    try:
        from PIL import Image as PILImage
    except ImportError:
        logger.debug("Pillow not available; saving image without resize")
        return content_bytes, None, None

    width = height = None
    try:
        img = PILImage.open(io.BytesIO(content_bytes))
        width, height = img.size
        if max(width, height) <= MAX_IMAGE_DIMENSION:
            return content_bytes, width, height
        img.thumbnail((MAX_IMAGE_DIMENSION, MAX_IMAGE_DIMENSION))
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        return buf.getvalue(), img.size[0], img.size[1]
    except Exception as e:
        logger.warning("Pillow processing failed: %s; saving raw bytes", e)
        return content_bytes, width, height


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
