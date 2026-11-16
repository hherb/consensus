"""``/api/memory/*`` and ``/api/documents/*``: memory config and documents."""

import logging

from aiohttp import web

from .context import session_app
from .request_io import (
    UploadError, int_field, non_string_field, read_json_object, read_upload,
)

logger = logging.getLogger(__name__)


async def handle_memory_config_get(request: web.Request) -> web.StreamResponse:
    """Return current memory configuration."""
    app_inst = await session_app(request)
    if isinstance(app_inst, web.Response):
        return app_inst
    if not app_inst.memory_available:
        return web.json_response(
            {"error": "Memory tools failed to load; see the application log for the cause"},
            status=404,
        )
    try:
        config = app_inst.db.get_memory_config()
        return web.json_response(config)
    except Exception as e:
        logger.exception("Could not read memory config")
        return web.json_response({"error": str(e)}, status=500)


async def handle_memory_config_put(request: web.Request) -> web.StreamResponse:
    """Update memory configuration keys."""
    app_inst = await session_app(request)
    if isinstance(app_inst, web.Response):
        return app_inst
    if not app_inst.memory_available:
        return web.json_response(
            {"error": "Memory tools failed to load; see the application log for the cause"},
            status=404,
        )
    data = await read_json_object(request)
    if data is None:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    allowed_keys = {"embedding_backend", "embedding_model", "embedding_endpoint"}
    updates = {k: v for k, v in data.items() if k in allowed_keys}
    for key, value in updates.items():
        # bool is an int: a checkbox-style true would read back as "True".
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return web.json_response(
                {"error": f"{key} must be a string or a number"}, status=400)
    for key, value in updates.items():
        app_inst.db.set_memory_config(key, str(value))

    return web.json_response({"ok": True})


async def handle_memory_test(request: web.Request) -> web.StreamResponse:
    """Test the embedding connection."""
    app_inst = await session_app(request)
    if isinstance(app_inst, web.Response):
        return app_inst
    if not app_inst.memory_available:
        return web.json_response(
            {"error": "Memory tools failed to load; see the application log for the cause"},
            status=404,
        )
    try:
        from ..tools_memory import EmbeddingClient
        client = EmbeddingClient(app_inst.db)
        ok, message = await client.test_connection()
        return web.json_response({"ok": ok, "message": message})
    except Exception as e:
        logger.exception("Memory connection test failed")
        return web.json_response({"ok": False, "message": str(e)})


async def handle_document_upload(request: web.Request) -> web.StreamResponse:
    """Upload a document file via multipart form data."""
    app_inst = await session_app(request)
    if isinstance(app_inst, web.Response):
        return app_inst
    if not app_inst.documents_available:
        return web.json_response(
            {"error": "Document tools failed to load; see the application log for the cause"},
            status=404,
        )

    # Lazy, like app._init_document_tools: a broken optional backend
    # must not stop the server from starting.
    from ..tools_document.constants import MAX_DOCUMENT_BYTES
    try:
        file_data, filename, mime_type, discussion_id, title = \
            await read_upload(request, MAX_DOCUMENT_BYTES,
                              "document", "application/octet-stream")
    except UploadError as e:
        return web.json_response({"error": str(e)}, status=e.status)

    result = await app_inst.add_document(
        filename=filename,
        content_bytes=file_data,
        mime_type=mime_type,
        discussion_id=discussion_id,
        title=title,
    )
    if "error" in result:
        return web.json_response(result, status=400)
    return web.json_response(result)


async def handle_document_add_url(request: web.Request) -> web.StreamResponse:
    """Add a document by fetching from a URL."""
    app_inst = await session_app(request)
    if isinstance(app_inst, web.Response):
        return app_inst
    if not app_inst.documents_available:
        return web.json_response(
            {"error": "Document tools failed to load; see the application log for the cause"},
            status=404,
        )

    data = await read_json_object(request)
    if data is None:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    bad = non_string_field(data, ("title",))
    if bad:
        return bad
    url = data.get("url", "")
    url = url.strip() if isinstance(url, str) else ""
    if not url:
        return web.json_response({"error": "url is required"}, status=400)
    try:
        discussion_id = int_field(data, "discussion_id", 0)
    except ValueError as e:
        return web.json_response({"error": str(e)}, status=400)

    result = await app_inst.add_document_from_url(
        url=url,
        discussion_id=discussion_id,
        title=data.get("title", ""),
    )
    if "error" in result:
        return web.json_response(result, status=400)
    return web.json_response(result)


async def handle_document_list(request: web.Request) -> web.StreamResponse:
    """List documents for a discussion."""
    app_inst = await session_app(request)
    if isinstance(app_inst, web.Response):
        return app_inst

    try:
        discussion_id = int(request.match_info["discussion_id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid discussion ID"}, status=400)
    docs = app_inst.get_discussion_documents(discussion_id)
    return web.json_response({"documents": docs})


async def handle_document_remove(request: web.Request) -> web.StreamResponse:
    """Remove a document from a discussion."""
    app_inst = await session_app(request)
    if isinstance(app_inst, web.Response):
        return app_inst

    try:
        document_id = int(request.match_info["document_id"])
        discussion_id = int(request.match_info["discussion_id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid ID"}, status=400)
    result = app_inst.remove_document(document_id, discussion_id)
    return web.json_response(result)


async def handle_document_delete(request: web.Request) -> web.StreamResponse:
    """Permanently delete a document."""
    app_inst = await session_app(request)
    if isinstance(app_inst, web.Response):
        return app_inst

    try:
        document_id = int(request.match_info["document_id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid document ID"}, status=400)
    result = app_inst.delete_document(document_id)
    return web.json_response(result)
