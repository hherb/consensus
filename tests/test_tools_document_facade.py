"""Contract tests for the ``consensus.tools_document`` package facade.

Splitting the former single module into a package (issue #61) moved every
public name behind re-exports in ``__init__.py``. Those re-exports are load
bearing in a way the rest of the suite cannot see: ``ConsensusApp`` imports
them *lazily, inside function bodies* ::

    from .tools_document import fetch_url_content      # app.py

so a name dropped from ``__init__.py`` raises ``ImportError`` when that
method is first called in production, not at collection time. These tests
pin the re-export list against the call sites that depend on it.
"""

import ast
import re
from pathlib import Path

import pytest

from consensus import tools_document

# The public document API as of the #61 split. Pinned deliberately: a name
# disappearing is the regression these tests exist to catch, and a name
# appearing is a public-API addition that deserves a conscious update here
# rather than silently widening the facade.
EXPECTED_PUBLIC_API = {
    "chunk_document",
    "create_document_provider",
    "extract_sections",
    "fetch_url_content",
    "ingest_document",
    "parse_document",
}

# Every module of the package, leaf-first. Each must stay importable on its
# own: an import cycle would only surface as an ImportError at runtime.
SUBMODULES = [
    "constants", "errors", "parsing", "chunking", "embedding", "schemas",
    "llm", "ingestion", "handlers", "provider",
]

PACKAGE_ROOT = Path(tools_document.__file__).parent


# ``from .tools_document import x`` and ``from consensus.tools_document import
# x`` are the same import spelled two ways; both must be caught.
_PACKAGE_MODULE = re.compile(r"(consensus\.)?tools_document")


def _names_imported_from_package(module_path: Path) -> set[str]:
    """Return every name a module imports from ``consensus.tools_document``.

    Parses the source rather than importing it, so the result covers branches
    the test suite never executes — including the lazy, in-function imports
    ``ConsensusApp`` uses. Both the relative and the absolute spelling of the
    package are recognised; a submodule-qualified import
    (``...tools_document.parsing``) is deliberately *not* counted, since it
    does not go through the facade.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _PACKAGE_MODULE.fullmatch(node.module or ""):
            names.update(alias.name for alias in node.names)
    return names


def _consumer_modules() -> list[Path]:
    """Every module under ``consensus/`` that imports from the facade."""
    return [
        path for path in sorted(PACKAGE_ROOT.parent.rglob("*.py"))
        if PACKAGE_ROOT not in path.parents
        and _names_imported_from_package(path)
    ]


class TestPublicApi:
    def test_all_matches_the_pinned_api(self):
        assert set(tools_document.__all__) == EXPECTED_PUBLIC_API

    def test_every_exported_name_resolves(self):
        for name in tools_document.__all__:
            assert getattr(tools_document, name, None) is not None, name

    def test_every_exported_name_is_the_one_its_submodule_defines(self):
        """Existence is not enough: a crossed re-export also resolves.

        ``from .chunking import chunk_document as parse_document`` passes an
        existence check and breaks only at the lazy call site in production.
        """
        from consensus.tools_document import chunking, ingestion, parsing, provider

        expected = {
            "chunk_document": chunking.chunk_document,
            "create_document_provider": provider.create_document_provider,
            "extract_sections": parsing.extract_sections,
            "fetch_url_content": parsing.fetch_url_content,
            "ingest_document": ingestion.ingest_document,
            "parse_document": parsing.parse_document,
        }
        assert set(expected) == EXPECTED_PUBLIC_API
        for name, defined in expected.items():
            assert getattr(tools_document, name) is defined, name

    def test_no_duplicate_entries_in_all(self):
        assert len(tools_document.__all__) == len(set(tools_document.__all__))


class TestCallSites:
    """Scans every module under ``consensus/``, not just ``app.py``.

    A lazy ``from .tools_document import ...`` added to ``server.py``,
    ``moderator.py`` or anywhere else would otherwise pass CI green and raise
    ``ImportError`` on first call in production — the exact failure class
    these tests exist to prevent.
    """

    def test_app_is_still_a_consumer(self):
        consumers = {path.name for path in _consumer_modules()}
        assert "app.py" in consumers, consumers

    def test_every_consumer_imports_only_names_the_facade_exports(self):
        for path in _consumer_modules():
            used = _names_imported_from_package(path)
            assert used <= EXPECTED_PUBLIC_API, (path.name, sorted(used - EXPECTED_PUBLIC_API))

    def test_names_used_by_consumers_are_importable(self):
        for path in _consumer_modules():
            for name in _names_imported_from_package(path):
                assert getattr(tools_document, name, None) is not None, (path.name, name)


class TestSubmodules:
    @pytest.mark.parametrize("name", SUBMODULES)
    def test_submodule_imports_standalone(self, name):
        __import__(f"consensus.tools_document.{name}")

    def test_no_submodule_imports_the_package_facade(self):
        """A submodule importing ``__init__`` would close an import cycle.

        Catches all three spellings: ``from . import x``,
        ``from consensus.tools_document import x``, and
        ``import consensus.tools_document``.
        """
        for name in SUBMODULES:
            source = (PACKAGE_ROOT / f"{name}.py").read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    # `from . import x` / `from .. import tools_document`
                    assert not (node.level and not node.module), (
                        f"{name}.py imports from the package facade"
                    )
                    # `from consensus.tools_document import x`
                    assert not (
                        not node.level
                        and _PACKAGE_MODULE.fullmatch(node.module or "")
                    ), f"{name}.py imports from the package facade"
                elif isinstance(node, ast.Import):
                    # `import consensus.tools_document`
                    for alias in node.names:
                        assert not _PACKAGE_MODULE.fullmatch(alias.name), (
                            f"{name}.py imports from the package facade"
                        )

    def test_every_submodule_is_listed(self):
        on_disk = {
            path.stem for path in PACKAGE_ROOT.glob("*.py")
            if path.stem != "__init__"
        }
        assert on_disk == set(SUBMODULES)
