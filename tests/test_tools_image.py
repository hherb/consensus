"""Characterization tests for ``consensus.tools_image`` (issue #61).

Written before the module is split into a package, against its public
entry points only (the storage/fetch/vision functions other modules import,
and ``create_image_provider(...).execute``), so they need no edits when the
code moves. Fakes sit at the network edges; see ``tests/image_helpers.py``.
"""

import base64
import json
import logging
import os
import sys

import httpx
import pytest

import consensus.tools_image as ti
from consensus.tools import ToolContext
from tests.image_helpers import (
    FakeApp, FakePricing, FakeVisionClient, fake_dns, image_size,
    install_transport, make_image, patch_image_module,
)

PNG_1PX = make_image(1, 1)


@pytest.fixture
def vision_client(monkeypatch):
    """Replace ``AIClient`` in the image modules with a recording fake."""
    FakeVisionClient.reset()
    patch_image_module(monkeypatch, "AIClient", FakeVisionClient)
    yield FakeVisionClient
    FakeVisionClient.reset()


@pytest.fixture
def discussion_id(tmp_db):
    """A real discussion row to attach images to."""
    return tmp_db.create_discussion("Topic")


def _ctx(entity_id: int = 0, discussion_id: int = 0) -> ToolContext:
    return ToolContext(caller_entity_id=entity_id, discussion_id=discussion_id)


async def _run(db, app, tool: str, args: dict, ctx: ToolContext):
    provider = ti.create_image_provider(db, app)
    return await provider.execute(tool, args, ctx)


# --- Vision model detection ---------------------------------------------------

def test_pricing_cache_answer_wins_over_patterns():
    pricing = FakePricing({"gpt-4o": False, "mystery-model": True})
    assert ti.is_vision_capable("gpt-4o", pricing_cache=pricing) is False
    assert ti.is_vision_capable("mystery-model", pricing_cache=pricing) is True


def test_unknown_model_falls_back_to_name_patterns():
    pricing = FakePricing()
    assert ti.is_vision_capable("Claude-Sonnet-4", pricing_cache=pricing)
    assert not ti.is_vision_capable("llama3-8b", pricing_cache=pricing)


def test_no_pricing_cache_uses_patterns():
    assert ti.is_vision_capable("gemini-2.5-pro")
    assert not ti.is_vision_capable("mistral-7b")


# --- Storage -------------------------------------------------------------------

def test_save_writes_file_with_unique_safe_name(images_dir):
    path, width, height, size = ti.save_image_file(
        PNG_1PX, "../../etc/my pic!.png", "image/png")
    assert "/" not in path and ".." not in path.replace("..png", "")
    assert path.endswith("_mypic.png")
    assert (width, height, size) == (1, 1, len(PNG_1PX))
    assert (images_dir / path).read_bytes() == PNG_1PX


def test_save_appends_extension_for_mime(images_dir):
    path, *_ = ti.save_image_file(make_image(2, 2, "JPEG"), "photo",
                                  "image/jpeg")
    assert path.endswith("_photo.jpg")


def test_save_unknown_mime_defaults_to_png_extension(images_dir):
    path, *_ = ti.save_image_file(PNG_1PX, "x", "image/x-unknown")
    assert path.endswith(".png")


def test_save_empty_filename_becomes_image(images_dir):
    path, *_ = ti.save_image_file(PNG_1PX, "///", "image/png")
    assert path.endswith("_image.png")


def test_save_truncates_long_filename(images_dir):
    path, *_ = ti.save_image_file(PNG_1PX, "a" * 300 + ".png", "image/png")
    prefix, name = path.split("_", 1)
    assert len(name) <= 100 + len(".png")


def test_save_resizes_oversized_png(images_dir):
    big = make_image(ti.MAX_IMAGE_DIMENSION * 2, 10)
    path, width, height, size = ti.save_image_file(big, "big.png", "image/png")
    stored = (images_dir / path).read_bytes()
    assert width == ti.MAX_IMAGE_DIMENSION
    assert image_size(stored) == (width, height)
    assert size == len(stored)


def test_save_resizes_oversized_webp(images_dir):
    from tests.image_helpers import image_format
    big = make_image(ti.MAX_IMAGE_DIMENSION + 10, 10, "WEBP")
    path, width, _h, _s = ti.save_image_file(big, "big.webp", "image/webp")
    assert width == ti.MAX_IMAGE_DIMENSION
    assert image_format((images_dir / path).read_bytes()) == "WEBP"


