"""Contract tests for the ``consensus.tools_python`` package facade.

Splitting the former single module into a package (issue #61) put the public
name behind a re-export in ``__init__.py``. The production consumer imports it
lazily, inside a function body (``app._init_python_tools``), so a dropped
re-export would raise ``ImportError`` only when the app starts — and that is
caught there and logged as a WARNING ("Python execution tools failed to
load"), leaving the app running without them. These tests pin the
re-export against every call site under ``consensus/``.
"""

import ast
import re
from pathlib import Path

import pytest

from consensus import tools_python

EXPECTED_PUBLIC_API = {"create_python_provider"}

# Leaf-first; each must import on its own (a cycle would fail only at runtime).
SUBMODULES = ["constants", "schemas", "execution", "install", "provider"]

PACKAGE_ROOT = Path(tools_python.__file__).parent

# ``from .tools_python import x`` and ``from consensus.tools_python import x``.
_PACKAGE_MODULE = re.compile(r"(consensus\.)?tools_python")


def _names_imported_from_package(module_path: Path) -> set[str]:
    """Return every name a module imports from the facade (not a submodule)."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    return {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and _PACKAGE_MODULE.fullmatch(node.module or "")
        for alias in node.names
    }


def _consumer_modules() -> list[Path]:
    """Every module under ``consensus/`` outside the package that imports from it."""
    return [
        path for path in sorted(PACKAGE_ROOT.parent.rglob("*.py"))
        if PACKAGE_ROOT not in path.parents and _names_imported_from_package(path)
    ]


def test_all_matches_the_pinned_api():
    assert set(tools_python.__all__) == EXPECTED_PUBLIC_API


def test_export_is_the_one_its_submodule_defines():
    from consensus.tools_python import provider

    assert tools_python.create_python_provider is provider.create_python_provider


def test_known_consumer_is_found():
    assert "app.py" in {path.name for path in _consumer_modules()}


def test_every_consumer_imports_only_exported_names():
    for path in _consumer_modules():
        used = _names_imported_from_package(path)
        assert used <= EXPECTED_PUBLIC_API, (path.name, sorted(used - EXPECTED_PUBLIC_API))


@pytest.mark.parametrize("name", SUBMODULES)
def test_submodule_imports_standalone(name):
    __import__(f"consensus.tools_python.{name}")


def test_every_submodule_is_listed():
    on_disk = {p.stem for p in PACKAGE_ROOT.glob("*.py") if p.stem != "__init__"}
    assert on_disk == set(SUBMODULES)


def test_no_submodule_imports_the_package_facade():
    """A submodule importing ``__init__`` would close an import cycle."""
    for name in SUBMODULES:
        tree = ast.parse((PACKAGE_ROOT / f"{name}.py").read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.level and not node.module), name
                assert not (not node.level and _PACKAGE_MODULE.fullmatch(node.module or "")), name


def test_moved_code_logs_under_the_old_logger_name():
    """Child loggers propagate, so ``caplog`` on ``consensus.tools_python`` still works."""
    for name in ("execution", "install"):
        module = __import__(f"consensus.tools_python.{name}", fromlist=["logger"])
        assert module.logger.name == f"consensus.tools_python.{name}"
