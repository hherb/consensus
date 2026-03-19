"""Tests for consensus.tools_python — sandboxed Python code execution."""

import asyncio
import json
import platform
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from consensus.tools_python import (
    _analyze_ast,
    _build_macos_sandbox_cmd,
    _compute_wall_timeout,
    _install_package_handler,
    create_python_provider,
    execute_python_handler,
    BLOCKED_MODULES,
    MIN_WALL_TIMEOUT_SECONDS,
    PACKAGE_NAME_RE,
    WALL_TIMEOUT_MULTIPLIER,
)
from consensus.sandbox_worker import (
    execute_code, ALLOWED_MODULES,
    MEMORY_FRACTION, CPU_FRACTION,
    MIN_MEMORY_BYTES, MIN_CPU_TIME_SECONDS,
    _compute_memory_limit, _compute_cpu_time_limit, _get_available_memory_bytes,
)
from consensus.tools import ToolContext, ToolDefinition


# --- Helper ---

def run_async(coro):
    """Run an async coroutine synchronously for tests."""
    return asyncio.run(coro)


@pytest.fixture
def tool_context():
    """Minimal ToolContext for handler tests."""
    return ToolContext(
        caller_entity_id=1,
        discussion_id=1,
    )


# --- AST Analysis ---

class TestAnalyzeAST:
    """Tests for the AST pre-analysis security layer."""

    def test_safe_code_passes(self):
        """Simple arithmetic should pass analysis."""
        assert _analyze_ast("x = 1 + 2") == []

    def test_safe_imports_pass(self):
        """Whitelisted module imports should pass."""
        assert _analyze_ast("import math") == []
        assert _analyze_ast("from collections import Counter") == []
        assert _analyze_ast("import json, re, datetime") == []

    def test_blocked_import_os(self):
        """Import of os module should be rejected."""
        violations = _analyze_ast("import os")
        assert len(violations) == 1
        assert "os" in violations[0]

    def test_blocked_import_subprocess(self):
        """Import of subprocess should be rejected."""
        violations = _analyze_ast("import subprocess")
        assert len(violations) == 1
        assert "subprocess" in violations[0]

    def test_blocked_import_socket(self):
        """Network-related imports should be rejected."""
        violations = _analyze_ast("import socket")
        assert len(violations) == 1

    def test_blocked_from_import(self):
        """from X import Y where X is blocked should be rejected."""
        violations = _analyze_ast("from os.path import join")
        assert len(violations) == 1
        assert "os" in violations[0]

    def test_blocked_exec_call(self):
        """Calls to exec() should be rejected."""
        violations = _analyze_ast("exec('print(1)')")
        assert len(violations) == 1
        assert "exec" in violations[0]

    def test_blocked_eval_call(self):
        """Calls to eval() should be rejected."""
        violations = _analyze_ast("eval('1+1')")
        assert len(violations) == 1
        assert "eval" in violations[0]

    def test_blocked_compile_call(self):
        """Calls to compile() should be rejected."""
        violations = _analyze_ast("compile('x=1', '<>', 'exec')")
        assert len(violations) == 1

    def test_blocked_dunder_subclasses(self):
        """Access to __subclasses__ should be rejected."""
        violations = _analyze_ast("int.__subclasses__()")
        assert len(violations) >= 1
        assert any("__subclasses__" in v for v in violations)

    def test_blocked_dunder_globals(self):
        """Access to __globals__ should be rejected."""
        violations = _analyze_ast("func.__globals__")
        assert len(violations) == 1
        assert "__globals__" in violations[0]

    def test_blocked_dunder_builtins(self):
        """Access to __builtins__ should be rejected."""
        violations = _analyze_ast("x.__builtins__")
        assert len(violations) == 1

    def test_syntax_error_reported(self):
        """Syntax errors should be reported as violations."""
        violations = _analyze_ast("def f(")
        assert len(violations) == 1
        assert "Syntax error" in violations[0]

    def test_multiple_violations(self):
        """Code with multiple issues should report all violations."""
        code = "import os\nimport subprocess\nexec('x')"
        violations = _analyze_ast(code)
        assert len(violations) == 3

    def test_blocked_import_shutil(self):
        """Import of shutil should be rejected (filesystem danger)."""
        violations = _analyze_ast("import shutil")
        assert len(violations) == 1

    def test_blocked_import_ctypes(self):
        """Import of ctypes should be rejected (FFI danger)."""
        violations = _analyze_ast("import ctypes")
        assert len(violations) == 1

    def test_blocked_import_http(self):
        """Import of http should be rejected (network danger)."""
        violations = _analyze_ast("from http.client import HTTPConnection")
        assert len(violations) == 1