def test_save_without_pillow_keeps_raw_bytes(images_dir, monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    path, width, height, size = ti.save_image_file(PNG_1PX, "a.png",
                                                   "image/png")
    assert (width, height) == (None, None)
    assert (images_dir / path).read_bytes() == PNG_1PX


def test_save_undecodable_bytes_is_logged_and_kept(images_dir, caplog):
    with caplog.at_level(logging.WARNING, logger="consensus.tools_image"):
        path, width, height, _ = ti.save_image_file(b"not an image",
                                                    "a.png", "image/png")
    assert (width, height) == (None, None)
    assert (images_dir / path).read_bytes() == b"not an image"
    assert "Pillow processing failed" in caplog.text


def test_load_and_base64_round_trip(images_dir):
    path, *_ = ti.save_image_file(PNG_1PX, "a.png", "image/png")
    assert ti.load_image_file(path) == PNG_1PX
    url = ti.image_to_base64_url(path, "image/png")
    assert url == "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()


@pytest.mark.parametrize("bad", ["../outside.png", "/etc/passwd", ".."])
def test_load_rejects_path_traversal(images_dir, bad):
    with pytest.raises(ValueError, match="Path traversal"):
        ti.load_image_file(bad)


def test_delete_removes_file_and_tolerates_missing(images_dir):
    path, *_ = ti.save_image_file(PNG_1PX, "a.png", "image/png")
    ti.delete_image_file(path)
    assert not (images_dir / path).exists()
    ti.delete_image_file(path)  # already gone: no error


def test_delete_rejects_path_traversal(images_dir):
    with pytest.raises(ValueError):
        ti.delete_image_file("../x.png")


# --- Ingestion -----------------------------------------------------------------

async def test_ingest_stores_row_file_and_link(tmp_db, images_dir,
                                               discussion_id):
    result = await ti.ingest_image(
        tmp_db, PNG_1PX, "dir/Chart.png", "image/png",
        discussion_id=discussion_id, source_url="https://h/x.png",
        source_type="url", uploader_entity_id=None,
    )
    row = tmp_db.get_image(result["image_id"])
    assert result["title"] == "Chart" == row["title"]
    assert row["source_url"] == "https://h/x.png"
    assert row["source_type"] == "url"
    assert (images_dir / result["filename"]).exists()
    linked = tmp_db.get_discussion_images(discussion_id)
    assert [img["id"] for img in linked] == [result["image_id"]]


async def test_ingest_explicit_title_and_no_discussion(tmp_db, images_dir):
    result = await ti.ingest_image(tmp_db, PNG_1PX, "a.png", "image/png",
                                   title="My title")
    assert result["title"] == "My title"
    assert result["mime_type"] == "image/png"


async def test_ingest_rejects_oversized(tmp_db, images_dir, monkeypatch):
    patch_image_module(monkeypatch, "MAX_IMAGE_BYTES", 4)
    result = await ti.ingest_image(tmp_db, PNG_1PX, "a.png", "image/png")
    assert "too large" in result["error"]
    assert list(images_dir.iterdir()) == []


async def test_ingest_rejects_unsupported_type(tmp_db, images_dir):
    result = await ti.ingest_image(tmp_db, b"x", "a.bmp", "image/bmp")
    assert result == {"error": "Unsupported image type: image/bmp"}


# --- URL fetch -----------------------------------------------------------------

def _png_response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, content=PNG_1PX,
                          headers={"content-type": "image/png; charset=x"})


async def test_fetch_returns_bytes_filename_and_bare_mime(monkeypatch):
    fake_dns(monkeypatch)
    seen = install_transport(monkeypatch, _png_response)
    content, filename, mime = await ti.fetch_image_from_url(
        "https://img.test/a/b/pic.png?x=1")
    assert (content, filename, mime) == (PNG_1PX, "pic.png", "image/png")
    assert str(seen[0].url) == "https://img.test/a/b/pic.png?x=1"


async def test_fetch_path_without_name_is_image(monkeypatch):
    fake_dns(monkeypatch)
    install_transport(monkeypatch, _png_response)
    _, filename, _ = await ti.fetch_image_from_url("https://img.test/")
    assert filename == "image"


async def test_fetch_missing_content_type_assumes_png(monkeypatch):
    fake_dns(monkeypatch)
    install_transport(monkeypatch,
                      lambda r: httpx.Response(200, content=PNG_1PX))
    _, _, mime = await ti.fetch_image_from_url("https://img.test/p")
    assert mime == "image/png"


