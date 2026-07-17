"""Python code execution tool provider for Consensus.

Provides a sandboxed Python execution environment for AI participants
to write and run code during discussions. Uses multi-layered security:

1. AST pre-analysis — rejects dangerous imports/calls before execution
2. Subprocess isolation — code runs in a separate process
3. Resource limits — RLIMIT_AS (memory), RLIMIT_CPU (CPU time)
4. Restricted builtins and import whitelist in the worker process
5. Optional macOS sandbox-exec for OS-level filesystem/network isolation

Also provides an install_python_package tool that lets participants
request library installation with user approval.
"""

import ast
import asyncio
import json
import logging
import os
import platform
import re
import sys
import tempfile
import uuid
from typing import Optional

from .frozen import is_frozen, worker_command
from .tools import PythonToolProvider, ToolContext, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

# --- Configuration ---

PYTHON_MAX_OUTPUT: int = 50_000  # Max chars of output returned
# Wall-clock timeout multiplier: wall time = CPU limit * this factor.
# Factor > 1 accounts for I/O waits, library load time, and multi-core work
# that doesn't count against RLIMIT_CPU linearly.
WALL_TIMEOUT_MULTIPLIER: float = 1.5
MIN_WALL_TIMEOUT_SECONDS: float = 30.0  # Floor: never less than 30s
INSTALL_APPROVAL_TIMEOUT: float = 300.0  # 5 minutes for user to approve
INSTALL_EXEC_TIMEOUT: float = 120.0  # 2 minutes for uv pip install
# Regex for valid PyPI package names (PEP 508)
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")

# Modules blocked at AST analysis level (before execution)
BLOCKED_MODULES = frozenset({
    "os", "sys", "subprocess", "shutil", "pathlib",
    "socket", "http", "urllib", "requests", "httpx",
    "ctypes", "multiprocessing", "threading", "signal",
    "importlib", "pickle", "shelve", "marshal",
    "code", "codeop", "compileall", "py_compile",
    "webbrowser", "antigravity", "turtle",
    "tkinter", "xml", "lxml",
    "resource", "gc", "inspect", "dis",
    "pty", "fcntl", "termios", "tty",
    "select", "selectors", "mmap",
    "asyncio", "concurrent",
})

# Dangerous builtin function names
BLOCKED_BUILTINS = frozenset({
    "exec", "eval", "compile", "__import__", "breakpoint",
})

# Dangerous dunder attributes
BLOCKED_ATTRS = frozenset({
    "__subclasses__", "__globals__", "__builtins__", "__code__",
    "__bases__", "__mro__", "__import__", "__loader__",
    "__spec__", "__qualname__",
})

EXECUTE_PYTHON_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {
            "type": "string",
            "description": (
                "Python code to execute in a sandboxed environment. "
                "Has access to safe standard library modules including "
                "math, statistics, collections, itertools, json, re, "
                "datetime, csv, and others. Can read/write files in a "
                "temporary sandbox directory. No network access, no "
                "system commands, no file access outside the sandbox."
            ),
        },
        "description": {
            "type": "string",
            "description": "Brief description of what this code does",
        },
    },
    "required": ["code"],
}


INSTALL_PACKAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "package_name": {
            "type": "string",
            "description": (
                "Name of the Python package to install from PyPI "
                "(e.g. 'numpy', 'hypercomplex', 'torch'). "
                "The user will be asked to approve the installation."
            ),
        },
        "reason": {
            "type": "string",
            "description": (
                "Brief explanation of why this package is needed, "
                "shown to the user in the approval prompt."
            ),
        },
    },
    "required": ["package_name", "reason"],
}


def _compute_wall_timeout() -> float:
    """Compute wall-clock timeout that scales with the sandbox CPU limit.

    Returns:
        Timeout in seconds, always >= MIN_WALL_TIMEOUT_SECONDS.
    """
    try:
        from .sandbox_worker import _compute_cpu_time_limit
        cpu_limit = _compute_cpu_time_limit()
    except Exception:
        cpu_limit = 30  # safe fallback
    return max(cpu_limit * WALL_TIMEOUT_MULTIPLIER, MIN_WALL_TIMEOUT_SECONDS)