# --- Sandbox Worker (direct execution) ---

class TestSandboxWorker:
    """Tests for the sandbox_worker.execute_code function."""

    def test_simple_arithmetic(self):
        """Basic arithmetic should work."""
        result = execute_code("x = 2 + 3")
        assert result["error"] is None
        assert result["stdout"] == ""

    def test_last_expression_captured(self):
        """The last expression value should be captured (REPL behavior)."""
        result = execute_code("2 + 3")
        assert result["error"] is None
        assert result["last_expr"] == "5"

    def test_print_captured(self):
        """print() output should be captured in stdout."""
        result = execute_code("print('hello world')")
        assert result["error"] is None
        assert "hello world" in result["stdout"]

    def test_multiline_code(self):
        """Multi-line code should execute correctly."""
        code = """
data = [1, 2, 3, 4, 5]
total = sum(data)
avg = total / len(data)
avg
"""
        result = execute_code(code)
        assert result["error"] is None
        assert result["last_expr"] == "3.0"

    def test_safe_import_math(self):
        """Importing math should work."""
        result = execute_code("import math\nmath.sqrt(144)")
        assert result["error"] is None
        assert result["last_expr"] == "12.0"

    def test_safe_import_json(self):
        """Importing json should work."""
        result = execute_code('import json\njson.dumps({"a": 1})')
        assert result["error"] is None
        assert '"a"' in result["last_expr"]

    def test_safe_import_collections(self):
        """Importing collections should work."""
        result = execute_code(
            "from collections import Counter\nCounter('abracadabra')"
        )
        assert result["error"] is None
        assert "Counter" in result["last_expr"]

    def test_safe_import_datetime(self):
        """Importing datetime should work."""
        result = execute_code("import datetime\ntype(datetime.date.today()).__name__")
        assert result["error"] is None
        assert "date" in result["last_expr"]

    def test_safe_import_re(self):
        """Importing re should work."""
        result = execute_code("import re\nre.findall(r'\\d+', 'abc123def456')")
        assert result["error"] is None
        assert "123" in result["last_expr"]

    def test_safe_import_statistics(self):
        """Importing statistics should work."""
        result = execute_code("import statistics\nstatistics.mean([1,2,3,4,5])")
        assert result["error"] is None
        assert result["last_expr"] == "3"

    def test_blocked_import_os_at_runtime(self):
        """Importing os should fail at runtime (even if AST check is bypassed)."""
        result = execute_code("import os")
        assert result["error"] is not None
        assert "not available" in result["error"] or "ImportError" in result["error"]

    def test_blocked_import_subprocess_at_runtime(self):
        """Importing subprocess should fail at runtime."""
        result = execute_code("import subprocess")
        assert result["error"] is not None

    def test_blocked_import_socket_at_runtime(self):
        """Importing socket should fail at runtime."""
        result = execute_code("import socket")
        assert result["error"] is not None

    def test_runtime_error_captured(self):
        """Runtime errors should be captured, not crash the worker."""
        result = execute_code("1 / 0")
        assert result["error"] is not None
        assert "ZeroDivisionError" in result["error"]

    def test_name_error_captured(self):
        """NameError should be captured."""
        result = execute_code("undefined_variable")
        assert result["error"] is not None
        assert "NameError" in result["error"]

    def test_type_error_captured(self):
        """TypeError should be captured."""
        result = execute_code("'string' + 42")
        assert result["error"] is not None
        assert "TypeError" in result["error"]

    def test_class_definition(self):
        """Class definitions should work."""
        code = """
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y
    def __repr__(self):
        return f'Point({self.x}, {self.y})'

Point(3, 4)
"""
        result = execute_code(code)
        assert result["error"] is None
        assert "Point(3, 4)" in result["last_expr"]

    def test_list_comprehension(self):
        """List comprehensions should work."""
        result = execute_code("[x**2 for x in range(5)]")
        assert result["error"] is None
        assert result["last_expr"] == "[0, 1, 4, 9, 16]"

    def test_lambda_and_map(self):
        """Lambda and map should work."""
        result = execute_code("list(map(lambda x: x*2, [1,2,3]))")
        assert result["error"] is None
        assert result["last_expr"] == "[2, 4, 6]"

    def test_dict_operations(self):
        """Dictionary operations should work."""
        code = """
d = {'a': 1, 'b': 2, 'c': 3}
sorted(d.items(), key=lambda x: x[1], reverse=True)
"""
        result = execute_code(code)
        assert result["error"] is None
        assert "c" in result["last_expr"]

    def test_string_formatting(self):
        """String formatting should work."""
        result = execute_code("f'{3.14159:.2f}'")
        assert result["error"] is None
        assert "3.14" in result["last_expr"]

    def test_no_code(self):
        """Empty code should produce no output."""
        result = execute_code("")
        assert result["error"] is None
        assert result["last_expr"] is None

    def test_only_comments(self):
        """Code with only comments should produce no output."""
        result = execute_code("# this is a comment")
        assert result["error"] is None