@pytest.mark.parametrize("url", ["ftp://img.test/a.png", "file:///etc/x"])
async def test_fetch_rejects_non_http_scheme(url):
    with pytest.raises(ValueError, match="Unsupported URL scheme"):
        await ti.fetch_image_from_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254",
                                "::1", "192.168.0.1"])
async def test_fetch_rejects_private_addresses(monkeypatch, ip):
    fake_dns(monkeypatch, {"internal.test": ip})
    seen = install_transport(monkeypatch, _png_response)
    with pytest.raises(ValueError, match="private/internal"):
        await ti.fetch_image_from_url("http://internal.test/a.png")
    assert seen == []


async def test_fetch_rejects_unresolvable_host(monkeypatch):
    fake_dns(monkeypatch, fail={"nowhere.test"})
    with pytest.raises(ValueError, match="private/internal"):
        await ti.fetch_image_from_url("http://nowhere.test/a.png")


async def test_fetch_rejects_oversized_body(monkeypatch):
    fake_dns(monkeypatch)
    patch_image_module(monkeypatch, "MAX_IMAGE_BYTES", 4)
    install_transport(monkeypatch, _png_response)
    with pytest.raises(ValueError, match="too large"):
        await ti.fetch_image_from_url("https://img.test/a.png")


async def test_fetch_rejects_non_image_content(monkeypatch):
    fake_dns(monkeypatch)
    install_transport(monkeypatch, lambda r: httpx.Response(
        200, content=b"<html>", headers={"content-type": "text/html"}))
    with pytest.raises(ValueError, match="supported image type: text/html"):
        await ti.fetch_image_from_url("https://img.test/a.png")


# --- Multimodal context blocks -------------------------------------------------

def _stored(images_dir, **extra) -> dict:
    path, *_ = ti.save_image_file(PNG_1PX, "a.png", "image/png")
    return {"id": 1, "storage_path": path, "mime_type": "image/png", **extra}


def test_blocks_prepend_labels_then_image_urls(images_dir):
    images = [
        _stored(images_dir, id=1, title="Chart"),
        _stored(images_dir, id=2, title="", original_filename="orig.png"),
        _stored(images_dir, id=3),
    ]
    blocks = ti.build_image_content_blocks(images)
    assert blocks[0] == {"type": "text", "text": (
        "Images shared in this discussion:\n"
        "[Image 1] Chart\n[Image 2] orig.png\n[Image 3] Image 3")}
    assert [b["type"] for b in blocks[1:]] == ["image_url"] * 3
    assert blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert blocks[1]["image_url"]["detail"] == "auto"


def test_blocks_skip_unloadable_image_and_log(images_dir, caplog):
    good = _stored(images_dir, id=1, title="Good")
    missing = {"id": 2, "storage_path": "gone.png", "mime_type": "image/png"}
    with caplog.at_level(logging.WARNING, logger="consensus.tools_image"):
        blocks = ti.build_image_content_blocks([missing, good])
    assert len(blocks) == 2
    assert "[Image 2]" not in blocks[0]["text"]
    assert "Failed to load image 2" in caplog.text


def test_blocks_empty_when_nothing_loads(images_dir):
    missing = {"id": 2, "storage_path": "gone.png", "mime_type": "image/png"}
    assert ti.build_image_content_blocks([missing]) == []
    assert ti.build_image_content_blocks([]) == []


# --- Provider ------------------------------------------------------------------

async def test_provider_registers_three_tools(tmp_db):
    provider = ti.create_image_provider(tmp_db)
    names = sorted(t.name for t in await provider.list_tools())
    assert names == ["add_image_url", "describe_image", "list_images"]
    assert provider.name == "images"


# --- list_images ---------------------------------------------------------------

async def test_list_images_empty(tmp_db, discussion_id):
    result = await _run(tmp_db, None, "list_images", {},
                        _ctx(discussion_id=discussion_id))
    assert result.content == "No images attached to this discussion."
    assert not result.is_error