def _analyze_ast(code: str) -> list[str]:
    """Analyze code AST for dangerous patterns before execution.

    Args:
        code: Python source code to analyze.

    Returns:
        List of violation descriptions. Empty list means code is safe.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    violations: list[str] = []

    for node in ast.walk(tree):
        # Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                if top_level in BLOCKED_MODULES:
                    violations.append(
                        f"Import of '{alias.name}' is not allowed in the sandbox"
                    )

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_level = node.module.split(".")[0]
                if top_level in BLOCKED_MODULES:
                    violations.append(
                        f"Import from '{node.module}' is not allowed in the sandbox"
                    )

        # Check dangerous function calls
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in BLOCKED_BUILTINS:
                    violations.append(
                        f"Call to '{node.func.id}()' is not allowed in the sandbox"
                    )

        # Check dangerous attribute access
        elif isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_ATTRS:
                violations.append(
                    f"Access to '{node.attr}' is not allowed in the sandbox"
                )

    return violations


def _build_macos_sandbox_cmd(
    python_executable: str,
    worker_module_args: list[str],
    sandbox_dir: str,
) -> Optional[list[str]]:
    """Build a macOS sandbox-exec command if available.

    Args:
        python_executable: Path to the Python interpreter.
        worker_module_args: Arguments for the worker module.
        sandbox_dir: Path to the temporary sandbox directory.

    Returns:
        Command list wrapped with sandbox-exec, or None if not on macOS
        or sandbox-exec is not available.
    """
    if platform.system() != "Darwin":
        return None

    # Check if sandbox-exec is available
    sandbox_exec = "/usr/bin/sandbox-exec"
    if not os.path.exists(sandbox_exec):
        return None

    # Seatbelt profile: deny most operations, allow reads + sandbox writes
    profile = f"""
