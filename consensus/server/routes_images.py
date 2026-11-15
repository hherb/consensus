"""``/api/images/*``: image upload, URL fetch, listing and serving."""

from aiohttp import web

from .constants import IMAGE_SERVE_CSP
from .context import CTX_KEY, get_app_for_request
from .request_io import UploadError, read_json_object, read_upload


async def handle_image_upload(request: web.Request) -> web.StreamResponse:
    """Upload an image file via multipart form data."""
    ctx = request.app[CTX_KEY]
    if ctx.auth_required and not request.get("auth_user"):
        return web.json_response({"error": "Authentication required"}, status=401)
    app_inst, _sid = await get_app_for_request(request)
    if app_inst is None:
        return web.json_response({"error": "Server at capacity"}, status=503)

    from ..tools_image.constants import MAX_IMAGE_BYTES
    try:
        file_data, filename, mime_type, discussion_id, title = \
            await read_upload(request, MAX_IMAGE_BYTES, "image",
                              "image/png")
    except UploadError as e:
        return web.json_response({"error": str(e)}, status=e.status)

    result = await app_inst.add_image(
        filename=filename,
        content_bytes=file_data,
        mime_type=mime_type,
        discussion_id=discussion_id,
        title=title,
    )
    if "error" in result:
        return web.json_response(result, status=400)
    return web.json_response(result)


async def handle_image_add_url(request: web.Request) -> web.StreamResponse:
    """Add an image by fetching from a URL."""
    ctx = request.app[CTX_KEY]
    if ctx.auth_required and not request.get("auth_user"):
        return web.json_response({"error": "Authentication required"}, status=401)
    app_inst, _sid = await get_app_for_request(request)
    if app_inst is None:
        return web.json_response({"error": "Server at capacity"}, status=503)

    data = await read_json_object(request)
    if data is None:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    url = data.get("url", "")
    url = url.strip() if isinstance(url, str) else ""
    if not url:
        return web.json_response({"error": "url is required"}, status=400)
    try:
        discussion_id = int(data.get("discussion_id", 0))
    except (TypeError, ValueError):
        return web.json_response(
            {"error": "discussion_id must be an integer"}, status=400)

    result = await app_inst.add_image_from_url(
        url=url,
        discussion_id=discussion_id,
        title=data.get("title", ""),
    )
    if "error" in result:
        return web.json_response(result, status=400)
    return web.json_response(result)


async def handle_image_list(request: web.Request) -> web.StreamResponse:
    """List images for a discussion."""
    ctx = request.app[CTX_KEY]
    if ctx.auth_required and not request.get("auth_user"):
        return web.json_response({"error": "Authentication required"}, status=401)
    app_inst, _sid = await get_app_for_request(request)
    if app_inst is None:
        return web.json_response({"error": "Server at capacity"}, status=503)

    try:
        discussion_id = int(request.match_info["discussion_id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid discussion ID"}, status=400)
    images = app_inst.get_discussion_images(discussion_id)
    return web.json_response({"images": images})


async def handle_image_serve(request: web.Request) -> web.StreamResponse:
    """Serve an image file by ID."""
    ctx = request.app[CTX_KEY]
    if ctx.auth_required and not request.get("auth_user"):
        return web.json_response({"error": "Authentication required"}, status=401)
    app_inst, _sid = await get_app_for_request(request)
    if app_inst is None:
        return web.json_response({"error": "Server at capacity"}, status=503)

    try:
        image_id = int(request.match_info["image_id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid image ID"}, status=400)
    result = app_inst.get_image_data(image_id)
    if result is None:
        return web.json_response({"error": "Image not found"}, status=404)

    data, mime_type, filename = result
    return web.Response(
        body=data,
        content_type=mime_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "Content-Disposition": f'inline; filename="{filename}"',
            # An uploaded or AI-fetched SVG can carry script; opened
            # directly it would run in the app's origin.
            "Content-Security-Policy": IMAGE_SERVE_CSP,
        },
    )


async def handle_image_remove(request: web.Request) -> web.StreamResponse:
    """Remove an image from a discussion."""
    ctx = request.app[CTX_KEY]
    if ctx.auth_required and not request.get("auth_user"):
        return web.json_response({"error": "Authentication required"}, status=401)
    app_inst, _sid = await get_app_for_request(request)
    if app_inst is None:
        return web.json_response({"error": "Server at capacity"}, status=503)

    try:
        image_id = int(request.match_info["image_id"])
        discussion_id = int(request.match_info["discussion_id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid ID"}, status=400)
    result = app_inst.remove_discussion_image(image_id, discussion_id)
    return web.json_response(result)


async def handle_image_delete(request: web.Request) -> web.StreamResponse:
    """Permanently delete an image."""
    ctx = request.app[CTX_KEY]
    if ctx.auth_required and not request.get("auth_user"):
        return web.json_response({"error": "Authentication required"}, status=401)
    app_inst, _sid = await get_app_for_request(request)
    if app_inst is None:
        return web.json_response({"error": "Server at capacity"}, status=503)

    try:
        image_id = int(request.match_info["image_id"])
    except (ValueError, KeyError):
        return web.json_response({"error": "Invalid image ID"}, status=400)
    result = app_inst.delete_image(image_id)
    return web.json_response(result)
