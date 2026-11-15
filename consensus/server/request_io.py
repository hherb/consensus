"""Reading request bodies: JSON objects and multipart uploads."""

from aiohttp import web


async def read_json_object(request: web.Request) -> dict | None:
    """Return the request's JSON body if it is an object, else ``None``."""
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


class UploadError(Exception):
    """An upload request that cannot be read; carries the HTTP status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


async def read_upload(request: web.Request, max_bytes: int,
                      default_filename: str, default_mime: str
                      ) -> tuple[bytes, str, str, int, str]:
    """Read a multipart upload: a ``file`` part plus optional text fields.

    The request's body limit is raised to *max_bytes* for this read only
    (aiohttp's default ``client_max_size`` of 1 MiB stays on every other
    route).

    Args:
        request: The multipart ``POST`` request.
        max_bytes: Largest accepted part.
        default_filename: Used when the file part has no filename.
        default_mime: Used when the file part has no Content-Type.

    Returns:
        ``(file_bytes, filename, mime_type, discussion_id, title)``; a
        non-numeric ``discussion_id`` field reads as 0 (unbound).

    Raises:
        UploadError: 400 for a body that is not multipart or has no file
            part, 413 for a part over *max_bytes*.
    """
    file_data = b""
    filename = default_filename
    mime_type = default_mime
    discussion_id = 0
    title = ""
    try:
        reader = await request.clone(client_max_size=max_bytes).multipart()
        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                file_data = await part.read()
                filename = part.filename or default_filename
                mime_type = part.headers.get("Content-Type", default_mime)
                mime_type = mime_type.split(";")[0].strip()
            elif part.name == "discussion_id":
                text = (await part.read()).decode()
                discussion_id = int(text) if text.isdigit() else 0
            elif part.name == "title":
                title = (await part.read()).decode()
    except web.HTTPRequestEntityTooLarge:
        raise UploadError(
            f"File too large. Maximum is {max_bytes // (1024 * 1024)} MB.",
            413) from None
    except (AssertionError, ValueError) as e:
        # aiohttp asserts on a non-multipart Content-Type and raises
        # ValueError on a malformed body.
        raise UploadError(f"Expected a multipart/form-data upload: {e}",
                          400) from None
    if not file_data:
        raise UploadError("No file provided", 400)
    return file_data, filename, mime_type, discussion_id, title
