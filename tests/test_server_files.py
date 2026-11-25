"""Route tests for the memory-config, document and image endpoints.

Most tests replace the ``ConsensusApp`` method a route reaches with a
recorder, pinning how the HTTP request is translated into the call; the
image upload → serve round trip runs for real, and pins the CSP that keeps an
uploaded SVG from running script in the app's origin (#99).
"""

from __future__ import annotations

import aiohttp
import pytest

from consensus import server
from consensus.app import ConsensusApp
from tests.server_helpers import (  # noqa: F401  (fixtures)
    fast_hashing, module_of, multi_client, server_home, signed_in, single_client,
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f"
    b"\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


def record(monkeypatch, attr: str, result: object, awaitable: bool = False
           ) -> list:
    """Replace ``ConsensusApp.<attr>`` with a recorder; return its calls."""
    calls: list = []
    if awaitable:
        async def fake(self, *args, **kwargs):
            calls.append((args, kwargs))
            return result
    else:
        def fake(self, *args, **kwargs):
            calls.append((args, kwargs))
            return result
    monkeypatch.setattr(ConsensusApp, attr, fake)
    return calls


async def upload(client, path: str, form: aiohttp.FormData):
    """POST a multipart upload the way the web frontend does."""
    return await client.post(path, data=form, headers={server.CSRF_HEADER: "1"})


def multipart(file_bytes: bytes | None, filename: str = "f.bin",
              content_type: str = "application/octet-stream",
              **fields: str) -> aiohttp.FormData:
    """Build an upload form with an optional ``file`` part plus text fields."""
    form = aiohttp.FormData(default_to_multipart=True)
    if file_bytes is not None:
        form.add_field("file", file_bytes, filename=filename,
                       content_type=content_type)
    for name, value in fields.items():
        form.add_field(name, value)
    return form


class TestMemoryConfig:
    async def test_get_returns_the_config(self, single_client):
        resp = await single_client.get("/api/memory/config")
        assert resp.status == 200
        assert isinstance(await resp.json(), dict)

    async def test_put_writes_only_allowed_keys(self, single_client):
        resp = await single_client.put("/api/memory/config", json={
            "embedding_model": "nomic", "embedding_endpoint": 11434,
            "drop_tables": "yes"})
        assert (await resp.json()) == {"ok": True}
        config = await (await single_client.get("/api/memory/config")).json()
        assert config["embedding_model"] == "nomic"
        assert config["embedding_endpoint"] == "11434"
        assert "drop_tables" not in config

    async def test_put_bad_json_is_a_400(self, single_client):
        resp = await single_client.put("/api/memory/config", data=b"{",
                                       headers={"Content-Type":
                                                "application/json"})
        assert resp.status == 400

    @pytest.mark.parametrize("value", [None, {"a": 1}, [1]])
    async def test_put_non_scalar_value_is_a_400(self, single_client, value):
        """``None`` used to be saved as the string "None"."""
        resp = await single_client.put("/api/memory/config",
                                       json={"embedding_endpoint": value})
        assert resp.status == 400
        assert "embedding_endpoint" in (await resp.json())["error"]
        config = await (await single_client.get("/api/memory/config")).json()
        assert config.get("embedding_endpoint") != "None"

    async def test_get_failure_is_a_500(self, single_client, monkeypatch,
                                        caplog):
        from consensus.database import Database

        def boom(self):
            raise RuntimeError("disk I/O error")
        monkeypatch.setattr(Database, "get_memory_config", boom)
        resp = await single_client.get("/api/memory/config")
        assert resp.status == 500
        assert (await resp.json()) == {
            "error": "Could not read memory config (RuntimeError)"}
        assert "Could not read memory config" in caplog.text

    async def test_connection_test_reports_the_result(self, single_client,
                                                      monkeypatch):
        from consensus.tools_memory import EmbeddingClient

        async def ok(self):
            return True, "connected"
        monkeypatch.setattr(EmbeddingClient, "test_connection", ok)
        resp = await single_client.post("/api/memory/test", json={})
        assert (await resp.json()) == {"ok": True, "message": "connected"}

    async def test_connection_test_failure_is_reported(self, single_client,
                                                       monkeypatch, caplog):
        from consensus.tools_memory import EmbeddingClient

        async def boom(self):
            raise ConnectionError("refused")
        monkeypatch.setattr(EmbeddingClient, "test_connection", boom)
        resp = await single_client.post("/api/memory/test", json={})
        assert resp.status == 200
        assert (await resp.json()) == {
            "ok": False,
            "message": "Memory connection test failed (ConnectionError)"}
        assert "Memory connection test failed" in caplog.text


class TestFeatureUnavailable:
    """Routes answer 404 with a pointer to the log when tools failed to load."""

    @pytest.fixture
    async def bare_client(self, server_home, monkeypatch):
        from tests.server_helpers import make_client
        monkeypatch.setattr(ConsensusApp, "_init_memory_tools",
                            lambda self: None)
        monkeypatch.setattr(ConsensusApp, "_init_document_tools",
                            lambda self: None)
        client = await make_client(multi_user=False)
        yield client
        await client.close()

    @pytest.mark.parametrize("method,path,feature", [
        ("get", "/api/memory/config", "Memory"),
        ("put", "/api/memory/config", "Memory"),
        ("post", "/api/memory/test", "Memory"),
        ("post", "/api/documents/add-url", "Document"),
    ])
    async def test_unavailable_is_a_404(self, bare_client, method, path,
                                        feature):
        resp = await getattr(bare_client, method)(path, json={})
        assert resp.status == 404
        assert (await resp.json())["error"].startswith(
            f"{feature} tools failed to load")

    async def test_document_upload_unavailable(self, bare_client):
        resp = await upload(bare_client, "/api/documents/upload", multipart(b"x"))
        assert resp.status == 404


class TestDocuments:
    async def test_upload_forwards_the_parts(self, single_client, monkeypatch):
        calls = record(monkeypatch, "add_document", {"document_id": 1},
                       awaitable=True)
        resp = await upload(single_client, "/api/documents/upload", multipart(
            b"hello", filename="notes.md",
            content_type="text/markdown; charset=utf-8",
            discussion_id="12", title="Notes"))
        assert resp.status == 200
        assert (await resp.json()) == {"document_id": 1}
        assert calls == [((), {"filename": "notes.md", "content_bytes": b"hello",
                               "mime_type": "text/markdown",
                               "discussion_id": 12, "title": "Notes"})]

    async def test_upload_non_numeric_discussion_is_unbound(
            self, single_client, monkeypatch):
        calls = record(monkeypatch, "add_document", {}, awaitable=True)
        await upload(single_client, "/api/documents/upload", multipart(
            b"x", discussion_id="abc"))
        assert calls[0][1]["discussion_id"] == 0

    async def test_upload_unicode_digit_discussion_is_unbound(
            self, single_client, monkeypatch):
        # "\u00b2".isdigit() is true but int() rejects it.
        calls = record(monkeypatch, "add_document", {}, awaitable=True)
        resp = await upload(single_client, "/api/documents/upload", multipart(
            b"x", discussion_id="\u00b2"))
        assert resp.status == 200
        assert calls[0][1]["discussion_id"] == 0

    async def test_upload_non_utf8_field_names_the_field(self, single_client):
        form = aiohttp.FormData(default_to_multipart=True)
        form.add_field("file", b"x", filename="f.bin")
        form.add_field("title", b"\xff\xfe", content_type="text/plain")
        resp = await upload(single_client, "/api/documents/upload", form)
        assert resp.status == 400
        assert (await resp.json()) == {"error": "title must be UTF-8 text"}

    async def test_upload_without_a_file_is_a_400(self, single_client):
        resp = await upload(single_client, "/api/documents/upload", multipart(None, title="t"))
        assert resp.status == 400
        assert (await resp.json()) == {"error": "No file provided"}

    async def test_upload_over_one_mib_is_accepted(self, single_client,
                                                   monkeypatch):
        """aiohttp's 1 MiB default body limit does not apply to uploads."""
        calls = record(monkeypatch, "add_document", {"document_id": 1},
                       awaitable=True)
        resp = await upload(single_client, "/api/documents/upload",
                            multipart(b"a" * (5 * 1024 * 1024)))
        assert resp.status == 200
        assert len(calls[0][1]["content_bytes"]) == 5 * 1024 * 1024

    async def test_upload_over_the_document_limit_is_a_413(
            self, single_client, monkeypatch):
        from consensus.tools_document import constants
        record(monkeypatch, "add_document", {}, awaitable=True)
        # The handler reads the limit at call time; shrink it to keep the
        # test fast (the real one is 50 MB).
        monkeypatch.setattr(constants, "MAX_DOCUMENT_BYTES", 2 * 1024 * 1024)
        resp = await upload(single_client, "/api/documents/upload",
                            multipart(b"a" * (2 * 1024 * 1024 + 1)))
        assert resp.status == 413
        assert (await resp.json()) == {
            "error": "File too large. Maximum is 2 MB."}

    @pytest.mark.parametrize("path", ["/api/documents/upload",
                                      "/api/images/upload"])
    async def test_non_multipart_body_is_a_400(self, single_client, path):
        resp = await single_client.post(
            path, data=b"plain", headers={server.CSRF_HEADER: "1",
                                          "Content-Type": "text/plain"})
        assert resp.status == 400
        assert "multipart/form-data" in (await resp.json())["error"]

    async def test_mime_parameters_are_stripped(self, single_client,
                                                         monkeypatch):
        calls = record(monkeypatch, "add_document", {}, awaitable=True)
        await upload(single_client, "/api/documents/upload", multipart(
            b"x", content_type="text/plain; charset=latin-1"))
        assert calls[0][1]["mime_type"] == "text/plain"

    async def test_upload_app_error_is_a_400(self, single_client, monkeypatch):
        record(monkeypatch, "add_document", {"error": "unsupported"},
               awaitable=True)
        resp = await upload(single_client, "/api/documents/upload", multipart(b"x"))
        assert resp.status == 400
        assert (await resp.json()) == {"error": "unsupported"}

    async def test_add_url_forwards(self, single_client, monkeypatch):
        calls = record(monkeypatch, "add_document_from_url", {"document_id": 2},
                       awaitable=True)
        resp = await single_client.post("/api/documents/add-url", json={
            "url": "  https://example.com/a  ", "discussion_id": "3",
            "title": "A"})
        assert (await resp.json()) == {"document_id": 2}
        assert calls == [((), {"url": "https://example.com/a",
                               "discussion_id": 3, "title": "A"})]

    async def test_add_url_defaults(self, single_client, monkeypatch):
        calls = record(monkeypatch, "add_document_from_url", {}, awaitable=True)
        await single_client.post("/api/documents/add-url",
                                 json={"url": "https://e.com"})
        assert calls[0][1] == {"url": "https://e.com", "discussion_id": 0,
                               "title": ""}

    @pytest.mark.parametrize("body", [{}, {"url": "   "}])
    async def test_add_url_requires_a_url(self, single_client, body):
        resp = await single_client.post("/api/documents/add-url", json=body)
        assert resp.status == 400
        assert (await resp.json()) == {"error": "url is required"}

    @pytest.mark.parametrize("path", ["/api/documents/add-url",
                                      "/api/images/add-url"])
    async def test_add_url_non_string_title_is_a_400(self, single_client,
                                                     monkeypatch, path):
        calls = record(monkeypatch, "add_document_from_url", {},
                       awaitable=True)
        record(monkeypatch, "add_image_from_url", {}, awaitable=True)
        resp = await single_client.post(path, json={
            "url": "https://example.com/a", "title": {}})
        assert resp.status == 400
        assert "must be a string" in (await resp.json())["error"]
        assert calls == []

    @pytest.mark.parametrize("path", ["/api/documents/add-url",
                                      "/api/images/add-url"])
    # json.dumps writes Infinity; int() of it is an OverflowError (#138).
    @pytest.mark.parametrize("discussion_id", ["x", None, float("inf")])
    async def test_add_url_bad_discussion_id_is_a_400(
            self, single_client, monkeypatch, path, discussion_id):
        calls = record(monkeypatch, "add_document_from_url", {},
                       awaitable=True)
        record(monkeypatch, "add_image_from_url", {}, awaitable=True)
        resp = await single_client.post(path, json={
            "url": "https://example.com/a", "discussion_id": discussion_id})
        assert resp.status == 400
        assert (await resp.json()) == {
            "error": "discussion_id must be an integer"}
        assert calls == []

    @pytest.mark.parametrize("path", ["/api/documents/add-url",
                                      "/api/images/add-url"])
    async def test_add_url_body_over_one_mib_is_a_413(self, single_client,
                                                      path):
        """Not "Invalid JSON body": the real cause reaches the client."""
        resp = await single_client.post(path, json={
            "url": "https://example.com/a", "title": "a" * (1024 * 1024)})
        assert resp.status == 413

    async def test_add_url_bad_json_is_a_400(self, single_client):
        resp = await single_client.post(
            "/api/documents/add-url", data=b"{",
            headers={"Content-Type": "application/json"})
        assert resp.status == 400

    async def test_add_url_app_error_is_a_400(self, single_client,
                                              monkeypatch):
        record(monkeypatch, "add_document_from_url", {"error": "blocked"},
               awaitable=True)
        resp = await single_client.post("/api/documents/add-url",
                                        json={"url": "http://10.0.0.1"})
        assert resp.status == 400

    async def test_list(self, single_client, monkeypatch):
        calls = record(monkeypatch, "get_discussion_documents", [{"id": 1}])
        resp = await single_client.get("/api/documents/7")
        assert (await resp.json()) == {"documents": [{"id": 1}]}
        assert calls == [((7,), {})]

    async def test_remove(self, single_client, monkeypatch):
        calls = record(monkeypatch, "remove_document", {"ok": True})
        resp = await single_client.delete("/api/documents/5/7")
        assert (await resp.json()) == {"ok": True}
        assert calls == [((5, 7), {})]

    async def test_delete(self, single_client, monkeypatch):
        calls = record(monkeypatch, "delete_document", {"ok": True})
        resp = await single_client.delete("/api/documents/5")
        assert (await resp.json()) == {"ok": True}
        assert calls == [((5,), {})]


class TestImages:
    async def test_upload_then_serve_round_trip(self, single_client):
        resp = await upload(single_client, "/api/images/upload", multipart(
            PNG_BYTES, filename="dot.png", content_type="image/png"))
        assert resp.status == 200, await resp.text()
        image_id = (await resp.json())["image_id"]

        served = await single_client.get(f"/api/images/file/{image_id}")
        assert served.status == 200
        assert served.content_type == "image/png"
        assert await served.read()
        assert served.headers["Content-Security-Policy"] == \
            server.IMAGE_SERVE_CSP
        assert served.headers["Cache-Control"] == "private, max-age=3600"
        assert served.headers["Content-Disposition"].startswith("inline;")

    async def test_served_svg_cannot_run_script(self, single_client,
                                                monkeypatch):
        """#99: an SVG is served sandboxed with no script source."""
        record(monkeypatch, "get_image_data",
               (b"<svg><script>alert(1)</script></svg>", "image/svg+xml",
                "x.svg"))
        served = await single_client.get("/api/images/file/1")
        csp = served.headers["Content-Security-Policy"]
        assert "default-src 'none'" in csp
        assert "sandbox" in csp
        assert "script-src" not in csp
        assert served.headers["X-Content-Type-Options"] == "nosniff"

    async def test_upload_forwards_the_parts(self, single_client, monkeypatch):
        calls = record(monkeypatch, "add_image", {"image_id": 1},
                       awaitable=True)
        await upload(single_client, "/api/images/upload", multipart(
            b"img", filename="a.jpg", content_type="image/jpeg",
            discussion_id="4", title="T"))
        assert calls == [((), {"filename": "a.jpg", "content_bytes": b"img",
                               "mime_type": "image/jpeg", "discussion_id": 4,
                               "title": "T"})]

    async def test_upload_without_a_file_is_a_400(self, single_client):
        resp = await upload(single_client, "/api/images/upload", multipart(None, discussion_id="1"))
        assert resp.status == 400

    async def test_upload_too_large_is_a_413(self, single_client, monkeypatch):
        from consensus.tools_image.constants import MAX_IMAGE_BYTES
        calls = record(monkeypatch, "add_image", {}, awaitable=True)
        resp = await upload(single_client, "/api/images/upload",
                            multipart(b"\0" * (MAX_IMAGE_BYTES + 1)))
        assert resp.status == 413
        assert (await resp.json()) == {
            "error": "File too large. Maximum is 20 MB."}
        assert calls == []

    async def test_upload_over_one_mib_is_accepted(self, single_client,
                                                   monkeypatch):
        """aiohttp's 1 MiB default body limit does not apply to uploads."""
        calls = record(monkeypatch, "add_image", {"image_id": 1},
                       awaitable=True)
        resp = await upload(single_client, "/api/images/upload",
                            multipart(b"\0" * (3 * 1024 * 1024)))
        assert resp.status == 200
        assert len(calls[0][1]["content_bytes"]) == 3 * 1024 * 1024

    async def test_upload_app_error_is_a_400(self, single_client, monkeypatch):
        record(monkeypatch, "add_image", {"error": "not an image"},
               awaitable=True)
        resp = await upload(single_client, "/api/images/upload", multipart(b"x"))
        assert resp.status == 400

    async def test_add_url_forwards(self, single_client, monkeypatch):
        calls = record(monkeypatch, "add_image_from_url", {"image_id": 3},
                       awaitable=True)
        resp = await single_client.post("/api/images/add-url", json={
            "url": " https://e.com/a.png ", "discussion_id": 2})
        assert (await resp.json()) == {"image_id": 3}
        assert calls == [((), {"url": "https://e.com/a.png",
                               "discussion_id": 2, "title": ""})]

    @pytest.mark.parametrize("body", [{}, {"url": ""}])
    async def test_add_url_requires_a_url(self, single_client, body):
        resp = await single_client.post("/api/images/add-url", json=body)
        assert resp.status == 400

    async def test_add_url_bad_json_is_a_400(self, single_client):
        resp = await single_client.post(
            "/api/images/add-url", data=b"{",
            headers={"Content-Type": "application/json"})
        assert resp.status == 400

    async def test_add_url_app_error_is_a_400(self, single_client,
                                              monkeypatch):
        record(monkeypatch, "add_image_from_url", {"error": "x"},
               awaitable=True)
        resp = await single_client.post("/api/images/add-url",
                                        json={"url": "https://e.com"})
        assert resp.status == 400

    async def test_list(self, single_client, monkeypatch):
        calls = record(monkeypatch, "get_discussion_images", [{"id": 1}])
        resp = await single_client.get("/api/images/9")
        assert (await resp.json()) == {"images": [{"id": 1}]}
        assert calls == [((9,), {})]

    async def test_serve_missing_is_a_404(self, single_client):
        resp = await single_client.get("/api/images/file/999")
        assert resp.status == 404

    async def test_remove(self, single_client, monkeypatch):
        calls = record(monkeypatch, "remove_discussion_image", {"ok": True})
        resp = await single_client.delete("/api/images/5/7")
        assert (await resp.json()) == {"ok": True}
        assert calls == [((5, 7), {})]

    async def test_delete(self, single_client, monkeypatch):
        calls = record(monkeypatch, "delete_image", {"ok": True})
        resp = await single_client.delete("/api/images/5")
        assert (await resp.json()) == {"ok": True}
        assert calls == [((5,), {})]

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/images/abc"), ("get", "/api/images/file/abc"),
        ("delete", "/api/images/abc"), ("delete", "/api/images/abc/1"),
        ("delete", "/api/images/1/abc"),
    ])
    async def test_non_numeric_id_is_a_400(self, single_client, method, path):
        resp = await getattr(single_client, method)(path)
        assert resp.status == 400


class TestMalformedInput:
    """Bad ids and bodies are a 400 with a message, never a bare 500."""

    @pytest.mark.parametrize("method,path", [
        ("get", "/api/documents/abc"), ("delete", "/api/documents/abc"),
        ("delete", "/api/documents/abc/1"), ("delete", "/api/documents/1/abc"),
    ])
    async def test_non_numeric_document_id(self, single_client, method, path):
        resp = await getattr(single_client, method)(path)
        assert resp.status == 400
        assert "error" in await resp.json()

    @pytest.mark.parametrize("path", ["/api/documents/add-url",
                                      "/api/images/add-url"])
    @pytest.mark.parametrize("body", [["a"], "x", {"url": 5},
                                      {"url": "https://e.com",
                                       "discussion_id": "abc"}])
    async def test_add_url_bad_body(self, single_client, path, body):
        resp = await single_client.post(path, json=body)
        assert resp.status == 400
        assert "error" in await resp.json()

    @pytest.mark.parametrize("body", [["a"], "x"])
    async def test_memory_config_put_non_object(self, single_client, body):
        resp = await single_client.put("/api/memory/config", json=body)
        assert resp.status == 400
