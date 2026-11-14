"""Contract tests for the ``consensus.tools_image`` package facade.

Splitting the former single module into a package (issue #61) put the public
names behind re-exports in ``__init__.py``. Most consumers import them lazily,
inside function bodies (``app._init_image_tools``, the
``app_api.images`` mixin, ``desktop``), so a dropped or crossed re-export
would fail only at runtime. These tests pin each re-export by *identity*
against its defining submodule and against every call site under
``consensus/``.
"""

import ast
import re
from pathlib import Path

import pytest

from consensus import tools_image
from consensus.tools_image import fetch, provider, storage, vision

EXPORTS = {
    "build_image_content_blocks": vision,
    "create_image_provider": provider,
    "delete_image_file": storage,
    "fetch_image_from_url": fetch,
    "image_to_base64_url": storage,
    "ingest_image": storage,
    "is_vision_capable": vision,
    "load_image_file": storage,
    "save_image_file": storage,
}

# Leaf-first; each must import on its own (a cycle would fail only at runtime).
SUBMODULES = ["constants", "schemas", "storage", "fetch", "vision",
              "handlers", "provider"]

PACKAGE_ROOT = Path(tools_image.__file__).parent

# ``from .tools_image import x`` and ``from consensus.tools_image import x``.
_PACKAGE_MODULE = re.compile(r"(consensus\.)?tools_image")


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
    assert set(tools_image.__all__) == set(EXPORTS)


@pytest.mark.parametrize("name", sorted(EXPORTS))
def test_export_is_the_one_its_submodule_defines(name):
    assert getattr(tools_image, name) is getattr(EXPORTS[name], name)


def test_known_consumers_are_found():
    names = {path.relative_to(PACKAGE_ROOT.parent).as_posix()
             for path in _consumer_modules()}
    assert {"app.py", "moderator.py", "desktop/bridge.py",
            "app_api/images.py"} <= names


def test_every_consumer_imports_only_exported_names():
    for path in _consumer_modules():
        used = _names_imported_from_package(path)
        assert used <= set(EXPORTS), (path.name, sorted(used - set(EXPORTS)))


@pytest.mark.parametrize("name", SUBMODULES)
def test_submodule_imports_standalone(name):
    __import__(f"consensus.tools_image.{name}")


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


@pytest.mark.parametrize("name", ["storage", "vision", "handlers"])
def test_moved_code_logs_under_the_old_logger_name(name):
    """Child loggers propagate, so ``caplog`` on ``consensus.tools_image`` still works."""
    module = __import__(f"consensus.tools_image.{name}", fromlist=["logger"])
    assert module.logger.name == f"consensus.tools_image.{name}"