async def test_list_images_formats_dims_and_snippets(tmp_db, images_dir,
                                                     discussion_id):
    first = await ti.ingest_image(tmp_db, PNG_1PX, "one.png", "image/png",
                                  discussion_id=discussion_id)
    second = await ti.ingest_image(tmp_db, b"<svg/>", "two.svg",
                                   "image/svg+xml",
                                   discussion_id=discussion_id)
    tmp_db.update_image_description(first["image_id"], "d" * 150)
    tmp_db.update_image_description(second["image_id"], "short")
    result = await _run(tmp_db, None, "list_images", {},
                        _ctx(discussion_id=discussion_id))
    lines = result.content.split("\n")
    assert lines[0] == "Images in this discussion — 2 total:"
    assert lines[2] == (f"  [ID {first['image_id']}] one "
                        f"(image/png, 1x1, {len(PNG_1PX)} bytes)")
    assert lines[3] == "    " + "d" * 100 + "..."
    assert lines[4] == (f"  [ID {second['image_id']}] two "
                        f"(image/svg+xml, 6 bytes)")
    assert lines[5] == "    short"
    assert result.metadata == {"count": 2}


# --- add_image_url -------------------------------------------------------------

async def test_add_image_url_requires_url(tmp_db):
    result = await _run(tmp_db, None, "add_image_url", {"url": "  "}, _ctx())
    assert result.is_error and result.content == "url is required."


async def test_add_image_url_ingests_and_links(tmp_db, images_dir,
                                               discussion_id, monkeypatch,
                                               sample_ai_entity):
    fake_dns(monkeypatch)
    install_transport(monkeypatch, _png_response)
    result = await _run(
        tmp_db, None, "add_image_url",
        {"url": "https://img.test/pic.png", "title": " Pic "},
        _ctx(sample_ai_entity, discussion_id))
    assert not result.is_error
    payload = json.loads(result.content)
    assert payload == result.metadata
    row = tmp_db.get_image(payload["image_id"])
    assert row["title"] == "Pic"
    assert row["source_type"] == "url"
    assert row["uploader_entity_id"] == sample_ai_entity
    assert len(tmp_db.get_discussion_images(discussion_id)) == 1


async def test_add_image_url_reports_fetch_failure(tmp_db, monkeypatch):
    fake_dns(monkeypatch, {"internal.test": "10.0.0.1"})
    result = await _run(tmp_db, None, "add_image_url",
                        {"url": "http://internal.test/a.png"}, _ctx())
    assert result.is_error
    assert result.content.startswith("Failed to fetch image: ")


async def test_add_image_url_reports_ingest_error(tmp_db, images_dir,
                                                  monkeypatch):
    async def fake_fetch(url):
        return b"x", "a.bmp", "image/bmp"

    patch_image_module(monkeypatch, "fetch_image_from_url", fake_fetch)
    result = await _run(tmp_db, None, "add_image_url",
                        {"url": "https://img.test/a.bmp"}, _ctx())
    assert result.is_error
    assert result.content == "Unsupported image type: image/bmp"


# --- describe_image ------------------------------------------------------------

@pytest.fixture
def stored_image(tmp_db, images_dir):
    """An ingested PNG; returns its image id."""
    path, w, h, size = ti.save_image_file(PNG_1PX, "a.png", "image/png")
    return tmp_db.add_image(
        filename=path, original_filename="a.png", title="A", description="",
        mime_type="image/png", width=w, height=h, file_size=size,
        storage_path=path, source_type="upload",
    )


def _vision_db(tmp_db, vision: dict):
    tmp_db.pricing = FakePricing(vision)
    return tmp_db


async def test_describe_requires_image_id(tmp_db):
    result = await _run(tmp_db, None, "describe_image", {}, _ctx())
    assert result.is_error and result.content == "image_id is required."


async def test_describe_unknown_image(tmp_db):
    result = await _run(tmp_db, None, "describe_image", {"image_id": 99},
                        _ctx())
    assert result.is_error and result.content == "Image 99 not found."


async def test_describe_unloadable_file(tmp_db, images_dir, stored_image):
    os.remove(images_dir / tmp_db.get_image(stored_image)["storage_path"])
    result = await _run(tmp_db, None, "describe_image",
                        {"image_id": stored_image}, _ctx())
    assert result.is_error
    assert result.content.startswith("Failed to load image: ")


