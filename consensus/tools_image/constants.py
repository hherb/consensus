"""Limits and MIME-type sets for the image tools."""

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

# Raster types Pillow can measure and re-encode, mapped to its format names.
# Re-encoding in the image's own format keeps the bytes matching the stored
# MIME type (an oversized GIF used to be saved as JPEG bytes).
_PIL_FORMATS = {
    "image/png": "PNG", "image/jpeg": "JPEG", "image/gif": "GIF",
    "image/webp": "WEBP",
}

# What vision APIs accept as image input. SVG is storable and viewable in
# the UI, but providers reject it, so it must never reach an image block.
VISION_IMAGE_TYPES = frozenset(_PIL_FORMATS)
