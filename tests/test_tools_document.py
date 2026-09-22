"""Characterization tests for the document RAG pure functions.

Pins the current behaviour of parsing, URL fetching, section extraction,
chunking and the embedding maths in ``consensus.tools_document`` so that the
module can be split into a package (issue #61) with a real safety net. The
stateful pipeline lives in ``test_tools_document_pipeline``, the tool
handlers in ``test_tools_document_handlers``.
"""

import struct
import sys

import pytest

from consensus.tools_document import chunking, constants, embedding, parsing
from consensus.tools_document.errors import DocumentParseError

from .document_helpers import (
    FakeHttpClient, FakeHttpResponse, FakePdf, FakePdfPage, patch_where_defined,
)


# ---------------------------------------------------------------------------
# Document parsing
# ---------------------------------------------------------------------------

class TestParseDocument:
    def test_plain_text_passthrough(self):
        out = parsing.parse_document(b"hello world", "notes.txt", "text/plain")
        assert out.markdown == "hello world"

    def test_markdown_passthrough(self):
        md = b"# Title\n\nbody"
        out = parsing.parse_document(md, "notes.md", "text/markdown")
        assert out.markdown == "# Title\n\nbody"

    def test_undecodable_bytes_are_replaced_not_raised(self):
        # A couple of stray bad bytes inside an otherwise large, readable
        # document stay under MAX_REPLACEMENT_CHAR_RATIO and must not be
        # treated as binary (issue #78 defect 7 is about content that is
        # *mostly* replacement characters, not a few encoding artifacts).
        content = b"ok " + b"good text " * 20 + b"\xff\xfe"
        out = parsing.parse_document(content, "notes.txt", "text/plain")
        assert out.markdown.startswith("ok ")

    def test_html_by_mime_type(self):
        html = b"<html><body><p>Readable paragraph with enough text.</p></body></html>"
        out = parsing.parse_document(html, "page", "text/html")
        assert "<p>" not in out.markdown

    def test_html_by_extension(self):
        html = b"<html><body><h1>Heading</h1><p>Body text here.</p></body></html>"
        out = parsing.parse_document(html, "PAGE.HTM", "application/octet-stream")
        assert "<h1>" not in out.markdown

    def test_pdf_by_extension_routes_to_pdf_parser(self, monkeypatch):
        called = {}

        def fake_pdf(content):
            called["content"] = content
            return "pdf text"

        patch_where_defined(monkeypatch, parsing.parse_document, "_parse_pdf", fake_pdf)
        assert parsing.parse_document(b"%PDF-1.4", "report.PDF", "application/octet-stream") == "pdf text"
        assert called["content"] == b"%PDF-1.4"

    def test_pdf_by_mime_type_routes_to_pdf_parser(self, monkeypatch):
        patch_where_defined(
            monkeypatch, parsing.parse_document, "_parse_pdf", lambda c: "pdf text",
        )
        assert parsing.parse_document(b"x", "no-extension", "application/pdf") == "pdf text"


class TestParsePdf:
    def _install_pdfplumber(self, monkeypatch, pages):
        import types
        module = types.ModuleType("pdfplumber")
        module.open = lambda _stream: FakePdf(pages)
        monkeypatch.setitem(sys.modules, "pdfplumber", module)

    def test_pages_are_numbered_from_one(self, monkeypatch):
        self._install_pdfplumber(monkeypatch, [FakePdfPage("first"), FakePdfPage("second")])
        out = parsing._parse_pdf(b"x")
        assert out.markdown == "## Page 1\n\nfirst\n\n## Page 2\n\nsecond"

    def test_blank_pages_are_skipped_but_do_not_shift_numbering(self, monkeypatch):
        self._install_pdfplumber(
            monkeypatch, [FakePdfPage("   "), FakePdfPage(None), FakePdfPage("third")],
        )
        assert parsing._parse_pdf(b"x").markdown == "## Page 3\n\nthird"

    def test_falls_back_to_pypdf2_when_pdfplumber_missing(self, monkeypatch):
        import types
        monkeypatch.setitem(sys.modules, "pdfplumber", None)
        pypdf2 = types.ModuleType("PyPDF2")
        pypdf2.PdfReader = lambda _stream: FakePdf([FakePdfPage("fallback text")])
        monkeypatch.setitem(sys.modules, "PyPDF2", pypdf2)
        assert parsing._parse_pdf(b"x").markdown == "## Page 1\n\nfallback text"

    def test_falls_back_to_pypdf2_when_pdfplumber_raises(self, monkeypatch):
        import types
        broken = types.ModuleType("pdfplumber")

        def _boom(_stream):
            raise ValueError("corrupt")

        broken.open = _boom
        monkeypatch.setitem(sys.modules, "pdfplumber", broken)
        pypdf2 = types.ModuleType("PyPDF2")
        pypdf2.PdfReader = lambda _stream: FakePdf([FakePdfPage("fallback text")])
        monkeypatch.setitem(sys.modules, "PyPDF2", pypdf2)
        assert "fallback text" in parsing._parse_pdf(b"x").markdown

    def test_empty_pypdf2_result_raises_instead_of_empty_pdf_placeholder(
        self, monkeypatch,
    ):
        """A scanned PDF raises rather than ingesting as "(Empty PDF)".

        That string was 11 non-blank characters, so the empty-after-parsing
        guard in ``ingestion.py`` never caught it (issue #78 defect 7).
        """
        import types
        monkeypatch.setitem(sys.modules, "pdfplumber", None)
        pypdf2 = types.ModuleType("PyPDF2")
        pypdf2.PdfReader = lambda _stream: FakePdf([FakePdfPage("")])
        monkeypatch.setitem(sys.modules, "PyPDF2", pypdf2)
        with pytest.raises(DocumentParseError, match="scanned"):
            parsing._parse_pdf(b"x")

    def test_no_pdf_library_raises_with_install_hint(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "pdfplumber", None)
        monkeypatch.setitem(sys.modules, "PyPDF2", None)
        with pytest.raises(DocumentParseError, match="pdfplumber"):
            parsing._parse_pdf(b"x")


