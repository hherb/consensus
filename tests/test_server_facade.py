"""Contract tests for the ``consensus.server`` package facade and route table.

Splitting the former single ``server.py`` (issue #61) turned ~35 closures
inside ``launch_web`` into module-level handlers wired up by
``create_web_app``. Its consumer (``consensus/__main__.py``) imports lazily,
so a dropped re-export would surface only when a user starts the web server;
and a route wired to the wrong handler still resolves. Both are pinned here.
"""

import ast
from pathlib import Path

import pytest

from consensus import server
from tests.server_helpers import server_home  # noqa: F401  (fixture)

EXPECTED_PUBLIC_API = {
    "AUTH_COOKIE", "AUTH_HEADER", "CSRF_HEADER", "CTX_KEY", "DEFAULT_HOST",
    "DEFAULT_PORT", "IMAGE_SERVE_CSP", "SESSION_COOKIE", "ServerContext",
    "create_web_app", "launch_web",
}

SUBMODULES = ["app_factory", "constants", "context", "middleware",
              "request_io", "routes_api", "routes_auth", "routes_documents",
              "routes_images", "routes_misc"]

PACKAGE_ROOT = Path(server.__file__).parent
CONSENSUS_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = CONSENSUS_ROOT.parent


def _imported_names(path: Path) -> list[str]:
    """Names imported from ``consensus.server`` by the module at *path*."""
    depth = len(path.relative_to(CONSENSUS_ROOT).parts) - 1
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom):
            continue
        absolute = node.level == 0 and node.module == "consensus.server"
        relative = node.level == depth + 1 and node.module == "server"
        if absolute or relative:
            names += [alias.name for alias in node.names]
    return names


class TestPublicApi:
    def test_all_matches_the_pinned_api(self):
        assert set(server.__all__) == EXPECTED_PUBLIC_API

    def test_every_exported_name_is_the_one_its_submodule_defines(self):
        """A crossed re-export also resolves; pin by identity."""
        from consensus.server import app_factory, constants, context

        expected = {name: getattr(constants, name)
                    for name in EXPECTED_PUBLIC_API
                    if hasattr(constants, name)}
        expected.update(CTX_KEY=context.CTX_KEY,
                        ServerContext=context.ServerContext,
                        create_web_app=app_factory.create_web_app,
                        launch_web=app_factory.launch_web)
        assert set(expected) == EXPECTED_PUBLIC_API
        for name, defined in expected.items():
            assert getattr(server, name) is defined, name

    def test_every_consumer_imports_an_exported_name(self):
        consumers = {}
        for path in CONSENSUS_ROOT.rglob("*.py"):
            if PACKAGE_ROOT in path.parents:
                continue
            names = _imported_names(path)
            if names:
                consumers[str(path.relative_to(REPO_ROOT))] = names
        assert consumers == {"consensus/__main__.py": ["launch_web"]}


class TestSubmodules:
    @pytest.mark.parametrize("name", SUBMODULES)
    def test_submodule_imports_standalone(self, name):
        __import__(f"consensus.server.{name}")

    def test_every_submodule_is_listed(self):
        on_disk = {p.stem for p in PACKAGE_ROOT.glob("*.py")
                   if p.stem != "__init__"}
        assert on_disk == set(SUBMODULES)

    def test_no_submodule_imports_the_package_facade(self):
        """A submodule importing ``__init__`` would close an import cycle."""
        for name in SUBMODULES:
            tree = ast.parse((PACKAGE_ROOT / f"{name}.py").read_text())
            for node in ast.walk(tree):
                if (isinstance(node, ast.ImportFrom) and node.level == 1
                        and not node.module):
                    # ``from . import routes_x`` names submodules: fine.
                    assert {a.name for a in node.names} <= set(SUBMODULES), \
                        name


def _expected_routes() -> dict[tuple[str, str], object]:
    from consensus.server import (
        routes_api as api, routes_auth as auth, routes_documents as docs,
        routes_images as img, routes_misc as misc,
    )
    return {
        ("GET", "/health"): misc.handle_health,
        ("POST", "/auth/register"): auth.handle_auth_register,
        ("POST", "/auth/login"): auth.handle_auth_login,
        ("POST", "/auth/logout"): auth.handle_auth_logout,
        ("GET", "/auth/status"): auth.handle_auth_status,
        ("GET", "/auth/me"): auth.handle_auth_me,
        ("POST", "/auth/me"): auth.handle_auth_update_profile,
        ("GET", "/auth/oauth/providers"): auth.handle_oauth_providers,
        ("GET", "/auth/oauth/authorize/{provider}"):
            auth.handle_oauth_authorize,
        ("GET", "/auth/oauth/callback/{provider}"): auth.handle_oauth_callback,
        ("POST", "/auth/oauth/callback/{provider}"):
            auth.handle_oauth_callback,
        ("GET", "/api/memory/config"): docs.handle_memory_config_get,
        ("PUT", "/api/memory/config"): docs.handle_memory_config_put,
        ("POST", "/api/memory/test"): docs.handle_memory_test,
        ("POST", "/api/documents/upload"): docs.handle_document_upload,
        ("POST", "/api/documents/add-url"): docs.handle_document_add_url,
        ("GET", "/api/documents/{discussion_id}"): docs.handle_document_list,
        ("DELETE", "/api/documents/{document_id}/{discussion_id}"):
            docs.handle_document_remove,
        ("DELETE", "/api/documents/{document_id}"):
            docs.handle_document_delete,
        ("POST", "/api/images/upload"): img.handle_image_upload,
        ("POST", "/api/images/add-url"): img.handle_image_add_url,
        ("GET", "/api/images/file/{image_id}"): img.handle_image_serve,
        ("GET", "/api/images/{discussion_id}"): img.handle_image_list,
        ("DELETE", "/api/images/{image_id}/{discussion_id}"):
            img.handle_image_remove,
        ("DELETE", "/api/images/{image_id}"): img.handle_image_delete,
        ("GET", "/api/events"): misc.handle_events,
        ("POST", "/api/{method}"): api.handle_api,
        ("GET", "/"): misc.serve_index,
        ("GET", "/{path}"): misc.serve_static,
    }


def test_route_table_wires_each_path_to_its_handler(server_home):
    """Every route reaches the handler it is meant to (eval routes aside)."""
    webapp = server.create_web_app(multi_user=False)
    actual = {}
    for route in webapp.router.routes():
        path = route.resource.canonical if route.resource else ""
        if route.method == "HEAD" or path.startswith("/eval"):
            continue
        actual[(route.method, path)] = route.handler
    assert actual == _expected_routes()
