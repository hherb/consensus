"""Reading request bodies: JSON objects and multipart uploads."""

from aiohttp import BodyPartReader, web


async def read_json_object(request: web.Request) -> dict | None:
    """Return the request's JSON body if it is an object, else ``None``.

    A body over the request's size limit is not "invalid JSON": aiohttp's
    ``HTTPRequestEntityTooLarge`` propagates and the client gets a 413.
    """
    try:
        data = await request.json()
    except ValueError:
        # JSONDecodeError and UnicodeDecodeError are ValueErrors, and so is
        # an integer past Python's int-string length limit.
        return None
    return data if isinstance(data, dict) else None


def non_string_field(data: dict, names: tuple[str, ...]) -> web.Response | None:
    """A 400 naming the first of *names* present in *data* but not a string."""
    for name in names:
        if name in data and not isinstance(data[name], str):
            return web.json_response({"error": f"{name} must be a string"},
                                     status=400)
    return None


class UploadError(Exception):
    """An upload request that cannot be read; carries the HTTP status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


async def _read_text_field(part: BodyPartReader) -> str:
    """Decode a multipart text field; a non-UTF-8 one is a 400 naming it."""
    raw = await part.read()
    try:
        return raw.decode()
    except UnicodeDecodeError:
        raise UploadError(f"{part.name} must be UTF-8 text", 400) from None


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
        UploadError: 400 for a body that is not multipart, has no file
            part or has a text field that is not UTF-8; 413 for a part
            over *max_bytes*.
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
                text = await _read_text_field(part)
                # isascii: "\u00b2".isdigit() is true but int() rejects it.
                discussion_id = (int(text) if text.isascii() and text.isdigit()
                                 else 0)
            elif part.name == "title":
                title = await _read_text_field(part)
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
