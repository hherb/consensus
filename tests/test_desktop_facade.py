"""Contract tests for the ``consensus.desktop`` package facade.

Splitting the former single module into a package (issue #61) moved the
bridge and launcher behind re-exports in ``__init__.py``. The consumers
(``consensus/__main__.py`` and the frozen app's
``packaging/macos/launch_app.py``) import lazily or run only in a GUI, so a
dropped re-export would surface only when a user starts the desktop app.
"""

import ast
from pathlib import Path

import pytest

from consensus import desktop

EXPECTED_PUBLIC_API = {
    "ASYNC_BRIDGE_TIMEOUT", "DesktopBridge", "WINDOW_HEIGHT",
    "WINDOW_MIN_HEIGHT", "WINDOW_MIN_WIDTH", "WINDOW_TITLE", "WINDOW_WIDTH",
    "launch_desktop",
}

SUBMODULES = ["bridge", "catalog", "constants", "discussion", "files",
              "launcher"]

PACKAGE_ROOT = Path(desktop.__file__).parent
CONSENSUS_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = CONSENSUS_ROOT.parent


def _imported_names(path: Path, relative_to: Path) -> list[str]:
    """Names imported from ``consensus.desktop`` by the module at *path*."""
    depth = len(path.relative_to(relative_to).parts) - 1
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        absolute = node.level == 0 and node.module == "consensus.desktop"
        relative = (node.level == depth + 1 and node.module == "desktop")
        if absolute or relative:
            names += [alias.name for alias in node.names]
    return names


class TestPublicApi:
    def test_all_matches_the_pinned_api(self):
        assert set(desktop.__all__) == EXPECTED_PUBLIC_API

    def test_every_exported_name_is_the_one_its_submodule_defines(self):
        """A crossed re-export also resolves; pin by identity."""
        from consensus.desktop import bridge, constants, launcher

        expected = {name: getattr(constants, name)
                    for name in EXPECTED_PUBLIC_API
                    if name not in ("DesktopBridge", "launch_desktop")}
        expected["DesktopBridge"] = bridge.DesktopBridge
        expected["launch_desktop"] = launcher.launch_desktop
        for name, defined in expected.items():
            assert getattr(desktop, name) is defined, name

    def test_every_consumer_imports_an_exported_name(self):
        consumers = {}
        for path in [*CONSENSUS_ROOT.rglob("*.py"),
                     *(REPO_ROOT / "packaging").rglob("*.py")]:
            if PACKAGE_ROOT in path.parents:
                continue
            names = _imported_names(path, CONSENSUS_ROOT
                                    if CONSENSUS_ROOT in path.parents
                                    else REPO_ROOT)
            if names:
                consumers[str(path.relative_to(REPO_ROOT))] = names
        assert set(consumers) == {"consensus/__main__.py",
                                  "packaging/macos/launch_app.py"}
        for where, names in consumers.items():
            assert set(names) <= EXPECTED_PUBLIC_API, where


class TestSubmodules:
    @pytest.mark.parametrize("name", SUBMODULES)
    def test_submodule_imports_standalone(self, name):
        __import__(f"consensus.desktop.{name}")

    def test_every_submodule_is_listed(self):
        on_disk = {p.stem for p in PACKAGE_ROOT.glob("*.py")
                   if p.stem != "__init__"}
        assert on_disk == set(SUBMODULES)

    def test_no_submodule_imports_the_package_facade(self):
        """A submodule importing ``__init__`` would close an import cycle."""
        for name in SUBMODULES:
            tree = ast.parse((PACKAGE_ROOT / f"{name}.py").read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.level == 1 and not node.module), name

    def test_bridge_composes_every_mixin(self):
        from consensus.desktop import catalog, discussion, files
        mro = desktop.DesktopBridge.__mro__
        for mixin in (catalog.CatalogBridgeMixin,
                      discussion.DiscussionBridgeMixin,
                      files.FilesBridgeMixin):
            assert mixin in mro
