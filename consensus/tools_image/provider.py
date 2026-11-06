"""Factory for the ``images`` tool provider."""

from ..tools import PythonToolProvider, ToolContext, ToolDefinition, ToolResult
from .handlers import (
    _add_image_url_handler, _describe_image_handler, _list_images_handler,
)
from .schemas import (
    _ADD_IMAGE_URL_SCHEMA, _DESCRIBE_IMAGE_SCHEMA, _LIST_IMAGES_SCHEMA,
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
