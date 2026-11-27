"""``execute_python``: AST pre-analysis, sandboxed worker launch, result formatting."""

import ast
import asyncio
import json
import locale
import logging
import os
import platform
import shutil
import site
import stat
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Optional

from ..error_text import public_error
from ..frozen import is_frozen, worker_command
from ..tools import ToolContext, ToolResult
from .constants import (
    BLOCKED_ATTRS, BLOCKED_BUILTINS, BLOCKED_MODULES, FALLBACK_CPU_LIMIT_SECONDS,
    MIN_WALL_TIMEOUT_SECONDS, PYTHON_MAX_OUTPUT, SEATBELT_PRIVATE_ROOTS,
    SEATBELT_UNSAFE_PATH_CHARS, WALL_TIMEOUT_MULTIPLIER,
    WORKER_DIAGNOSTIC_MAX_CHARS,
)

logger = logging.getLogger(__name__)


async def kill_if_running(proc: Optional[asyncio.subprocess.Process]) -> None:
    """Kill and reap *proc* unless it has already exited (or never started).

    Called from ``finally`` blocks so timeouts *and* task cancellation both
    leave no child process running.

    Args:
        proc: The subprocess, or None if launching it failed.
    """
    if proc is not None and proc.returncode is None:
        proc.kill()
        await proc.wait()


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


def worker_read_paths() -> list[str]:
    """Return the resolved directories the sandbox worker's interpreter reads.

    The worker runs this interpreter with this ``site`` setup, so it needs
    the prefixes and every ``sys.path`` entry (``consensus`` is in
    site-packages or, installed editable, in its finder's ``MAPPING``). The
    parent's ``sys.path[0]`` (the launch directory or script directory) is
    left out: the non-frozen worker runs with ``-P``, so it has none. So is
    the parent's cwd, wherever it sits in ``sys.path``, and any entry that
    is or contains ``$HOME`` or a ``SEATBELT_PRIVATE_ROOTS`` root (logged),
    since re-allowing it would undo the deny. A frozen app needs its
    bundle's ``Contents`` directory; otherwise the worker script's own
    directory is added. Editable namespace packages and ``.pth`` files
    written after startup (``install_python_package``) are included too.

    Returns:
        Sorted, de-duplicated real paths that exist.
    """
    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    paths.update(sys.path[1:])
    # setuptools editable installs map a package to its source dir in a finder.
    for name, module in list(sys.modules.items()):
        if name.startswith("__editable__"):
            paths.update(getattr(module, "MAPPING", {}).values())
            for dirs in getattr(module, "NAMESPACES", {}).values():
                paths.update(dirs)
    # A package installed since startup may have added a .pth (issue #166).
    for site_dir in site.getsitepackages():
        paths.update(pth_paths(site_dir))
    if is_frozen():
        paths.add(str(Path(sys.executable).resolve().parents[1]))
    else:
        # The worker runs by path (frozen.worker_command), so its directory.
        paths.add(str(Path(__file__).resolve().parents[1]))
    real = {os.path.realpath(p) for p in paths if p and os.path.exists(p)}
    real.discard(os.path.realpath(os.getcwd()))
    covered = [os.path.realpath(Path.home()), *SEATBELT_PRIVATE_ROOTS]
    broad = {p for p in real if any(_is_same_or_ancestor(p, c) for c in covered)}
    for path in sorted(broad):
        logger.warning("Sandbox profile will not re-allow reads under %r: it "
                       "covers the home directory or a user-data root", path)
    return sorted(real - broad)


def pth_paths(site_dir: str) -> list[str]:
    """Return the directories the ``.pth`` files in *site_dir* add.

    Mirrors ``site.addsitedir`` and ``site.addpackage``: names starting with
    ``.`` and files flagged hidden are skipped, content is decoded as
    ``utf-8-sig`` with a locale fallback, and blank lines, ``#`` comments and
    lines starting with ``import`` are skipped; other lines are paths
    relative to *site_dir*. Existence is checked by the caller.

    Args:
        site_dir: A site-packages directory.

    Returns:
        The paths, unresolved; empty if *site_dir* cannot be listed.
    """
    try:
        names = sorted(n for n in os.listdir(site_dir)
                       if n.endswith(".pth") and not n.startswith("."))
    except FileNotFoundError:
        return []
    except OSError as e:
        logger.warning("Sandbox profile skips .pth files in %s: %s", site_dir, e)
        return []
    paths = []
    for name in names:
        full = os.path.join(site_dir, name)
        try:
            if getattr(os.lstat(full), "st_flags", 0) & stat.UF_HIDDEN:
                continue
            with open(full, "rb") as f:
                raw = f.read()
        except OSError as e:
            logger.warning("Sandbox profile skips unreadable %s: %s; imports from "
                           "the paths it adds will fail", full, e)
            continue
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode(locale.getencoding(), errors="replace")
        for line in text.splitlines():
            line = line.rstrip()
            if line and not line.startswith(("#", "import ", "import\t")):
                paths.append(os.path.join(site_dir, line))
    return paths


def _is_same_or_ancestor(path: str, other: str) -> bool:
    """Return True if *path* is *other* or one of its parent directories."""
    return other == path or other.startswith(path.rstrip(os.sep) + os.sep)


