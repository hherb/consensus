"""Assembles the ``python_exec`` tool provider."""

from ..tools import PythonToolProvider, ToolContext, ToolDefinition, ToolResult
from .execution import execute_python_handler
from .install import _install_package_handler
from .schemas import EXECUTE_PYTHON_SCHEMA, INSTALL_PACKAGE_SCHEMA


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

