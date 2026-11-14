"""Contract tests for the ``consensus.moderator`` package facade.

Splitting the former single module into a package (issue #61) moved
``Moderator`` and its constants behind re-exports in ``__init__.py``. Every
consumer imports ``Moderator`` from the facade, so these tests pin the
public API by identity and check that each consumer imports only exported
names, and that ``Moderator`` still composes every mixin.
"""

import ast
from pathlib import Path

import pytest

from consensus import moderator

EXPECTED_PUBLIC_API = {
    "CONCLUSION_MAX_TOKENS", "CONTEXT_MESSAGE_LIMIT", "MEDIATION_MAX_TOKENS",
    "MODERATOR_TEMPERATURE", "Moderator", "SUMMARY_MAX_TOKENS",
}

SUBMODULES = ["constants", "context", "core", "synthesis", "turn"]

KNOWN_CONSUMERS = {
    "app.py", "app_discussion_state.py", "app_discussion_setup.py",
    "app_discussion_flow/conclusion.py", "app_discussion_flow/turns.py",
}

PACKAGE_ROOT = Path(moderator.__file__).parent
CONSENSUS_ROOT = PACKAGE_ROOT.parent


def _imported_names(path: Path) -> list[str]:
    """Names imported from ``consensus.moderator`` by the module at *path*."""
    depth = len(path.relative_to(CONSENSUS_ROOT).parts) - 1
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        absolute = node.level == 0 and node.module == "consensus.moderator"
        relative = node.level == depth + 1 and node.module == "moderator"
        if absolute or relative:
            names += [alias.name for alias in node.names]
    return names


class TestPublicApi:
    def test_all_matches_the_pinned_api(self):
        assert set(moderator.__all__) == EXPECTED_PUBLIC_API

    def test_every_exported_name_is_the_one_its_submodule_defines(self):
        """A crossed re-export also resolves; pin by identity."""
        from consensus.moderator import constants, core

        expected = {name: getattr(constants, name)
                    for name in EXPECTED_PUBLIC_API if name != "Moderator"}
        expected["Moderator"] = core.Moderator
        for name, defined in expected.items():
            assert getattr(moderator, name) is defined, name

    def test_every_consumer_imports_an_exported_name(self):
        consumers = {}
        for path in CONSENSUS_ROOT.rglob("*.py"):
            if PACKAGE_ROOT in path.parents:
                continue
            names = _imported_names(path)
            if names:
                consumers[path.relative_to(CONSENSUS_ROOT).as_posix()] = names
        assert set(consumers) == KNOWN_CONSUMERS
        for where, names in consumers.items():
            assert set(names) <= EXPECTED_PUBLIC_API, where


class TestSubmodules:
    @pytest.mark.parametrize("name", SUBMODULES)
    def test_submodule_imports_standalone(self, name):
        __import__(f"consensus.moderator.{name}")

    def test_every_submodule_is_listed(self):
        on_disk = {p.stem for p in PACKAGE_ROOT.glob("*.py")
                   if p.stem != "__init__"}
        assert on_disk == set(SUBMODULES)

    def test_no_submodule_imports_the_package_facade(self):
        """A submodule importing ``__init__`` would close an import cycle."""
        for name in SUBMODULES:
            tree = ast.parse(
                (PACKAGE_ROOT / f"{name}.py").read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.level and not node.module), name
                    assert not (node.level == 2
                                and node.module == "moderator"), name
                    assert not (node.level == 0
                                and node.module == "consensus.moderator"), name
                elif isinstance(node, ast.Import):
                    assert "consensus.moderator" not in {
                        alias.name for alias in node.names}, name

    def test_moderator_composes_every_mixin(self):
        from consensus.moderator import context, synthesis, turn
        mro = moderator.Moderator.__mro__
        for mixin in (context.ContextMixin, turn.TurnMixin,
                      synthesis.SynthesisMixin):
            assert mixin in mro