# --- Full Handler (subprocess-based) ---

class TestExecutePythonHandler:
    """Tests for the full handler that runs code via subprocess."""

    def test_simple_execution(self, tool_context):
        """Simple code should execute via the subprocess handler."""
        result = run_async(execute_python_handler(
            {"code": "2 + 2"},
            tool_context,
        ))
        assert not result.is_error
        assert "4" in result.content

    def test_ast_rejection(self, tool_context):
        """Code with blocked imports should be rejected before execution."""
        result = run_async(execute_python_handler(
            {"code": "import os; os.listdir('/')"},
            tool_context,
        ))
        assert result.is_error
        assert "security analysis" in result.content

    def test_empty_code(self, tool_context):
        """Empty code should return an error."""
        result = run_async(execute_python_handler(
            {"code": ""},
            tool_context,
        ))
        assert result.is_error

    def test_with_description(self, tool_context):
        """Description should appear in the output."""
        result = run_async(execute_python_handler(
            {"code": "42", "description": "The answer"},
            tool_context,
        ))
        assert not result.is_error
        assert "The answer" in result.content

    def test_print_output(self, tool_context):
        """print() output should be included in results."""
        result = run_async(execute_python_handler(
            {"code": "print('hello from sandbox')"},
            tool_context,
        ))
        assert not result.is_error
        assert "hello from sandbox" in result.content

    def test_runtime_error(self, tool_context):
        """Runtime errors should be reported, not crash."""
        result = run_async(execute_python_handler(
            {"code": "1/0"},
            tool_context,
        ))
        assert result.is_error
        assert "ZeroDivisionError" in result.content

    def test_multiple_blocked_patterns(self, tool_context):
        """Multiple violations should all be reported."""
        result = run_async(execute_python_handler(
            {"code": "import os\nimport subprocess"},
            tool_context,
        ))
        assert result.is_error
        assert "os" in result.content
        assert "subprocess" in result.content


# --- Provider Factory ---

class TestPythonProvider:
    """Tests for the create_python_provider factory."""

    def test_provider_created(self):
        """Factory should return a valid provider."""
        provider = create_python_provider()
        assert provider.name == "python_exec"

    def test_provider_has_execute_tool_without_app(self):
        """Provider without app should have only execute_python tool."""
        provider = create_python_provider()
        tools = run_async(provider.list_tools())
        assert len(tools) == 1
        assert tools[0].name == "execute_python"

    def test_provider_has_install_tool_with_app(self):
        """Provider with app should also have install_python_package tool."""
        from unittest.mock import MagicMock
        mock_app = MagicMock()
        provider = create_python_provider(app=mock_app)
        tools = run_async(provider.list_tools())
        assert len(tools) == 2
        tool_names = {t.name for t in tools}
        assert "execute_python" in tool_names
        assert "install_python_package" in tool_names

    def test_tool_schema_format(self):
        """Tool schema should be valid OpenAI function-calling format."""
        provider = create_python_provider()
        tools = run_async(provider.list_tools())
        tool = tools[0]
        schema = tool.to_openai_schema()
        assert schema["type"] == "function"
        assert "code" in schema["function"]["parameters"]["properties"]
        assert "code" in schema["function"]["parameters"]["required"]


# --- Module Whitelist Consistency ---

