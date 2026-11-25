"""Characterization tests for the ``consensus.tools_image`` tool handlers.

Split from ``tests/test_tools_image.py`` (#102) to keep both modules under
the 500-line guideline: the provider and the ``list_images``,
``add_image_url`` and ``describe_image`` handlers, driven through
``create_image_provider(...).execute``. Fakes sit at the network edges;
see ``tests/image_helpers.py``.
"""

import json
import os

import pytest

import consensus.tools_image as ti
from consensus.tools import ToolContext
from tests.image_helpers import (
    FakeApp, FakePricing, FakeVisionClient, fake_dns, install_transport, make_image, patch_image_module, png_response,
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
    install_transport(monkeypatch, png_response)
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


async def test_add_image_url_reports_fetch_failure(tmp_db, images_dir,
                                                  monkeypatch):
    fake_dns(monkeypatch, {"internal.test": "10.0.0.1"})
    install_transport(monkeypatch, png_response)
    result = await _run(tmp_db, None, "add_image_url",
                        {"url": "http://internal.test/a.png"}, _ctx())
    assert result.is_error
    assert result.content.startswith("Failed to fetch image: ")


async def test_add_image_url_reports_ingest_error(tmp_db, images_dir,
                                                  monkeypatch):
    async def fake_fetch(url, deadline=None):
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
    # The OSError names the storage path, so only the log keeps it (#92).
    assert result.content == "Failed to load image (FileNotFoundError)"


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