class TestParseHtml:
    def test_extracts_readable_text(self):
        html = (
            b"<html><body><article><p>"
            b"A reasonably long paragraph of readable article text goes here."
            b"</p></article></body></html>"
        )
        out = parsing._parse_html(html)
        assert "readable article text" in out.markdown
        assert "<p>" not in out.markdown

    def test_falls_back_to_tag_stripping_when_trafilatura_fails(self, monkeypatch):
        import types
        broken = types.ModuleType("trafilatura")

        def _boom(*_args, **_kwargs):
            raise RuntimeError("trafilatura exploded")

        broken.extract = _boom
        monkeypatch.setitem(sys.modules, "trafilatura", broken)
        out = parsing._parse_html(b"<div><span>bare text</span></div>")
        assert out.markdown == "bare text"
        assert out.fidelity == constants.FIDELITY_DEGRADED

    def test_falls_back_when_trafilatura_returns_nothing(self, monkeypatch):
        import types
        empty = types.ModuleType("trafilatura")
        empty.extract = lambda *_a, **_k: None
        monkeypatch.setitem(sys.modules, "trafilatura", empty)
        out = parsing._parse_html(b"<p>only text</p>")
        assert out.markdown == "only text"
        assert out.fidelity == constants.FIDELITY_DEGRADED


# ---------------------------------------------------------------------------
# URL fetching
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_httpx(monkeypatch):
    """Install a fake ``httpx.AsyncClient`` and return a configurator."""
    state = {"urls": [], "kwargs": None}

    def configure(content: bytes, content_type: str):
        response = FakeHttpResponse(content, content_type)

        def factory(**kwargs):
            state["kwargs"] = kwargs
            return FakeHttpClient(response, state["urls"])

        import types
        module = types.SimpleNamespace(AsyncClient=factory)
        patch_where_defined(monkeypatch, parsing.fetch_url_content, "httpx", module)
        return state

    return configure


class TestFetchUrlContent:
    @pytest.mark.asyncio
    async def test_returns_content_filename_and_mime(self, fake_httpx):
        state = fake_httpx(b"<html>hi</html>", "text/html; charset=utf-8")
        content, filename, mime = await parsing.fetch_url_content("http://x.test/page.html")
        assert content == b"<html>hi</html>"
        assert filename == "page.html"
        assert mime == "text/html"
        assert state["urls"] == ["http://x.test/page.html"]

    @pytest.mark.asyncio
    async def test_follows_redirects_with_configured_timeout(self, fake_httpx):
        state = fake_httpx(b"x", "text/plain")
        await parsing.fetch_url_content("http://x.test/a.txt")
        assert state["kwargs"]["follow_redirects"] is True
        assert state["kwargs"]["timeout"] == constants.URL_FETCH_TIMEOUT

    @pytest.mark.asyncio
    async def test_extensionless_pdf_gets_pdf_suffix(self, fake_httpx):
        fake_httpx(b"%PDF", "application/pdf")
        _, filename, _ = await parsing.fetch_url_content("http://x.test/paper")
        assert filename == "paper.pdf"

    @pytest.mark.asyncio
    async def test_extensionless_html_gets_html_suffix(self, fake_httpx):
        fake_httpx(b"<html>", "text/html")
        _, filename, _ = await parsing.fetch_url_content("http://x.test/article")
        assert filename == "article.html"

    @pytest.mark.asyncio
    async def test_empty_path_falls_back_to_document(self, fake_httpx):
        fake_httpx(b"x", "application/octet-stream")
        _, filename, _ = await parsing.fetch_url_content("http://x.test/")
        assert filename == "document"