class TestModuleWhitelist:
    """Verify that blocked and allowed module lists are consistent."""

    def test_no_overlap(self):
        """Blocked and allowed module lists should not overlap."""
        overlap = BLOCKED_MODULES & ALLOWED_MODULES
        assert overlap == set(), f"Modules in both blocked and allowed: {overlap}"

    def test_dangerous_modules_blocked(self):
        """Critical dangerous modules must be in the blocked list."""
        critical = {"os", "subprocess", "socket", "shutil", "ctypes"}
        assert critical.issubset(BLOCKED_MODULES)

    def test_safe_modules_allowed(self):
        """Core safe modules must be in the allowed list."""
        core_safe = {"math", "json", "re", "datetime", "collections"}
        assert core_safe.issubset(ALLOWED_MODULES)

    def test_scientific_modules_allowed(self):
        """Scientific/ML libraries must be in the allowed list."""
        scientific = {"numpy", "scipy", "pandas", "torch", "hypercomplex", "sympy"}
        assert scientific.issubset(ALLOWED_MODULES)


# --- Dynamic Resource Limits ---

class TestResourceLimits:
    """Tests for dynamic resource limit computation."""

    def test_memory_fraction_in_range(self):
        """Memory fraction must be between 0 and 1."""
        assert 0 < MEMORY_FRACTION <= 1.0

    def test_cpu_fraction_in_range(self):
        """CPU fraction must be between 0 and 1."""
        assert 0 < CPU_FRACTION <= 1.0

    def test_available_memory_positive(self):
        """Available memory detection should return a positive value."""
        mem = _get_available_memory_bytes()
        assert mem > 0

    def test_memory_limit_respects_minimum(self):
        """Computed memory limit should be at least MIN_MEMORY_BYTES."""
        limit = _compute_memory_limit()
        assert limit >= MIN_MEMORY_BYTES

    def test_memory_limit_is_fraction_of_available(self):
        """Memory limit should not exceed available memory."""
        available = _get_available_memory_bytes()
        limit = _compute_memory_limit()
        assert limit <= available

    def test_cpu_time_limit_respects_minimum(self):
        """Computed CPU time limit should be at least MIN_CPU_TIME_SECONDS."""
        limit = _compute_cpu_time_limit()
        assert limit >= MIN_CPU_TIME_SECONDS

    def test_cpu_time_limit_positive(self):
        """CPU time limit should be a positive integer."""
        limit = _compute_cpu_time_limit()
        assert isinstance(limit, int)
        assert limit > 0


# --- Package Name Validation ---

class TestPackageNameValidation:
    """Tests for PyPI package name validation regex."""

    def test_valid_simple_name(self):
        """Simple package names should be valid."""
        assert PACKAGE_NAME_RE.match("numpy")
        assert PACKAGE_NAME_RE.match("torch")
        assert PACKAGE_NAME_RE.match("hypercomplex")

    def test_valid_hyphenated_name(self):
        """Hyphenated names should be valid."""
        assert PACKAGE_NAME_RE.match("scikit-learn")
        assert PACKAGE_NAME_RE.match("my-package")

    def test_valid_dotted_name(self):
        """Dotted names should be valid."""
        assert PACKAGE_NAME_RE.match("zope.interface")

    def test_valid_underscored_name(self):
        """Underscored names should be valid."""
        assert PACKAGE_NAME_RE.match("my_package")

    def test_reject_command_injection(self):
        """Names with shell metacharacters should be rejected."""
        assert not PACKAGE_NAME_RE.match("numpy; rm -rf /")
        assert not PACKAGE_NAME_RE.match("pkg && echo pwned")
        assert not PACKAGE_NAME_RE.match("$(whoami)")
        assert not PACKAGE_NAME_RE.match("pkg`id`")

    def test_reject_empty(self):
        """Empty string should not match."""
        assert not PACKAGE_NAME_RE.match("")

    def test_reject_path_traversal(self):
        """Path-like names should be rejected."""
        assert not PACKAGE_NAME_RE.match("../../../etc/passwd")
        assert not PACKAGE_NAME_RE.match("/usr/bin/python")


# --- io module bypass prevention ---