async def test_describe_uses_vision_capable_caller(tmp_db, stored_image,
                                                   sample_ai_entity,
                                                   sample_provider,
                                                   vision_client):
    db = _vision_db(tmp_db, {"test-model": True})
    app = FakeApp(db)
    result = await _run(db, app, "describe_image",
                        {"image_id": stored_image}, _ctx(sample_ai_entity))
    assert not result.is_error
    assert json.loads(result.content) == {
        "image_id": stored_image, "title": "A",
        "description": "A red square."}
    client = vision_client.instances[0]
    assert client.closed
    assert client.base_url == "http://localhost:11434/v1"
    assert client.api_key == f"key-{sample_provider}"
    assert app.key_requests == [(sample_provider, "TEST_API_KEY")]
    call = client.calls[0]
    assert call["model"] == "test-model"
    assert call["max_tokens"] == 512
    user = call["messages"][1]["content"]
    assert user[0] == {"type": "text", "text": "Describe this image in detail."}
    assert user[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert user[1]["image_url"]["detail"] == "high"


async def test_describe_caches_general_description(tmp_db, stored_image,
                                                   sample_ai_entity,
                                                   vision_client):
    db = _vision_db(tmp_db, {"test-model": True})
    await _run(db, FakeApp(db), "describe_image",
               {"image_id": stored_image}, _ctx(sample_ai_entity))
    assert db.get_image(stored_image)["description"] == "A red square."


async def test_describe_specific_question_is_not_cached(tmp_db, stored_image,
                                                        sample_ai_entity,
                                                        vision_client):
    db = _vision_db(tmp_db, {"test-model": True})
    await _run(db, FakeApp(db), "describe_image",
               {"image_id": stored_image, "question": "What colour is it?"},
               _ctx(sample_ai_entity))
    assert vision_client.instances[0].calls[0]["messages"][1]["content"][0][
        "text"] == "What colour is it?"
    assert db.get_image(stored_image)["description"] == ""


async def test_describe_keeps_existing_description(tmp_db, stored_image,
                                                   sample_ai_entity,
                                                   vision_client):
    db = _vision_db(tmp_db, {"test-model": True})
    db.update_image_description(stored_image, "Original")
    await _run(db, FakeApp(db), "describe_image",
               {"image_id": stored_image}, _ctx(sample_ai_entity))
    assert db.get_image(stored_image)["description"] == "Original"


async def test_describe_falls_back_to_vision_moderator(tmp_db, stored_image,
                                                       sample_ai_entity,
                                                       vision_client):
    mod_provider = tmp_db.add_provider("ModProv", "http://mod/v1", "MOD_KEY")
    moderator = tmp_db.add_entity("Mod", "ai", "#000", mod_provider,
                                  "vision-model", 0.5, 256, "")
    db = _vision_db(tmp_db, {"test-model": False, "vision-model": True})
    app = FakeApp(db, moderator_id=moderator)
    await _run(db, app, "describe_image", {"image_id": stored_image},
               _ctx(sample_ai_entity))
    client = vision_client.instances[0]
    assert client.base_url == "http://mod/v1"
    assert client.calls[0]["model"] == "vision-model"
    assert app.key_requests == [(mod_provider, "MOD_KEY")]


@pytest.mark.parametrize("moderator_vision", [False, None])
async def test_describe_keeps_caller_when_moderator_cannot_help(
        tmp_db, stored_image, sample_ai_entity, vision_client,
        moderator_vision):
    mod_provider = tmp_db.add_provider("ModProv", "http://mod/v1", "MOD_KEY")
    moderator = tmp_db.add_entity("Mod", "ai", "#000", mod_provider,
                                  "plain-model", 0.5, 256, "")
    db = _vision_db(tmp_db, {"test-model": False,
                             "plain-model": moderator_vision})
    await _run(db, FakeApp(db, moderator_id=moderator), "describe_image",
               {"image_id": stored_image}, _ctx(sample_ai_entity))
    assert vision_client.instances[0].calls[0]["model"] == "test-model"


async def test_describe_ignores_missing_moderator_row(tmp_db, stored_image,
                                                      sample_ai_entity,
                                                      vision_client):
    db = _vision_db(tmp_db, {"test-model": False})
    await _run(db, FakeApp(db, moderator_id=12345), "describe_image",
               {"image_id": stored_image}, _ctx(sample_ai_entity))
    assert vision_client.instances[0].calls[0]["model"] == "test-model"


async def test_describe_without_discussion_keeps_caller(tmp_db, stored_image,
                                                        sample_ai_entity,
                                                        vision_client):
    db = _vision_db(tmp_db, {"test-model": False})
    await _run(db, FakeApp(db), "describe_image", {"image_id": stored_image},
               _ctx(sample_ai_entity))
    assert vision_client.instances[0].calls[0]["model"] == "test-model"
