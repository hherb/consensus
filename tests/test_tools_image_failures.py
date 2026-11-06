"""Defects found while characterizing ``consensus.tools_image`` (#61).

Each test failed before its fix:

* a failed vision call was returned as a successful description and
  **persisted** as the image's description;
* a failed description-cache write was swallowed with ``except: pass``;
* resizing re-encoded a GIF as JPEG (bytes no longer matched the stored
  MIME type) and, when that save failed, recorded the *resized* dimensions
  beside the raw original bytes;
* every SVG was logged as a Pillow failure;
* SVGs were sent to vision models, which accept only raster formats, so a
  single attached SVG failed every vision-model turn;
* the SSRF guard checked only the first URL, so a public URL redirecting
  to an internal address was fetched; CGNAT and multicast passed it;
* image fetches had no retry (#88) and buffered the whole body before
  checking its size.
"""

import json
import logging

import httpx
import pytest

import consensus.tools_image as ti
from consensus.tools_image.constants import MAX_IMAGE_DIMENSION
from consensus.http_fetch import FetchError
from consensus.tools import ToolContext
from tests.image_helpers import (
    FakeApp, FakePricing, FakeVisionClient, fake_dns, image_format,
    image_size, install_transport, make_image, patch_image_module,
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
def stored_image(tmp_db, images_dir):
    """An ingested PNG with an empty description; returns its id."""
    path, w, h, size = ti.save_image_file(PNG_1PX, "a.png", "image/png")
    return tmp_db.add_image(
        filename=path, original_filename="a.png", title="A", description="",
        mime_type="image/png", width=w, height=h, file_size=size,
        storage_path=path, source_type="upload",
    )


async def _describe(db, app, image_id, caller):
    tmp = ti.create_image_provider(db, app)
    return await tmp.execute("describe_image", {"image_id": image_id},
                             ToolContext(caller_entity_id=caller,
                                         discussion_id=0))


# --- describe_image failures ---------------------------------------------------

async def test_vision_failure_is_an_error_and_never_cached(
        tmp_db, stored_image, sample_ai_entity, vision_client, caplog):
    tmp_db.pricing = FakePricing({"test-model": True})
    vision_client.error = RuntimeError("provider down")
    with caplog.at_level(logging.WARNING, logger="consensus.tools_image"):
        result = await _describe(tmp_db, FakeApp(tmp_db), stored_image,
                                 sample_ai_entity)
    assert result.is_error
    assert "provider down" in result.content
    assert tmp_db.get_image(stored_image)["description"] == ""
    assert "provider down" in caplog.text
    assert vision_client.instances[0].closed


async def test_unknown_caller_is_an_error(tmp_db, stored_image, vision_client):
    tmp_db.pricing = FakePricing()
    result = await _describe(tmp_db, FakeApp(tmp_db), stored_image, 4242)
    assert result.is_error
    assert "caller" in result.content
    assert tmp_db.get_image(stored_image)["description"] == ""


async def test_cache_write_failure_is_logged_not_swallowed(
        tmp_db, stored_image, sample_ai_entity, vision_client, monkeypatch,
        caplog):
    tmp_db.pricing = FakePricing({"test-model": True})

    def broken(*args):
        raise RuntimeError("disk full")

    monkeypatch.setattr(tmp_db, "update_image_description", broken)
    with caplog.at_level(logging.WARNING, logger="consensus.tools_image"):
        result = await _describe(tmp_db, FakeApp(tmp_db), stored_image,
                                 sample_ai_entity)
    assert not result.is_error
    assert json.loads(result.content)["description"] == "A red square."
    assert "disk full" in caplog.text


# --- resize keeps format and honest dimensions ---------------------------------

def test_oversized_gif_stays_gif(images_dir):
    big = make_image(MAX_IMAGE_DIMENSION + 50, 10, "GIF", mode="P")
    path, width, height, size = ti.save_image_file(big, "big.gif",
                                                   "image/gif")
    stored = (images_dir / path).read_bytes()
    assert image_format(stored) == "GIF"
    assert image_size(stored) == (width, height)
    assert width == MAX_IMAGE_DIMENSION


def test_failed_resize_records_original_dimensions(images_dir, monkeypatch,
                                                   caplog):
    from PIL import Image

    def broken_save(self, *args, **kwargs):
        raise OSError("encoder missing")

    big = make_image(MAX_IMAGE_DIMENSION + 50, 10)
    monkeypatch.setattr(Image.Image, "save", broken_save)
    with caplog.at_level(logging.WARNING, logger="consensus.tools_image"):
        path, width, height, size = ti.save_image_file(big, "big.png",
                                                       "image/png")
    assert (images_dir / path).read_bytes() == big
    assert (width, height) == (MAX_IMAGE_DIMENSION + 50, 10)
    assert size == len(big)
    assert "encoder missing" in caplog.text


def test_svg_is_not_reported_as_a_pillow_failure(images_dir, caplog):
    with caplog.at_level(logging.WARNING, logger="consensus.tools_image"):
        path, width, height, _ = ti.save_image_file(b"<svg/>", "a.svg",
                                                    "image/svg+xml")
    assert (width, height) == (None, None)
    assert caplog.text == ""


# --- vision context only carries raster images ---------------------------------

def test_svg_is_listed_but_not_sent_to_vision_models(images_dir):
    png_path, *_ = ti.save_image_file(PNG_1PX, "a.png", "image/png")
    svg_path, *_ = ti.save_image_file(b"<svg/>", "d.svg", "image/svg+xml")
    blocks = ti.build_image_content_blocks([
        {"id": 1, "storage_path": png_path, "mime_type": "image/png",
         "title": "Photo"},
        {"id": 2, "storage_path": svg_path, "mime_type": "image/svg+xml",
         "title": "Diagram"},
    ])
    image_urls = [b["image_url"]["url"] for b in blocks
                  if b["type"] == "image_url"]
    assert len(image_urls) == 1
    assert image_urls[0].startswith("data:image/png;")
    assert "[Image 1] Photo" in blocks[0]["text"]
    assert "[Image 2] Diagram (image/svg+xml" in blocks[0]["text"]


def test_only_svgs_still_produce_the_listing(images_dir):
    svg_path, *_ = ti.save_image_file(b"<svg/>", "d.svg", "image/svg+xml")
    blocks = ti.build_image_content_blocks([
        {"id": 2, "storage_path": svg_path, "mime_type": "image/svg+xml",
         "title": "Diagram"}])
    assert [b["type"] for b in blocks] == ["text"]


# --- fetch: SSRF, retry, streaming cap -----------------------------------------

async def test_redirect_to_internal_address_is_refused(monkeypatch):
    fake_dns(monkeypatch, {"metadata.test": "169.254.169.254"})

    def handler(request):
        if request.url.host == "img.test":
            return httpx.Response(
                302, headers={"location": "http://metadata.test/creds"})
        return httpx.Response(200, content=PNG_1PX,
                              headers={"content-type": "image/png"})

    seen = install_transport(monkeypatch, handler)
    with pytest.raises(FetchError, match="private/internal"):
        await ti.fetch_image_from_url("https://img.test/a.png")
    assert [r.url.host for r in seen] == ["img.test"]


@pytest.mark.parametrize("ip", ["100.64.0.1", "224.0.0.1", "0.0.0.0",
                                "::ffff:127.0.0.1"])
async def test_non_public_ranges_are_refused(monkeypatch, ip):
    fake_dns(monkeypatch, {"odd.test": ip})
    seen = install_transport(monkeypatch, lambda r: httpx.Response(200))
    with pytest.raises(FetchError, match="private/internal"):
        await ti.fetch_image_from_url("http://odd.test/a.png")
    assert seen == []


async def test_transient_failure_is_retried(monkeypatch):
    fake_dns(monkeypatch)

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr("asyncio.sleep", no_sleep)
    answers = iter([
        httpx.Response(503),
        httpx.Response(200, content=PNG_1PX,
                       headers={"content-type": "image/png"}),
    ])
    seen = install_transport(monkeypatch, lambda r: next(answers))
    content, _, _ = await ti.fetch_image_from_url("https://img.test/a.png")
    assert content == PNG_1PX
    assert len(seen) == 2


async def test_oversized_stream_is_aborted(monkeypatch):
    fake_dns(monkeypatch)
    patch_image_module(monkeypatch, "MAX_IMAGE_BYTES", 25)
    produced = []

    async def body():
        for _ in range(100):
            produced.append(1)
            yield b"x" * 10

    install_transport(monkeypatch, lambda r: httpx.Response(
        200, content=body(), headers={"content-type": "image/png"}))
    with pytest.raises(FetchError, match="too large"):
        await ti.fetch_image_from_url("https://img.test/a.png")
    assert len(produced) < 100


# --- app start-up ---------------------------------------------------------------

def test_image_tools_load_failure_is_a_warning_with_traceback(
        app, monkeypatch, caplog):
    """No optional deps load at import, so a failure is a fault, not INFO."""
    def boom(db, app=None):
        raise ImportError("broken facade")

    monkeypatch.setattr("consensus.tools_image.create_image_provider", boom)
    with caplog.at_level(logging.WARNING, logger="consensus.app"):
        app._init_image_tools()
    assert app.images_available is False
    assert any(r.levelno == logging.WARNING
               and "Image tools failed to load" in r.getMessage()
               and r.exc_info for r in caplog.records)