class TestIoModuleRestriction:
    """Verify that io.open / io.FileIO cannot bypass the sandboxed open()."""

    def test_io_open_blocked(self):
        """io.open() should be restricted to the sandbox directory."""
        result = execute_code("import io\nio.open('/etc/passwd', 'r')")
        assert result["error"] is not None
        assert "denied" in result["error"] or "PermissionError" in result["error"]

    def test_io_stringio_allowed(self):
        """io.StringIO should still work (no filesystem access)."""
        result = execute_code(
            "import io\nbuf = io.StringIO()\nbuf.write('hello')\nbuf.getvalue()"
        )
        assert result["error"] is None
        assert "hello" in result["last_expr"]

    def test_io_bytesio_allowed(self):
        """io.BytesIO should still work (no filesystem access)."""
        result = execute_code(
            "import io\nbuf = io.BytesIO(b'data')\nbuf.getvalue()"
        )
        assert result["error"] is None
        assert "data" in result["last_expr"]


# --- Wall-clock timeout ---

class TestWallTimeout:
    """Tests for dynamic wall-clock timeout computation."""

    def test_wall_timeout_scales_with_cpu(self):
        """Wall timeout should be at least CPU limit * multiplier."""
        timeout = _compute_wall_timeout()
        assert timeout >= MIN_WALL_TIMEOUT_SECONDS

    def test_wall_timeout_never_below_minimum(self):
        """Wall timeout should never drop below the floor."""
        with patch("consensus.sandbox_worker._compute_cpu_time_limit", return_value=1):
            # Even with a tiny CPU limit, wall timeout respects the floor
            timeout = _compute_wall_timeout()
            assert timeout >= MIN_WALL_TIMEOUT_SECONDS

    def test_wall_timeout_proportional(self):
        """Wall timeout should scale proportionally with CPU limit."""
        with patch("consensus.sandbox_worker._compute_cpu_time_limit", return_value=100):
            timeout = _compute_wall_timeout()
            assert timeout >= 100 * WALL_TIMEOUT_MULTIPLIER


class TestTimeoutEnforcement:
    """Test that infinite loops are killed by the wall-clock timeout."""

    def test_infinite_loop_killed(self, tool_context):
        """An infinite loop should be terminated by the timeout."""
        # Use a very short timeout for this test
        with patch("consensus.tools_python._compute_wall_timeout", return_value=3.0):
            result = run_async(execute_python_handler(
                {"code": "while True: pass"},
                tool_context,
            ))
            assert result.is_error
            assert "timed out" in result.content.lower() or "exceeded" in result.content.lower()


# --- macOS sandbox-exec command builder ---

class TestMacosSandboxCmd:
    """Tests for the _build_macos_sandbox_cmd function."""

    def test_returns_none_on_linux(self):
        """Should return None on non-macOS platforms."""
        with patch("consensus.tools_python.platform.system", return_value="Linux"):
            result = _build_macos_sandbox_cmd("/usr/bin/python3", ["-m", "worker"], "/tmp/sb")
            assert result is None

    def test_returns_none_if_sandbox_exec_missing(self):
        """Should return None if sandbox-exec binary doesn't exist."""
        with patch("consensus.tools_python.platform.system", return_value="Darwin"):
            with patch("consensus.tools_python.os.path.exists", return_value=False):
                result = _build_macos_sandbox_cmd("/usr/bin/python3", ["-m", "worker"], "/tmp/sb")
                assert result is None

    def test_returns_command_on_macos(self):
        """Should return a valid command list on macOS with sandbox-exec present."""
        with patch("consensus.tools_python.platform.system", return_value="Darwin"):
            with patch("consensus.tools_python.os.path.exists", return_value=True):
                result = _build_macos_sandbox_cmd(
                    "/usr/bin/python3", ["-m", "consensus.sandbox_worker"], "/tmp/sb-1234",
                )
                assert result is not None
                assert result[0] == "/usr/bin/sandbox-exec"
                assert "-p" in result
                assert "/usr/bin/python3" in result
                assert "-m" in result
                # Profile should deny network
                profile_idx = result.index("-p") + 1
                assert "deny network" in result[profile_idx]
                # Profile should allow writes to sandbox dir
                assert "/tmp/sb-1234" in result[profile_idx]


# --- install_python_package handler ---

