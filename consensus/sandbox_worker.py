"""Sandboxed Python code execution worker.

This module runs as a separate subprocess to execute untrusted Python code
with restricted builtins, whitelisted imports, resource limits, and
filesystem isolation. It receives code via stdin and outputs structured
JSON results to stdout.

Security layers applied:
1. Resource limits (RLIMIT_AS for memory, RLIMIT_CPU for CPU time)
2. Restricted builtins (dangerous functions removed)
3. Whitelisted imports (only safe stdlib modules allowed)
4. Sandboxed file I/O (open() only allows paths inside temp directory)
"""

import ast
import io
import json
import multiprocessing
import os
import sys
import tempfile
import traceback
from types import CodeType
from typing import Any, Optional, Union

# --- Resource limit policy ---
# Fractions of available system resources allocated to the sandbox worker.
# Generous limits to support ML workloads (torch, numpy, etc.).

MEMORY_FRACTION = 0.70         # Use up to 70% of available (free) memory
CPU_FRACTION = 0.70            # Use up to 70% of available CPU cores
MIN_MEMORY_BYTES = 256 * 1024 * 1024   # Floor: 256 MB even on constrained systems
MIN_CPU_TIME_SECONDS = 10              # Floor: at least 10 seconds of CPU time


def _get_available_memory_bytes() -> int:
    """Return available (free) system memory in bytes.

    Falls back to total memory if free memory cannot be determined.
    """
    try:
        import psutil
        return psutil.virtual_memory().available
    except ImportError:
        pass

    # macOS: parse vm_stat for free + inactive pages (approximates available memory)
    if sys.platform == "darwin":
        try:
            import subprocess
            result = subprocess.run(
                ["vm_stat"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                import re as _re
                text = result.stdout
                # Extract page size (first line, e.g. "...page size of 16384 bytes")
                page_match = _re.search(r"page size of (\d+) bytes", text)
                page_size = int(page_match.group(1)) if page_match else 16384
                free = 0
                for label in ("Pages free", "Pages inactive"):
                    m = _re.search(rf"{label}:\s+(\d+)", text)
                    if m:
                        free += int(m.group(1))
                if free > 0:
                    return free * page_size
        except Exception:
            pass

    # Linux: parse /proc/meminfo
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024  # kB -> bytes
    except (OSError, ValueError):
        pass

    # Absolute fallback: assume 8 GB total, allocate fraction of that
    return 8 * 1024 * 1024 * 1024


def _compute_memory_limit() -> int:
    """Compute the memory limit for the sandbox worker."""
    available = _get_available_memory_bytes()
    limit = int(available * MEMORY_FRACTION)
    return max(limit, MIN_MEMORY_BYTES)


def _compute_cpu_time_limit() -> int:
    """Compute the CPU time limit in seconds.

    Scales with the number of CPU cores so multi-core ML workloads
    get proportional time.
    """
    ncpus = multiprocessing.cpu_count()
    # Base of 30s per core at 70% utilisation, minimum 10s
    limit = int(ncpus * 30 * CPU_FRACTION)
    return max(limit, MIN_CPU_TIME_SECONDS)

ALLOWED_MODULES = frozenset({
    # Math and numerics
    "math", "cmath", "statistics", "decimal", "fractions", "numbers",
    "random",
    # Data structures and functional
    "collections", "itertools", "functools", "operator", "copy",
    # Serialization (safe formats only)
    "json", "csv", "struct",
    # Text processing
    "re", "string", "textwrap", "difflib", "unicodedata",
    # Date/time
    "datetime", "time", "calendar",
    # Hashing and encoding
    "hashlib", "base64", "uuid", "zlib",
    # Containers and algorithms
    "bisect", "heapq", "array",
    # Formatting and types
    "pprint", "dataclasses",
    "typing", "enum", "abc", "contextlib",
    "io", "html",
    # Scientific / computational libraries (safe — no I/O or system side effects)
    "numpy", "scipy", "pandas", "sklearn", "scikit_learn",
    "torch", "torchvision", "torchaudio",
    "tensorflow", "keras", "jax", "flax",
    "sympy", "mpmath",
    "matplotlib", "seaborn", "plotly",
    "hypercomplex",
    "networkx", "igraph",
    "PIL", "pillow",
    "cv2",
    # Additional safe third-party computational libraries
    "astropy", "biopython", "Bio",
    "shapely", "pyproj",
    "pint", "uncertainties",
    "regex",
})

# Builtins to remove from the execution namespace
BLOCKED_BUILTINS = frozenset({
    "exec", "eval", "compile", "__import__",
    "breakpoint", "exit", "quit", "input", "help",
    "globals", "locals", "vars",
    "getattr", "setattr", "delattr",
    "memoryview",
})


def _apply_resource_limits() -> None:
    """Apply OS-level resource limits to this process.

    Limits are computed dynamically from available system resources
    (MEMORY_FRACTION of free RAM, CPU_FRACTION of cores).
    """
    try:
        import resource
        mem_limit = _compute_memory_limit()
        cpu_limit = _compute_cpu_time_limit()
        # Memory limit (address space)
        resource.setrlimit(resource.RLIMIT_AS, (mem_limit, mem_limit))
        # CPU time limit (soft, hard = soft + 2s grace for cleanup)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit + 2))
    except (ImportError, ValueError, OSError):
        # resource module not available (Windows) or limits not supported
        pass


def _make_safe_builtins() -> dict[str, Any]:
    """Create a restricted copy of __builtins__ with dangerous functions removed."""
    import builtins
    safe = {}
    for name in dir(builtins):
        if name.startswith("_"):
            continue
        if name in BLOCKED_BUILTINS:
            continue
        safe[name] = getattr(builtins, name)

    # Keep __name__ and __build_class__ (needed for class definitions)
    safe["__name__"] = "__sandbox__"
    safe["__build_class__"] = builtins.__build_class__
    return safe


def _make_restricted_import(sandboxed_open):
    """Create a restricted __import__ that only allows whitelisted modules.

    Args:
        sandboxed_open: The sandboxed open() function. Used to patch modules
            like ``io`` that expose their own file-open capabilities.
    """
    original_import = __import__

    def restricted_import(name: str, *args: Any, **kwargs: Any) -> Any:
        """Import hook that only allows whitelisted modules."""
        top_level = name.split(".")[0]
        if top_level not in ALLOWED_MODULES:
            raise ImportError(
                f"Module '{name}' is not available in the sandbox. "
                f"Allowed modules: {', '.join(sorted(ALLOWED_MODULES))}"
            )
        mod = original_import(name, *args, **kwargs)
        # Patch io module to prevent filesystem bypass via io.open / io.FileIO
        if top_level == "io":
            mod.open = sandboxed_open
            for attr in ("FileIO", "RawIOBase"):
                if hasattr(mod, attr):
                    delattr(mod, attr)
        return mod

    return restricted_import


def _make_sandboxed_open(sandbox_dir: str):
    """Create a sandboxed open() that only allows access within the sandbox directory."""
    original_open = open

    def sandboxed_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        """Open files only within the sandbox directory."""
        filepath = os.path.realpath(str(file))
        sandbox_real = os.path.realpath(sandbox_dir)
        if not filepath.startswith(sandbox_real + os.sep) and filepath != sandbox_real:
            raise PermissionError(
                f"File access denied: only files in the sandbox directory are accessible. "
                f"Use relative paths or paths within the sandbox."
            )
        return original_open(file, mode, *args, **kwargs)

    return sandboxed_open


def _extract_last_expr(code: str) -> tuple[Union[str, CodeType], Optional[str]]:
    """If the last statement is an expression, rewrite it to capture its value.

    Returns:
        Tuple of (modified_code_or_compiled, capture_var_name) where
        capture_var_name is None if the last statement is not an expression.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, None

    if not tree.body:
        return code, None

    last_stmt = tree.body[-1]
    if not isinstance(last_stmt, ast.Expr):
        return code, None

    # Replace last expression with assignment to capture variable
    capture_var = "__sandbox_result__"
    assign = ast.Assign(
        targets=[ast.Name(id=capture_var, ctx=ast.Store())],
        value=last_stmt.value,
        lineno=last_stmt.lineno,
        col_offset=last_stmt.col_offset,
    )
    ast.copy_location(assign, last_stmt)
    tree.body[-1] = assign
    ast.fix_missing_locations(tree)

    return compile(tree, "<sandbox>", "exec"), capture_var


def execute_code(code: str) -> dict[str, Any]:
    """Execute Python code in a restricted environment.

    Args:
        code: Python source code to execute.

    Returns:
        Dictionary with stdout, stderr, last_expr, and error fields.
    """
    result: dict[str, Any] = {
        "stdout": "",
        "stderr": "",
        "last_expr": None,
        "error": None,
    }

    # Create temporary sandbox directory
    sandbox_dir = tempfile.mkdtemp(prefix="consensus-sandbox-")

    # Save original io.open so we can restore it after execution
    # (important when execute_code is called in-process, e.g. tests)
    _orig_io_open = io.open
    _orig_io_fileio = getattr(io, "FileIO", None)
    _orig_io_rawiobase = getattr(io, "RawIOBase", None)

    try:
        # Prepare restricted builtins
        safe_builtins = _make_safe_builtins()
        sandbox_open = _make_sandboxed_open(sandbox_dir)
        safe_builtins["__import__"] = _make_restricted_import(sandbox_open)
        safe_builtins["open"] = sandbox_open
        safe_builtins["print"] = print  # Will be captured via stdout redirect

        # Prepare execution namespace
        exec_globals: dict[str, Any] = {"__builtins__": safe_builtins}

        # Process last expression for REPL-like behavior
        compiled_or_code, capture_var = _extract_last_expr(code)

        # Redirect stdout/stderr
        old_stdout, old_stderr = sys.stdout, sys.stderr
        captured_stdout = io.StringIO()
        captured_stderr = io.StringIO()
        sys.stdout = captured_stdout
        sys.stderr = captured_stderr

        try:
            # Change to sandbox directory so relative paths stay contained
            old_cwd = os.getcwd()
            os.chdir(sandbox_dir)

            try:
                if isinstance(compiled_or_code, str):
                    exec(compile(compiled_or_code, "<sandbox>", "exec"), exec_globals)
                else:
                    exec(compiled_or_code, exec_globals)

                # Capture last expression value
                if capture_var and capture_var in exec_globals:
                    value = exec_globals[capture_var]
                    if value is not None:
                        result["last_expr"] = repr(value)
            finally:
                os.chdir(old_cwd)

        except Exception:
            result["error"] = traceback.format_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            result["stdout"] = captured_stdout.getvalue()
            result["stderr"] = captured_stderr.getvalue()

    finally:
        # Restore io module to prevent leaking sandbox restrictions.  The
        # restricted importer deletes io.FileIO and io.RawIOBase, so both
        # must be restored or in-process callers (e.g. tests) permanently
        # corrupt the shared io module.
        io.open = _orig_io_open
        if _orig_io_fileio is not None:
            io.FileIO = _orig_io_fileio
        if _orig_io_rawiobase is not None:
            io.RawIOBase = _orig_io_rawiobase
        # Clean up sandbox directory
        try:
            import shutil
            shutil.rmtree(sandbox_dir, ignore_errors=True)
        except Exception:
            pass

    return result


def main() -> None:
    """Entry point when run as a subprocess."""
    _apply_resource_limits()

    # Read code from stdin
    code = sys.stdin.read()
    if not code.strip():
        json.dump({"stdout": "", "stderr": "", "last_expr": None, "error": "No code provided"}, sys.stdout)
        sys.exit(0)

    result = execute_code(code)

    # Output structured result
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
