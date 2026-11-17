"""Check tool arguments against the tool's ``inputSchema`` (issue #124).

Covers the subset of JSON Schema that ``tool_defs.py`` uses: required keys,
top-level ``type``, array ``items`` and ``enum``. Lenient where clients
are: a numeric string (``"5"``) is coerced for an ``integer`` / ``number``
argument, as the handlers' ``int()`` calls accepted it before, and an
explicit ``null`` for an optional argument means "not given".
"""

from __future__ import annotations

import math
import re
from typing import Any, Callable

from .errors import ToolError

#: SQLite's INTEGER range; a larger id would fail in the DB as OverflowError.
_INT64 = range(-2**63, 2**63)
#: 19 digits spans int64; the cap also keeps ``int()`` clear of Python's
#: max-str-digits limit, which raises ValueError.
_INTEGER_TEXT = re.compile(r"[+-]?[0-9]{1,19}")


class _Invalid(Exception):
    """A value does not match its declared type."""


def _integer(value: Any) -> int:
    """Return ``value`` as an int64; integral floats and digit strings pass."""
    if isinstance(value, bool):
        raise _Invalid
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    elif isinstance(value, str) and _INTEGER_TEXT.fullmatch(value.strip()):
        value = int(value)
    if not isinstance(value, int) or value not in _INT64:
        raise _Invalid
    return value


def _number(value: Any) -> int | float:
    """Return ``value`` as a finite number, accepting numeric strings."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise _Invalid
    try:
        number = float(value)  # an int past float range raises OverflowError
    except (ValueError, OverflowError):
        raise _Invalid from None
    if not math.isfinite(number):
        raise _Invalid
    return number if isinstance(value, str) else value


def _exact(kind: type) -> Callable[[Any], Any]:
    """Return a check that accepts only instances of ``kind``, uncoerced."""
    def check(value: Any) -> Any:
        """Return ``value`` unchanged if it is a ``kind``."""
        if not isinstance(value, kind):
            raise _Invalid
        return value
    return check


#: JSON Schema type → a check that returns the (coerced) value or raises.
TYPE_CHECKS: dict[str, Callable[[Any], Any]] = {
    "integer": _integer,
    "number": _number,
    "string": _exact(str),
    "boolean": _exact(bool),
    "array": _exact(list),
}

#: Property keywords this module understands; a tool def using any other
#: one would be advertised but not enforced (pinned by a test).
SUPPORTED_KEYWORDS = {"type", "description", "enum", "items"}

_ARTICLES = {"integer": "an integer", "array": "an array"}


def _expected(kind: str) -> str:
    """Return ``kind`` with its article, for an error message."""
    return _ARTICLES.get(kind, f"a {kind}")


def _check(name: str, spec: dict, value: Any) -> Any:
    """Return ``value`` coerced to ``spec``; raise ``ToolError`` on mismatch."""
    kind = spec["type"]
    try:
        value = TYPE_CHECKS[kind](value)
    except _Invalid:
        raise ToolError(f"{name} must be {_expected(kind)}") from None
    if "enum" in spec and value not in spec["enum"]:
        raise ToolError(f"{name} must be one of: "
                        + ", ".join(map(str, spec["enum"])))
    if "items" in spec:
        value = [_check(f"{name}[{i}]", spec["items"], item)
                 for i, item in enumerate(value)]
    return value


def validate_arguments(schema: dict, arguments: dict) -> dict:
    """Return a coerced copy of ``arguments`` valid against ``schema``.

    Raises ``ToolError`` listing every problem (``"; "``-separated).
    Arguments the schema does not declare pass through unchanged.
    """
    properties = schema.get("properties", {})
    result: dict = {}
    problems: list[str] = []
    for name, value in arguments.items():
        if name not in properties:
            result[name] = value
        elif value is not None:
            try:
                result[name] = _check(name, properties[name], value)
            except ToolError as e:
                problems.append(str(e))
    problems = [f"{name} is required" for name in schema.get("required", [])
                if arguments.get(name) is None] + problems
    if problems:
        raise ToolError("; ".join(problems))
    return result