class TestInstallPackageHandler:
    """Tests for the install_python_package tool handler."""

    @pytest.fixture
    def mock_app(self):
        """Create a mock ConsensusApp with the required interface."""
        app = MagicMock()
        app._pending_user_inputs = {}
        app.db.get_entity.return_value = {"name": "TestBot"}
        return app

    @pytest.fixture
    def install_context(self):
        """ToolContext for install tests."""
        return ToolContext(caller_entity_id=1, discussion_id=1)

    def test_empty_package_name(self, install_context, mock_app):
        """Empty package name should return error."""
        result = run_async(_install_package_handler(
            {"package_name": "", "reason": "need it"},
            install_context, mock_app,
        ))
        assert result.is_error
        assert "No package name" in result.content

    def test_invalid_package_name(self, install_context, mock_app):
        """Invalid package name should be rejected."""
        result = run_async(_install_package_handler(
            {"package_name": "pkg; rm -rf /", "reason": "need it"},
            install_context, mock_app,
        ))
        assert result.is_error
        assert "Invalid package name" in result.content

    def test_already_installed(self, install_context, mock_app):
        """Already-installed package should return success without user prompt."""
        # Mock the subprocess check to return success (import worked)
        mock_proc = AsyncMock()
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0

        async def run_test():
            with patch("consensus.tools_python.asyncio.create_subprocess_exec",
                       return_value=mock_proc):
                return await _install_package_handler(
                    {"package_name": "json", "reason": "need JSON"},
                    install_context, mock_app,
                )

        result = run_async(run_test())
        assert not result.is_error
        assert "already installed" in result.content

    def test_user_denies_install(self, install_context, mock_app):
        """User denial should return error."""
        async def simulate_denial():
            # Wait for the handler to register the pending input
            while not mock_app._pending_user_inputs:
                await asyncio.sleep(0.01)
            # Get the future and resolve with "no"
            request_id = next(iter(mock_app._pending_user_inputs))
            future, _ = mock_app._pending_user_inputs[request_id]
            future.set_result("no")

        async def run_test():
            denial_task = asyncio.create_task(simulate_denial())
            result = await _install_package_handler(
                {"package_name": "nonexistent_pkg_xyz_12345", "reason": "testing"},
                install_context, mock_app,
            )
            await denial_task
            return result

        result = run_async(run_test())
        assert result.is_error
        assert "denied" in result.content

    def test_user_approves_install(self, install_context, mock_app):
        """User approval should trigger uv pip install."""
        async def simulate_approval():
            while not mock_app._pending_user_inputs:
                await asyncio.sleep(0.01)
            request_id = next(iter(mock_app._pending_user_inputs))
            future, _ = mock_app._pending_user_inputs[request_id]
            future.set_result("yes")

        # Mock the subprocess to avoid actually installing
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"Successfully installed pkg\n", b"")
        mock_proc.returncode = 0

        async def run_test():
            approval_task = asyncio.create_task(simulate_approval())
            with patch("consensus.tools_python.asyncio.create_subprocess_exec") as mock_exec:
                # First call: import check (fail = not installed)
                check_proc = AsyncMock()
                check_proc.wait.return_value = 1
                check_proc.returncode = 1
                # Second call: uv pip install (succeed)
                mock_exec.side_effect = [check_proc, mock_proc]
                result = await _install_package_handler(
                    {"package_name": "some-new-pkg", "reason": "testing"},
                    install_context, mock_app,
                )
            await approval_task
            return result

        result = run_async(run_test())
        assert not result.is_error
        assert "Successfully installed" in result.content

    def test_approval_prompt_shows_package_info(self, install_context, mock_app):
        """The approval prompt should contain package name and reason."""
        async def capture_and_deny():
            while not mock_app._pending_user_inputs:
                await asyncio.sleep(0.01)
            request_id = next(iter(mock_app._pending_user_inputs))
            _, request_data = mock_app._pending_user_inputs[request_id]
            # Verify the prompt content
            assert "fancy-lib" in request_data["question"]
            assert "matrix operations" in request_data["question"]
            assert "uv pip install" in request_data["question"]
            # Deny to end the flow
            future = mock_app._pending_user_inputs[request_id][0]
            future.set_result("no")

        async def run_test():
            task = asyncio.create_task(capture_and_deny())
            result = await _install_package_handler(
                {"package_name": "fancy-lib", "reason": "matrix operations"},
                install_context, mock_app,
            )
            await task
            return result

        run_async(run_test())
