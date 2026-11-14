"""Contract tests for the ``consensus.tools_memory`` package facade.

Splitting the former single module into a package (issue #61) moved every
public name behind re-exports in ``__init__.py``. Every production consumer
imports them *lazily, inside function bodies* ::

    from .tools_memory import EmbeddingClient        # moderator.py, server.py, ...

so a name dropped from ``__init__.py`` raises ``ImportError`` the first time
that code path runs in production, not at collection time. These tests pin
the re-export list against the call sites that depend on it.
"""

import ast
import re
from pathlib import Path

import pytest

from consensus import tools_memory

# The public memory API as of the #61 split. Pinned deliberately: a name
# disappearing is the regression these tests exist to catch, and a name
# appearing is a public-API addition that deserves a conscious update here.
# The two underscored names are imported by mcp_server/ and
# context_strategies.py; they predate the split and kept their spelling.
EXPECTED_PUBLIC_API = {
    "EmbeddingClient",
    "EmbeddingContextLengthError",
    "MemoryUnavailableError",
    "_pack_embedding",
    "_rank_by_similarity",
    "backfill_embeddings",
    "create_memory_provider",
    "ensure_discussion_indexed",
}

# Every module of the package, leaf-first. Each must stay importable on its
# own: an import cycle would only surface as an ImportError at runtime.
SUBMODULES = [
    "constants", "embedding", "schemas", "handlers", "handlers_kg", "provider",
]

PACKAGE_ROOT = Path(tools_memory.__file__).parent

# ``from .tools_memory import x`` and ``from consensus.tools_memory import x``
# are the same import spelled two ways; both must be caught.
_PACKAGE_MODULE = re.compile(r"(consensus\.)?tools_memory")


def _names_imported_from_package(module_path: Path) -> set[str]:
    """Return every name a module imports from ``consensus.tools_memory``.

    Parses the source rather than importing it, so the result covers the
    lazy, in-function imports every consumer uses. A submodule-qualified
    import (``...tools_memory.embedding``) is deliberately *not* counted,
    since it does not go through the facade.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and _PACKAGE_MODULE.fullmatch(node.module or ""):
            names.update(alias.name for alias in node.names)
    return names


def _consumer_modules() -> list[Path]:
    """Every module under ``consensus/`` outside the package that imports from it."""
    return [
        path for path in sorted(PACKAGE_ROOT.parent.rglob("*.py"))
        if PACKAGE_ROOT not in path.parents
        and _names_imported_from_package(path)
    ]


class TestPublicApi:
    def test_all_matches_the_pinned_api(self):
        assert set(tools_memory.__all__) == EXPECTED_PUBLIC_API

    def test_no_duplicate_entries_in_all(self):
        assert len(tools_memory.__all__) == len(set(tools_memory.__all__))

    def test_every_exported_name_is_the_one_its_submodule_defines(self):
        """Existence is not enough: a crossed re-export also resolves.

        ``from .embedding import _pack_embedding as _rank_by_similarity``
        passes an existence check and breaks only at the call site.
        """
        from consensus.tools_memory import embedding, handlers, provider

        expected = {
            "EmbeddingClient": embedding.EmbeddingClient,
            "EmbeddingContextLengthError": embedding.EmbeddingContextLengthError,
            "MemoryUnavailableError": embedding.MemoryUnavailableError,
            "_pack_embedding": embedding._pack_embedding,
            "_rank_by_similarity": embedding._rank_by_similarity,
            "backfill_embeddings": handlers.backfill_embeddings,
            "create_memory_provider": provider.create_memory_provider,
            "ensure_discussion_indexed": handlers.ensure_discussion_indexed,
        }
        assert set(expected) == EXPECTED_PUBLIC_API
        for name, defined in expected.items():
            assert getattr(tools_memory, name) is defined, name


class TestCallSites:
    """Scans every module under ``consensus/``, not just one known consumer."""

    def test_known_consumers_are_found(self):
        consumers = {path.relative_to(PACKAGE_ROOT.parent).as_posix()
                     for path in _consumer_modules()}
        assert {"app.py", "context_strategies.py",
                "mcp_server/handlers_read.py"} <= consumers, consumers

    def test_every_consumer_imports_only_names_the_facade_exports(self):
        for path in _consumer_modules():
            used = _names_imported_from_package(path)
            assert used <= EXPECTED_PUBLIC_API, (path.name, sorted(used - EXPECTED_PUBLIC_API))


class TestSubmodules:
    @pytest.mark.parametrize("name", SUBMODULES)
    def test_submodule_imports_standalone(self, name):
        __import__(f"consensus.tools_memory.{name}")

    def test_no_submodule_imports_the_package_facade(self):
        """A submodule importing ``__init__`` would close an import cycle."""
        for name in SUBMODULES:
            tree = ast.parse((PACKAGE_ROOT / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.level and not node.module), name
                    assert not (
                        not node.level and _PACKAGE_MODULE.fullmatch(node.module or "")
                    ), name
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not _PACKAGE_MODULE.fullmatch(alias.name), name

    def test_every_submodule_is_listed(self):
        on_disk = {p.stem for p in PACKAGE_ROOT.glob("*.py") if p.stem != "__init__"}
        assert on_disk == set(SUBMODULES)

    def test_moved_code_logs_under_the_old_logger_name(self):
        """``caplog`` on ``consensus.tools_memory`` must still see submodule logs.

        Child loggers propagate to their dotted parent, so this holds exactly
        while each logger is named after its submodule.
        """
        for name in ("embedding", "handlers", "handlers_kg"):
            module = __import__(f"consensus.tools_memory.{name}", fromlist=["logger"])
            assert module.logger.name == f"consensus.tools_memory.{name}"