# ---------------------------------------------------------------------------
# Section extraction
# ---------------------------------------------------------------------------

class TestExtractSections:
    def test_no_headers_yields_no_sections(self):
        assert parsing.extract_sections("just a paragraph") == []

    def test_header_levels_and_text(self):
        sections = parsing.extract_sections("# One\n\ntext\n\n### Three\n\nmore")
        assert [(s["header"], s["level"]) for s in sections] == [("One", 1), ("Three", 3)]

    def test_each_section_ends_where_the_next_begins(self):
        md = "# One\n\ntext\n\n## Two\n\nmore"
        sections = parsing.extract_sections(md)
        assert sections[0]["from_char"] == 0
        assert sections[0]["to_char"] == sections[1]["from_char"]

    def test_last_section_runs_to_end_of_document(self):
        md = "# One\n\ntext"
        assert parsing.extract_sections(md)[-1]["to_char"] == len(md)

    def test_five_hashes_are_not_a_header(self):
        assert parsing.extract_sections("##### Too deep") == []

    def test_header_requires_whitespace_after_hashes(self):
        assert parsing.extract_sections("#NoSpace") == []


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

class TestChunkDocument:
    def test_blank_document_yields_no_chunks(self):
        assert chunking.chunk_document("   \n\n  ") == []

    def test_short_document_is_a_single_chunk(self):
        chunks = chunking.chunk_document("# Title\n\nA short body.")
        assert len(chunks) == 1
        assert chunks[0]["chunk_index"] == 0
        assert chunks[0]["from_char"] == 0
        assert chunks[0]["to_char"] == len("# Title\n\nA short body.")
        assert chunks[0]["section_header"] == "Title"

    def test_chunk_indices_are_consecutive(self):
        md = "\n\n".join("p" * 200 for _ in range(6))
        chunks = chunking.chunk_document(md, chunk_size=300, overlap=50)
        assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))

    def test_consecutive_chunks_overlap_by_the_requested_amount(self):
        md = "\n\n".join("p" * 200 for _ in range(4))
        chunks = chunking.chunk_document(md, chunk_size=300, overlap=50)
        assert len(chunks) > 1
        for earlier, later in zip(chunks, chunks[1:]):
            assert later["content"].startswith(earlier["content"][-50:])

    def test_zero_overlap_starts_each_chunk_at_a_paragraph(self):
        md = "\n\n".join("p" * 200 for _ in range(4))
        chunks = chunking.chunk_document(md, chunk_size=300, overlap=0)
        # With chunk_size=300 no two 200-char paragraphs fit together, so each
        # chunk must be exactly one paragraph and carry no overlap prefix.
        assert len(chunks) == 4
        for chunk in chunks:
            assert chunk["content"] == "p" * 200

    def test_section_header_is_taken_from_the_chunk_start_offset(self):
        md = "# Alpha\n\n" + "a" * 100 + "\n\n## Beta\n\n" + "b" * 100
        chunks = chunking.chunk_document(md, chunk_size=80, overlap=0)
        assert chunks[0]["section_header"] == "Alpha"
        assert chunks[-1]["section_header"] == "Beta"

    def test_final_chunk_runs_to_end_of_document(self):
        md = "\n\n".join("p" * 200 for _ in range(4))
        chunks = chunking.chunk_document(md, chunk_size=300, overlap=50)
        assert chunks[-1]["to_char"] == len(md)

    def test_single_paragraph_larger_than_chunk_size_is_not_split(self):
        md = "x" * 2000
        chunks = chunking.chunk_document(md, chunk_size=100, overlap=10)
        assert len(chunks) == 1
        assert chunks[0]["content"] == md