(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow file-read*)
(allow file-write* (subpath "{sandbox_dir}"))
(allow file-write* (subpath "/private/tmp/"))
(allow file-write* (subpath "/dev/null"))
(allow file-write-data (literal "/dev/fd/1"))
(allow file-write-data (literal "/dev/fd/2"))
(allow sysctl-read)
(allow mach-lookup)
(deny network*)
""".strip()

    return [sandbox_exec, "-p", profile, python_executable] + worker_module_args


async def execute_python_handler(
    arguments: dict,
    context: ToolContext,
) -> ToolResult:
    """Execute Python code in a sandboxed subprocess.

    Args:
        arguments: Tool arguments with 'code' (required) and 'description' (optional).
        context: Tool execution context.

    Returns:
        ToolResult with execution output or error message.
    """
    code = arguments.get("code", "").strip()
    description = arguments.get("description", "")

    if not code:
        return ToolResult(content="No code provided.", is_error=True)

    # Layer 1: AST pre-analysis
    violations = _analyze_ast(code)
    if violations:
        violation_list = "\n".join(f"  - {v}" for v in violations)
        return ToolResult(
            content=f"Code rejected by security analysis:\n{violation_list}",
            is_error=True,
        )

    # Prepare subprocess command (frozen app bundles a dedicated worker binary)
    python_exe, worker_args = worker_command()

    # Compute wall-clock timeout that scales with the dynamic CPU limit
    wall_timeout = _compute_wall_timeout()

    # Create a temp dir for macOS sandbox profile (worker creates its own internally too)
    sandbox_dir = tempfile.mkdtemp(prefix="consensus-sandbox-")

    try:
        # Try macOS sandbox-exec wrapper
        sandbox_cmd = _build_macos_sandbox_cmd(python_exe, worker_args, sandbox_dir)

        if sandbox_cmd:
            cmd = sandbox_cmd
            logger.debug("Using macOS sandbox-exec for code execution")
        else:
            cmd = [python_exe] + worker_args
            logger.debug("Using subprocess isolation for code execution")

        # Launch worker subprocess
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as e:
            logger.error("Failed to launch sandbox worker: %s", e)
            return ToolResult(
                content=f"Failed to start code execution environment: {e}",
                is_error=True,
            )

        # Send code via stdin and wait for result with timeout
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=code.encode("utf-8")),
                timeout=wall_timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(
                content=(
                    f"Code execution timed out after {wall_timeout:.0f} seconds. "
                    "The code may contain an infinite loop or very long computation."
                ),
                is_error=True,
            )

        # Parse worker output
        stdout_str = stdout_bytes.decode("utf-8", errors="replace")
        stderr_str = stderr_bytes.decode("utf-8", errors="replace")

        if process.returncode != 0 and not stdout_str.strip():
            # Worker crashed without producing output
            error_msg = stderr_str.strip() or f"Process exited with code {process.returncode}"
            if "MemoryError" in error_msg or process.returncode == -9:
                error_msg = "Code execution exceeded the memory limit."
            elif process.returncode == -24 or "SIGXCPU" in error_msg:
                error_msg = "Code execution exceeded the CPU time limit."
            return ToolResult(content=f"Execution error: {error_msg}", is_error=True)

        # Parse JSON result from worker
        try:
            result_data = json.loads(stdout_str)
        except json.JSONDecodeError:
            # Worker produced non-JSON output (crash or unexpected behavior)
            return ToolResult(
                content=f"Execution error: unexpected worker output\n{stderr_str[:1000]}",
                is_error=True,
            )

        # Format output
        return _format_result(result_data, description)

    finally:
        # Clean up sandbox directory
        try:
            import shutil
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception:
            pass


def _format_result(result_data: dict, description: str) -> ToolResult:
    """Format worker output into a ToolResult.

    Args:
        result_data: Parsed JSON from the sandbox worker.
        description: Optional description of what the code does.

    Returns:
        Formatted ToolResult.
    """
    parts: list[str] = []

    # Add description if provided
    if description:
        parts.append(f"[{description}]")

    # Check for execution errors
    error = result_data.get("error")
    if error:
        parts.append(f"Error:\n{error}")

    # Add stdout output
    stdout = result_data.get("stdout", "")
    if stdout:
        parts.append(f"Output:\n{stdout}")

    # Add last expression value
    last_expr = result_data.get("last_expr")
    if last_expr is not None:
        parts.append(f"Result: {last_expr}")

    # Add stderr if present (warnings, etc.)
    stderr = result_data.get("stderr", "")
    if stderr and not error:
        parts.append(f"Warnings:\n{stderr}")

    content = "\n\n".join(parts) if parts else "(no output)"

    # Truncate if too long
    if len(content) > PYTHON_MAX_OUTPUT:
        content = content[:PYTHON_MAX_OUTPUT] + f"\n\n[Output truncated at {PYTHON_MAX_OUTPUT} characters]"

    is_error = bool(error)
    metadata = {}
    if description:
        metadata["description"] = description

    return ToolResult(content=content, is_error=is_error, metadata=metadata)


def create_python_provider(app=None) -> PythonToolProvider:
    """Create and return the Python code execution tool provider.

    Args:
        app: Optional ConsensusApp instance. When provided, enables the
            install_python_package tool which requires user approval via
            the app's event emitter.

    Returns:
        Configured PythonToolProvider with execute_python and optionally
        install_python_package tools.
    """
    provider = PythonToolProvider(name="python_exec")
    provider.register(
        ToolDefinition(
            name="execute_python",
            description=(
                "Execute Python code in a secure sandbox. Use this to perform "
                "calculations, data analysis, string processing, or any "
                "computation that benefits from running actual code. "
                "Available modules include math, statistics, collections, "
                "itertools, json, re, datetime, csv, and other safe standard "
                "library modules. Scientific libraries like numpy, scipy, "
                "pandas, torch, hypercomplex, matplotlib, etc. are also "
                "allowed if installed. No network access or system commands. "
                "If a library is not installed, use install_python_package "
                "to request its installation."
            ),
            parameters=EXECUTE_PYTHON_SCHEMA,
        ),
        execute_python_handler,
    )

    if app is not None:
        async def install_package_handler(
            arguments: dict, context: ToolContext,
        ) -> ToolResult:
            """Install a Python package with user approval."""
            return await _install_package_handler(arguments, context, app)

        provider.register(
            ToolDefinition(
                name="install_python_package",
                description=(
                    "Request installation of a Python package from PyPI. "
                    "The user will be prompted to approve the installation. "
                    "Use this when execute_python fails due to a missing "
                    "library that is needed for the task."
                ),
                parameters=INSTALL_PACKAGE_SCHEMA,
            ),
            install_package_handler,
        )

    return provider


async def _install_package_handler(
    arguments: dict,
    context: ToolContext,
    app,
) -> ToolResult:
    """Handle package installation requests with user approval.

    Args:
        arguments: Tool arguments with 'package_name' and 'reason'.
        context: Tool execution context.
        app: ConsensusApp instance for event emission and user interaction.

    Returns:
        ToolResult indicating success or failure of installation.
    """
    if is_frozen():
        return ToolResult(
            content=(
                "Package installation is not available in the bundled "
                "desktop app. Install Consensus from PyPI instead "
                "(`uv tool install consensus-app`) to use this feature."
            ),
            is_error=True,
            metadata={"status": "unavailable_frozen"},
        )

    package_name = arguments.get("package_name", "").strip()
    reason = arguments.get("reason", "").strip()

    if not package_name:
        return ToolResult(content="No package name provided.", is_error=True)

    # Validate package name to prevent command injection
    if not PACKAGE_NAME_RE.match(package_name):
        return ToolResult(
            content=f"Invalid package name: '{package_name}'. "
            "Package names may only contain letters, numbers, dots, hyphens, and underscores.",
            is_error=True,
        )

    # Check if already installed.  Query the distribution (PyPI) name via
    # importlib.metadata rather than guessing the import name, which differs
    # for many packages (pillow→PIL, scikit-learn→sklearn, opencv-python→cv2).
    # package_name is already validated to bare [A-Za-z0-9._-], so embedding
    # it as a repr literal is safe.
    try:
        check_proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            f"import importlib.metadata as m; m.version({package_name!r})",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await check_proc.wait()
        if check_proc.returncode == 0:
            return ToolResult(
                content=f"Package '{package_name}' is already installed.",
                metadata={"package": package_name, "status": "already_installed"},
            )
    except Exception:
        pass  # Proceed to install attempt

    # Request user approval via the ask_user event pattern
    request_id = f"pkg_{context.discussion_id}_{uuid.uuid4().hex[:8]}"
    loop = asyncio.get_running_loop()
    future = loop.create_future()

    entity_name = ""
    if app.db:
        entity = app.db.get_entity(context.caller_entity_id)
        if entity:
            entity_name = entity.get("name", "")

    question = (
        f"**{entity_name or 'A participant'}** is requesting to install "
        f"Python package **{package_name}**.\n\n"
        f"**Reason:** {reason}\n\n"
        f"This will run: `uv pip install {package_name}`\n\n"
        f"Type **yes** to approve or **no** to deny."
    )

    request_data = {
        "request_id": request_id,
        "discussion_id": context.discussion_id,
        "entity_id": context.caller_entity_id,
        "entity_name": entity_name,
        "question": question,
        "context": f"Package installation request: {package_name}",
    }

    app._pending_user_inputs[request_id] = (future, request_data)
    app.emit("user_input_request", request_data)

    logger.info("install_python_package: %s requests '%s' (request_id=%s)",
                entity_name, package_name, request_id)

    try:
        user_answer = await asyncio.wait_for(future, timeout=INSTALL_APPROVAL_TIMEOUT)
    except asyncio.TimeoutError:
        logger.warning("Package install approval timed out (request_id=%s)", request_id)
        return ToolResult(
            content="The user did not respond to the installation request in time.",
            is_error=True,
        )
    except asyncio.CancelledError:
        return ToolResult(content="Installation request was cancelled.", is_error=True)
    finally:
        app._pending_user_inputs.pop(request_id, None)

    # Check approval
    answer = user_answer.strip().lower()
    if answer not in ("yes", "y", "approve", "ok"):
        return ToolResult(
            content=f"The user denied installation of '{package_name}'.",
            is_error=True,
            metadata={"package": package_name, "status": "denied"},
        )

    # Run uv pip install
    logger.info("Installing package '%s' (approved by user)", package_name)
    try:
        install_proc = await asyncio.create_subprocess_exec(
            "uv", "pip", "install", package_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            install_proc.communicate(),
            timeout=INSTALL_EXEC_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return ToolResult(
            content=f"Installation of '{package_name}' timed out after "
            f"{INSTALL_EXEC_TIMEOUT:.0f} seconds.",
            is_error=True,
        )
    except FileNotFoundError:
        return ToolResult(
            content="'uv' is not available. Cannot install packages.",
            is_error=True,
        )
    except Exception as e:
        logger.error("Failed to install package '%s': %s", package_name, e)
        return ToolResult(
            content=f"Installation failed: {e}",
            is_error=True,
        )

    stdout_str = stdout_bytes.decode("utf-8", errors="replace")
    stderr_str = stderr_bytes.decode("utf-8", errors="replace")

    if install_proc.returncode != 0:
        error_output = stderr_str.strip() or stdout_str.strip()
        logger.warning("uv pip install '%s' failed: %s", package_name, error_output)
        return ToolResult(
            content=f"Installation of '{package_name}' failed:\n{error_output[:2000]}",
            is_error=True,
            metadata={"package": package_name, "status": "failed"},
        )

    logger.info("Successfully installed '%s'", package_name)
    return ToolResult(
        content=f"Successfully installed '{package_name}'. You can now use it with execute_python.",
        metadata={"package": package_name, "status": "installed"},
    )
