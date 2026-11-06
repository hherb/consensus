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

The package is split by concern (issue #61): ``constants``, ``schemas``,
``execution`` (``execute_python``), ``install`` (``install_python_package``)
and ``provider``. Production code imports only the name below; tests import
private helpers from their submodule.
"""

from .provider import create_python_provider

__all__ = ["create_python_provider"]
