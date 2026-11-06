"""JSON schemas for the image tools' parameters."""

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
