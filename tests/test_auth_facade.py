"""Contract tests for the ``consensus.auth`` package facade.

Splitting the former single module into a package (issue #61) moved the
API behind re-exports in ``__init__.py``. Its consumer, ``server.py``,
builds auth only in ``--multi-user`` mode, so a dropped re-export would
surface only on a hosted deployment.
"""

import ast
from pathlib import Path

import pytest

from consensus import auth

SUBMODULES = ["constants", "database", "manager", "models", "oauth",
              "passwords"]

CONSTANTS = {
    "AUTH_TOKEN_BYTES", "AUTH_TOKEN_TTL", "MIN_PASSWORD_LENGTH",
    "OAUTH_HTTP_TIMEOUT", "OAUTH_MAX_RETRIES", "OAUTH_PROVIDERS",
    "OAUTH_RETRY_BASE_DELAY", "OAUTH_STATE_TTL", "PASSWORD_HASH_ITERATIONS",
    "PASSWORD_SALT_BYTES",
}

#: Exported name -> the submodule that defines it, constants aside.
DEFINED_IN = {
    "AuthDatabase": "database",
    "AuthManager": "manager",
    "AuthToken": "models",
    "User": "models",
    "build_oauth_authorize_url": "oauth",
    "exchange_oauth_code": "oauth",
    "get_available_oauth_providers": "oauth",
    "hash_password": "passwords",
    "verify_password": "passwords",
}

EXPECTED_PUBLIC_API = CONSTANTS | set(DEFINED_IN)

PACKAGE_ROOT = Path(auth.__file__).parent
CONSENSUS_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = CONSENSUS_ROOT.parent


def _imported_names(path: Path) -> list[str]:
    """Names imported from ``consensus.auth`` by the module at *path*."""
    depth = len(path.relative_to(CONSENSUS_ROOT).parts) - 1
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        absolute = node.level == 0 and node.module == "consensus.auth"
        relative = node.level == depth + 1 and node.module == "auth"
        if absolute or relative:
            names += [alias.name for alias in node.names]
    return names


class TestPublicApi:
    def test_all_matches_the_pinned_api(self):
        assert set(auth.__all__) == EXPECTED_PUBLIC_API

    def test_every_exported_name_is_the_one_its_submodule_defines(self):
        """A crossed re-export also resolves; pin by identity."""
        from consensus.auth import constants

        for name in CONSTANTS:
            assert getattr(auth, name) is getattr(constants, name), name
        for name, where in DEFINED_IN.items():
            module = __import__(f"consensus.auth.{where}", fromlist=[name])
            assert getattr(auth, name) is getattr(module, name), name

    def test_every_consumer_imports_an_exported_name(self):
        consumers = {}
        for path in CONSENSUS_ROOT.rglob("*.py"):
            if PACKAGE_ROOT in path.parents:
                continue
            names = _imported_names(path)
            if names:
                consumers[str(path.relative_to(REPO_ROOT))] = names
        assert set(consumers) == {"consensus/server.py"}
        for where, names in consumers.items():
            assert set(names) <= EXPECTED_PUBLIC_API, where


class TestSubmodules:
    @pytest.mark.parametrize("name", SUBMODULES)
    def test_submodule_imports_standalone(self, name):
        __import__(f"consensus.auth.{name}")

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