def _seatbelt_subpaths(paths: Iterable[str]) -> str:
    """Render *paths* as Seatbelt ``(subpath ...)`` filters.

    A path holding a quote or backslash would break out of the profile
    string, so it is left out and logged; its imports then fail visibly.

    Args:
        paths: Resolved directory paths.

    Returns:
        The filters, space-separated.
    """
    safe = []
    for path in paths:
        if SEATBELT_UNSAFE_PATH_CHARS.intersection(path):
            logger.warning("Sandbox profile skips a path it cannot quote: %r", path)
        else:
            safe.append(f'(subpath "{path}")')
    return " ".join(safe)


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

    Raises:
        ValueError: *sandbox_dir* resolves to a path the profile cannot quote.
    """
    if platform.system() != "Darwin":
        return None

    # Check if sandbox-exec is available
    sandbox_exec = "/usr/bin/sandbox-exec"
    if not os.path.exists(sandbox_exec):
        return None

    # Seatbelt profile: deny network; writes only under sandbox_dir (the
    # worker's TMPDIR, so its scratch dir lands there, issue #97); no file
    # contents or listings under the user-data roots except what the
    # interpreter needs (issue #163). Stat stays allowed everywhere, so path
    # resolution works. Seatbelt matches resolved paths (/var/folders/... is
    # /private/var/...), and the re-allow must name file-read-data: a later
    # file-read* does not override an earlier file-read-data deny.
    sandbox_real = os.path.realpath(sandbox_dir)
    if SEATBELT_UNSAFE_PATH_CHARS.intersection(sandbox_real):
        raise ValueError(f"sandbox profile cannot quote the run directory {sandbox_real!r}")
    readable =_seatbelt_subpaths(worker_read_paths() + [sandbox_real])
    private = _seatbelt_subpaths(SEATBELT_PRIVATE_ROOTS)
    profile = f"""
(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow file-read*)
(deny file-read-data {private})
(allow file-read-data {readable})
(allow file-write* (subpath "{sandbox_real}"))
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

    # The only writable path: the worker gets it as TMPDIR, so its scratch dir
    # and any library cache that falls back to the temp dir land inside it,
    # and the macOS Seatbelt profile confines writes to it (issue #97).
    sandbox_dir = tempfile.mkdtemp(prefix="consensus-sandbox-")
    # MPLCONFIGDIR: matplotlib otherwise warns that ~/.matplotlib is read-only.
    worker_env = {**os.environ, "TMPDIR": sandbox_dir, "MPLCONFIGDIR": sandbox_dir}

    process = None
    try:
        # Try macOS sandbox-exec wrapper
        try:
            sandbox_cmd = _build_macos_sandbox_cmd(python_exe, worker_args, sandbox_dir)
        except ValueError as e:
            logger.error("Cannot build the sandbox profile: %s", e)
            return ToolResult(
                content=("Code execution is unavailable: the sandbox cannot run "
                         "from a temp directory whose path holds a quote or "
                         "backslash. The operator should check the server log."),
                is_error=True,
            )

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
                env=worker_env,
                # The worker calls os.getcwd(), which fails in a directory the
                # profile cannot read; it is also the sys.path[0] of a
                # non-frozen (python -m) worker.
                cwd=sandbox_dir,
            )
        except Exception as e:
            logger.error("Failed to launch sandbox worker: %s", e)
            return ToolResult(
                content=public_error(
                    "Failed to start code execution environment", e),
                is_error=True,
            )

        # Send code via stdin and wait for result with timeout
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(input=code.encode("utf-8")),
                timeout=wall_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("Sandboxed code timed out after %.0fs", wall_timeout)
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
            logger.warning(
                "Sandbox worker exited with code %s and no output: %s",
                process.returncode, stderr_str[:WORKER_DIAGNOSTIC_MAX_CHARS],
            )
            error_msg = stderr_str.strip() or f"Process exited with code {process.returncode}"
            if sandbox_cmd and "Operation not permitted" in error_msg:
                # The profile stopped the worker loading its own files; the
                # traceback holds host paths, so it stays in the log (#163).
                logger.error("Seatbelt profile blocked the sandbox worker: %s",
                             stderr_str[:WORKER_DIAGNOSTIC_MAX_CHARS])
                error_msg = (
                    "The macOS sandbox stopped the code runner from loading "
                    "its own files. This is a server configuration problem, "
                    "not an error in your code; the operator should check the "
                    "server log."
                )
            elif "MemoryError" in error_msg:
                error_msg = "Code execution exceeded the memory limit."
            elif process.returncode == -9:
                # RLIMIT_AS raises MemoryError; a bare SIGKILL is usually the OS
                # OOM killer, but could be any external kill.
                error_msg = (
                    "Code execution was killed by signal 9 (SIGKILL), most "
                    "likely for running out of memory."
                )
            elif process.returncode == -24 or "SIGXCPU" in error_msg:
                error_msg = "Code execution exceeded the CPU time limit."
            return ToolResult(content=f"Execution error: {error_msg}", is_error=True)

        # Parse JSON result from worker
        try:
            result_data = json.loads(stdout_str)
        except json.JSONDecodeError:
            # Worker produced non-JSON output (crash or unexpected behavior)
            logger.warning(
                "Sandbox worker returned non-JSON output (exit code %s)",
                process.returncode,
            )
            return ToolResult(
                content=(
                    "Execution error: unexpected worker output\n"
                    f"{stdout_str[:WORKER_DIAGNOSTIC_MAX_CHARS]}\n"
                    f"{stderr_str[:WORKER_DIAGNOSTIC_MAX_CHARS]}"
                ).rstrip(),
                is_error=True,
            )

        # Format output
        return _format_result(result_data, description)

    finally:
        # Covers timeout and cancellation alike: never leave the worker running.
        await kill_if_running(process)
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

