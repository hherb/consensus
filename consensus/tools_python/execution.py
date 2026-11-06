"""``execute_python``: AST pre-analysis, sandboxed worker launch, result formatting."""

import ast
import asyncio
import json
import logging
import os
import platform
import shutil
import tempfile
from typing import Optional

from ..frozen import worker_command
from ..tools import ToolContext, ToolResult
from .constants import (
    BLOCKED_ATTRS, BLOCKED_BUILTINS, BLOCKED_MODULES, FALLBACK_CPU_LIMIT_SECONDS,
    MIN_WALL_TIMEOUT_SECONDS, PYTHON_MAX_OUTPUT, WALL_TIMEOUT_MULTIPLIER,
)

logger = logging.getLogger(__name__)


def _compute_wall_timeout() -> float:
    """Compute wall-clock timeout that scales with the sandbox CPU limit.

    Returns:
        Timeout in seconds, always >= MIN_WALL_TIMEOUT_SECONDS.
    """
    try:
        from ..sandbox_worker import _compute_cpu_time_limit
        cpu_limit = _compute_cpu_time_limit()
    except Exception as e:
        logger.warning(
            "Could not compute sandbox CPU limit (%s); assuming %ss",
            e, FALLBACK_CPU_LIMIT_SECONDS,
        )
        cpu_limit = FALLBACK_CPU_LIMIT_SECONDS
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
        shutil.rmtree(sandbox_dir, ignore_errors=True)


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

