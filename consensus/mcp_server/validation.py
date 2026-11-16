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

_INTEGER_TEXT = re.compile(r"[+-]?[0-9]+")


class _Invalid(Exception):
    """A value does not match its declared type."""


def _integer(value: Any) -> int:
    if isinstance(value, bool):
        raise _Invalid
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and _INTEGER_TEXT.fullmatch(value.strip()):
        return int(value)
    raise _Invalid


def _number(value: Any) -> int | float:
    if isinstance(value, bool):
        raise _Invalid
    if isinstance(value, (int, float)):
        number = value
    elif isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            raise _Invalid from None
    else:
        raise _Invalid
    if not math.isfinite(number):
        raise _Invalid
    return number


def _exact(kind: type) -> Callable[[Any], Any]:
    def check(value: Any) -> Any:
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
