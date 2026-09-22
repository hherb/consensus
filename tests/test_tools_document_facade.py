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
    "constants", "parsing", "chunking", "embedding", "schemas",
    "llm", "ingestion", "handlers", "provider",
]

PACKAGE_ROOT = Path(tools_document.__file__).parent


def _names_imported_from_package(module_path: Path) -> set[str]:
    """Return every name a module imports from ``consensus.tools_document``.

    Parses the source rather than importing it, so the result covers branches
    the test suite never executes — including the lazy, in-function imports
    ``ConsensusApp`` uses.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "tools_document":
            names.update(alias.name for alias in node.names)
    return names


class TestPublicApi:
    def test_all_matches_the_pinned_api(self):
        assert set(tools_document.__all__) == EXPECTED_PUBLIC_API

    def test_every_exported_name_resolves(self):
        for name in tools_document.__all__:
            assert getattr(tools_document, name, None) is not None, name

    def test_no_duplicate_entries_in_all(self):
        assert len(tools_document.__all__) == len(set(tools_document.__all__))


class TestAppCallSites:
    def test_app_imports_only_names_the_facade_exports(self):
        used = _names_imported_from_package(PACKAGE_ROOT.parent / "app.py")
        assert used, "expected app.py to import from the document package"
        assert used <= EXPECTED_PUBLIC_API, sorted(used - EXPECTED_PUBLIC_API)

    def test_names_used_by_app_are_importable(self):
        used = _names_imported_from_package(PACKAGE_ROOT.parent / "app.py")
        for name in used:
            assert getattr(tools_document, name, None) is not None, name


class TestSubmodules:
    @pytest.mark.parametrize("name", SUBMODULES)
    def test_submodule_imports_standalone(self, name):
        __import__(f"consensus.tools_document.{name}")

    def test_no_submodule_imports_the_package_facade(self):
        """A submodule importing ``__init__`` would close an import cycle."""
        for name in SUBMODULES:
            source = (PACKAGE_ROOT / f"{name}.py").read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    # `from . import x` / `from .. import tools_document`
                    assert not (node.level and not node.module), (
                        f"{name}.py imports from the package facade"
                    )

    def test_every_submodule_is_listed(self):
        on_disk = {
            path.stem for path in PACKAGE_ROOT.glob("*.py")
            if path.stem != "__init__"
        }
        assert on_disk == set(SUBMODULES)
