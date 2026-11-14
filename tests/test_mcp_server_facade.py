"""Contract tests for the ``consensus.mcp_server`` package facade.

Splitting the former single module into a package (issue #61) moved the
server behind re-exports in ``__init__.py``. Its consumers are the
``consensus-mcp`` console script (``pyproject.toml``) and
``python -m consensus.mcp_server`` (``__main__.py``) — neither is exercised by
importing the class, so a broken entry point would only surface for a user.
"""

import ast
import tomllib
from pathlib import Path

import pytest

from consensus import mcp_server

EXPECTED_PUBLIC_API = {
    "AGENT_ENTITY_NAME", "ConsensusMCPServer", "JSONRPC_VERSION",
    "MCP_PROTOCOL_VERSION", "SERVER_NAME", "SERVER_VERSION", "main",
}

SUBMODULES = ["constants", "tool_defs", "handlers_read", "handlers_write",
              "server"]

PACKAGE_ROOT = Path(mcp_server.__file__).parent
REPO_ROOT = PACKAGE_ROOT.parent.parent


class TestPublicApi:
    def test_all_matches_the_pinned_api(self):
        assert set(mcp_server.__all__) == EXPECTED_PUBLIC_API

    def test_every_exported_name_is_the_one_its_submodule_defines(self):
        """A crossed re-export also resolves; pin by identity."""
        from consensus.mcp_server import constants, server

        expected = {name: getattr(constants, name)
                    for name in EXPECTED_PUBLIC_API
                    if name not in ("ConsensusMCPServer", "main")}
        expected["ConsensusMCPServer"] = server.ConsensusMCPServer
        expected["main"] = server.main
        for name, defined in expected.items():
            assert getattr(mcp_server, name) is defined, name


class TestEntryPoints:
    def test_console_script_resolves_to_main(self):
        scripts = tomllib.loads(
            (REPO_ROOT / "pyproject.toml").read_text())["project"]["scripts"]
        module, _, attr = scripts["consensus-mcp"].partition(":")
        assert getattr(__import__(module, fromlist=[attr]), attr) \
            is mcp_server.main

    def test_dunder_main_calls_main(self):
        tree = ast.parse((PACKAGE_ROOT / "__main__.py").read_text())
        calls = [n.func.id for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        assert calls == ["main"]


class TestSubmodules:
    @pytest.mark.parametrize("name", SUBMODULES)
    def test_submodule_imports_standalone(self, name):
        __import__(f"consensus.mcp_server.{name}")

    def test_every_submodule_is_listed(self):
        on_disk = {p.stem for p in PACKAGE_ROOT.glob("*.py")
                   if p.stem not in ("__init__", "__main__")}
        assert on_disk == set(SUBMODULES)

    def test_no_submodule_imports_the_package_facade(self):
        """A submodule importing ``__init__`` would close an import cycle."""
        for name in SUBMODULES:
            tree = ast.parse((PACKAGE_ROOT / f"{name}.py").read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert not (node.level == 1 and not node.module), name

    def test_every_tool_handler_is_defined_by_a_mixin(self):
        """Each registered handler is a bound method the class really has."""
        srv = mcp_server.ConsensusMCPServer.__new__(
            mcp_server.ConsensusMCPServer)
        srv._tools = {}
        srv._register_all_tools()
        assert len(srv._tools) == 13
        for name, tool in srv._tools.items():
            assert tool["handler"].__name__ == f"_handle_{name}", name
