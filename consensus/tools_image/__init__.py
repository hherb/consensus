"""Image tool provider for Consensus.

Provides image storage, multimodal context support, and tools for AI participants:
- describe_image: Get a detailed description of an image (for non-vision models)
- list_images: List images in the current discussion
- add_image_url: Add an image from a URL to the discussion

Optional: Pillow (PIL) for image dimension detection and resizing.

Split from a single module into this package (issue #61): ``constants``,
``storage`` (files + ingestion), ``fetch`` (URL fetch + SSRF guard),
``vision`` (model detection, context blocks, vision LLM call), ``schemas``,
``handlers`` and ``provider``. This facade re-exports what other modules
import; patch names in the submodule that *uses* them.
"""

from .fetch import fetch_image_from_url
from .provider import create_image_provider
from .storage import (
    delete_image_file, image_to_base64_url, ingest_image, load_image_file,
    save_image_file,
)
from .vision import build_image_content_blocks, is_vision_capable

__all__ = [
    "build_image_content_blocks",
    "create_image_provider",
    "delete_image_file",
    "fetch_image_from_url",
    "image_to_base64_url",
    "ingest_image",
    "is_vision_capable",
    "load_image_file",
    "save_image_file",
]