class TestSplitParagraphs:
    def test_splits_on_blank_lines_and_strips(self):
        assert chunking._split_paragraphs("one\n\n  two  \n\n\nthree") == [
            (0, "one"), (5, "two"), (15, "three"),
        ]

    def test_offsets_point_into_the_original_text(self):
        text = "alpha\n\nbeta"
        for offset, para in chunking._split_paragraphs(text):
            assert text[offset:offset + len(para)] == para

    def test_blank_paragraphs_are_dropped(self):
        assert chunking._split_paragraphs("\n\n\n") == []

    def test_repeated_paragraph_text_advances_the_cursor(self):
        result = chunking._split_paragraphs("same\n\nsame")
        assert [offset for offset, _ in result] == [0, 6]


class TestFindSectionForOffset:
    def test_returns_none_without_sections(self):
        assert chunking._find_section_for_offset(5, []) is None

    def test_returns_none_before_the_first_section(self):
        sections = [{"header": "A", "from_char": 10, "to_char": 20}]
        assert chunking._find_section_for_offset(4, sections) is None

    def test_returns_the_last_section_starting_at_or_before_the_offset(self):
        sections = [
            {"header": "A", "from_char": 0, "to_char": 10},
            {"header": "B", "from_char": 10, "to_char": 20},
        ]
        assert chunking._find_section_for_offset(10, sections) == "B"
        assert chunking._find_section_for_offset(9, sections) == "A"


# ---------------------------------------------------------------------------
# Embedding maths
# ---------------------------------------------------------------------------

class TestEmbeddingHelpers:
    def test_pack_unpack_roundtrip(self):
        vec = [0.5, -0.25, 1.0]
        assert embedding._unpack_embedding(embedding._pack_embedding(vec)) == pytest.approx(vec)

    def test_pack_produces_four_bytes_per_float(self):
        assert len(embedding._pack_embedding([1.0, 2.0, 3.0])) == 12

    def test_unpack_reads_little_endian_native_floats(self):
        blob = struct.pack("2f", 1.5, 2.5)
        assert embedding._unpack_embedding(blob) == pytest.approx([1.5, 2.5])

    def test_identical_vectors_score_one(self):
        assert embedding._cosine_similarity([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self):
        assert embedding._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_opposite_vectors_score_minus_one(self):
        assert embedding._cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)

    def test_mismatched_dimensions_score_zero_rather_than_truncating(self):
        assert embedding._cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0]) == 0.0

    def test_zero_vector_scores_zero(self):
        assert embedding._cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestRankBySimilarity:
    def _rows(self):
        return [
            {"id": 1, "embedding": embedding._pack_embedding([1.0, 0.0])},
            {"id": 2, "embedding": embedding._pack_embedding([0.0, 1.0])},
            {"id": 3, "embedding": embedding._pack_embedding([0.7071, 0.7071])},
        ]

    def test_orders_by_descending_similarity(self):
        ranked = embedding._rank_by_similarity([1.0, 0.0], self._rows(), limit=3)
        assert [row["id"] for _, row in ranked] == [1, 3, 2]

    def test_limit_truncates_after_sorting(self):
        ranked = embedding._rank_by_similarity([1.0, 0.0], self._rows(), limit=2)
        assert [row["id"] for _, row in ranked] == [1, 3]

    def test_threshold_excludes_low_scoring_rows(self):
        ranked = embedding._rank_by_similarity([1.0, 0.0], self._rows(), limit=3, threshold=0.5)
        assert [row["id"] for _, row in ranked] == [1, 3]

    def test_scores_are_returned_alongside_rows(self):
        ranked = embedding._rank_by_similarity([1.0, 0.0], self._rows(), limit=1)
        score, row = ranked[0]
        assert score == pytest.approx(1.0)
        assert row["id"] == 1

    def test_empty_rows_yield_empty_ranking(self):
        assert embedding._rank_by_similarity([1.0, 0.0], [], limit=5) == []


class TestSplitIntoSubChunks:
    def test_text_within_size_is_returned_whole(self):
        assert embedding._split_into_sub_chunks("abc", size=10, overlap=2) == ["abc"]

    def test_long_text_is_split_into_overlapping_windows(self):
        chunks = embedding._split_into_sub_chunks("x" * 1200, size=500, overlap=100)
        assert [len(c) for c in chunks] == [500, 500, 400]

    def test_windows_advance_by_size_minus_overlap(self):
        text = "".join(chr(ord("a") + i % 26) for i in range(30))
        chunks = embedding._split_into_sub_chunks(text, size=10, overlap=4)
        assert chunks[0] == text[0:10]
        assert chunks[1] == text[6:16]

    def test_reassembly_covers_the_whole_text(self):
        text = "y" * 950
        chunks = embedding._split_into_sub_chunks(text, size=400, overlap=50)
        assert "".join(chunks).count("y") >= len(text)


