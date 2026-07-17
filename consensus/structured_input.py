"""Build the human input spec for a structured phase (issue #57).

Pure helpers, no app/DB state: given the active method, the current
speaker, and the discussion, produce the ``current_input_spec`` the
frontend renders as a form (or a guided-JSON fallback).  Kept out of
``app.py`` so it is unit-testable without a running app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .methods.base import DiscussionMethod
    from .models import Discussion, Entity

#: JSON-Schema primitive types the generic form renderer supports.
_RENDERABLE_PRIMITIVES = ("string", "number", "integer", "boolean")


def _prop_renderable(prop: dict) -> bool:
    """Return True when one property subschema maps to a form widget."""
    if "enum" in prop:
        return True
    ptype = prop.get("type")
    if ptype in _RENDERABLE_PRIMITIVES:
        return True
    if ptype == "array":
        items = prop.get("items", {})
        return bool(items) and _prop_renderable(items)
    if ptype == "object":
        sub = prop.get("properties", {})
        if not sub:
            return False  # unresolved additionalProperties -> guided JSON
        return all(_prop_renderable(p) for p in sub.values())
    return False


def schema_is_renderable(schema: dict) -> bool:
    """Return True when every top-level property maps to a form widget.

    False collapses the whole form to the guided-JSON fallback (e.g. the
    ACH matrix's 2-level ``additionalProperties``).
    """
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return False
    return all(_prop_renderable(p) for p in props.values())


def build_input_spec(method: "Optional[DiscussionMethod]",
                     entity: "Optional[Entity]",
                     discussion: "Optional[Discussion]") -> Optional[dict]:
    """Return the current human speaker's structured input spec, or None.

    None when there is no active method or the current phase declares no
    output tool for this entity (an ordinary free-text turn).
    """
    if method is None or entity is None or discussion is None:
        return None
    spec = method.get_output_tool(entity, discussion)
    if spec is None:
        return None
    schema = method.resolve_input_schema(spec, entity, discussion)
    return {
        "tool_name": spec.name,
        "description": spec.description,
        "schema": schema,
        "renderable": schema_is_renderable(schema),
    }
