"""JSON schemas for the ``execute_python`` and ``install_python_package`` tools."""

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
