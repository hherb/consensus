"""Limits and security blocklists for the sandboxed Python tools."""

import re

PYTHON_MAX_OUTPUT: int = 50_000  # Max chars of output returned
# Wall-clock timeout multiplier: wall time = CPU limit * this factor.
# Factor > 1 accounts for I/O waits, library load time, and multi-core work
# that doesn't count against RLIMIT_CPU linearly.
WALL_TIMEOUT_MULTIPLIER: float = 1.5
MIN_WALL_TIMEOUT_SECONDS: float = 30.0  # Floor: never less than 30s
# CPU limit assumed when the sandbox worker's own computation fails.
FALLBACK_CPU_LIMIT_SECONDS: int = 30
INSTALL_APPROVAL_TIMEOUT: float = 300.0  # 5 minutes for user to approve
INSTALL_EXEC_TIMEOUT: float = 120.0  # 2 minutes for uv pip install
INSTALL_CHECK_TIMEOUT: float = 30.0  # importlib.metadata "already installed?" probe
INSTALL_ERROR_MAX_CHARS: int = 2000  # uv error output shown to the model
WORKER_DIAGNOSTIC_MAX_CHARS: int = 1000  # worker stdout/stderr shown on a crash
REQUEST_ID_HEX_CHARS: int = 8  # random suffix length of an approval request id
# Replies (lower-cased, stripped) that approve or deny a package install.
INSTALL_APPROVAL_WORDS = frozenset({"yes", "y", "approve", "ok"})
INSTALL_DENIAL_WORDS = frozenset({"no", "n", "deny"})
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
