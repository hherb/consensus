"""Characterization tests for ``consensus.tools_image`` (issue #61).

Written before the module is split into a package, against its public
entry points only (the storage/fetch/vision functions other modules import,
and ``create_image_provider(...).execute``), so they need no edits when the
code moves. Fakes sit at the network edges; see ``tests/image_helpers.py``.
"""

import asyncio
import base64
import logging
import sys

import httpx
import pytest

import consensus.tools_image as ti
from consensus.tools_image.constants import MAX_IMAGE_DIMENSION
from consensus.http_fetch import FETCH_MAX_RETRIES, FetchError, FetchUnavailable
from tests.image_helpers import (
    FakePricing, fake_dns, image_size,
    install_transport, make_image, patch_image_module, png_response,
)

PNG_1PX = make_image(1, 1)


@pytest.fixture
def discussion_id(tmp_db):
    """A real discussion row to attach images to."""
    return tmp_db.create_discussion("Topic")



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
    big = make_image(MAX_IMAGE_DIMENSION * 2, 10)
    path, width, height, size = ti.save_image_file(big, "big.png", "image/png")
    stored = (images_dir / path).read_bytes()
    assert width == MAX_IMAGE_DIMENSION
    assert image_size(stored) == (width, height)
    assert size == len(stored)


def test_save_resizes_oversized_webp(images_dir):
    from tests.image_helpers import image_format
    big = make_image(MAX_IMAGE_DIMENSION + 10, 10, "WEBP")
    path, width, _h, _s = ti.save_image_file(big, "big.webp", "image/webp")
    assert width == MAX_IMAGE_DIMENSION
    assert image_format((images_dir / path).read_bytes()) == "WEBP"


def test_save_without_pillow_keeps_raw_bytes(images_dir, monkeypatch):
    monkeypatch.setitem(sys.modules, "PIL", None)
    path, width, height, size = ti.save_image_file(PNG_1PX, "a.png",
                                                   "image/png")
    assert (width, height) == (None, None)
    assert (images_dir / path).read_bytes() == PNG_1PX


def test_save_refuses_undecodable_raster_bytes(images_dir):
    """Stored, they would reach a vision model and sink the whole turn."""
    with pytest.raises(ValueError, match="Not a valid image/png image"):
        ti.save_image_file(b"not an image", "a.png", "image/png")
    assert list(images_dir.iterdir()) == []


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

async def test_fetch_returns_bytes_filename_and_bare_mime(monkeypatch):
    fake_dns(monkeypatch)
    seen = install_transport(monkeypatch, png_response)
    content, filename, mime = await ti.fetch_image_from_url(
        "https://img.test/a/b/pic.png?x=1")
    assert (content, filename, mime) == (PNG_1PX, "pic.png", "image/png")
    assert str(seen[0].url) == "https://img.test/a/b/pic.png?x=1"


async def test_fetch_path_without_name_is_image(monkeypatch):
    fake_dns(monkeypatch)
    install_transport(monkeypatch, png_response)
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
    with pytest.raises(FetchError, match="Unsupported URL scheme"):
        await ti.fetch_image_from_url(url)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "169.254.169.254",
                                "::1", "192.168.0.1"])
async def test_fetch_rejects_private_addresses(monkeypatch, ip):
    fake_dns(monkeypatch, {"internal.test": ip})
    seen = install_transport(monkeypatch, png_response)
    with pytest.raises(FetchError, match="private/internal"):
        await ti.fetch_image_from_url("http://internal.test/a.png")
    assert seen == []


async def test_fetch_retries_then_reports_an_unresolvable_host(monkeypatch):
    """A DNS failure is often transient and is not an SSRF refusal."""
    looked_up = fake_dns(monkeypatch, fail={"nowhere.test"})
    seen = install_transport(monkeypatch, png_response)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    with pytest.raises(FetchUnavailable, match="could not resolve nowhere.test"):
        await ti.fetch_image_from_url("http://nowhere.test/a.png")
    assert seen == []
    assert looked_up == ["nowhere.test"] * FETCH_MAX_RETRIES


async def test_fetch_rejects_oversized_body(monkeypatch):
    fake_dns(monkeypatch)
    patch_image_module(monkeypatch, "MAX_IMAGE_BYTES", 4)
    install_transport(monkeypatch, png_response)
    with pytest.raises(FetchError, match="too large"):
        await ti.fetch_image_from_url("https://img.test/a.png")


async def test_fetch_rejects_non_image_content(monkeypatch):
    fake_dns(monkeypatch)
    install_transport(monkeypatch, lambda r: httpx.Response(
        200, content=b"<html>", headers={"content-type": "text/html"}))
    with pytest.raises(FetchError, match="supported image type: text/html"):
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
    # Labelled, so the model knows the image exists but was not sent.
    assert "[Image 2] Image 2 (could not be loaded)" in blocks[0]["text"]
    assert "Failed to load image 2" in caplog.text


def test_blocks_label_an_image_that_cannot_load(images_dir):
    missing = {"id": 2, "storage_path": "gone.png", "mime_type": "image/png"}
    blocks = ti.build_image_content_blocks([missing])
    assert len(blocks) == 1 and blocks[0]["type"] == "text"
    assert ti.build_image_content_blocks([]) == []
