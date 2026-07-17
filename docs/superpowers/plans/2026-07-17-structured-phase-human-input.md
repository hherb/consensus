# Structured-phase Human Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a human taking a turn in a `requires_structured_output` phase submit through a schema-driven form (validated and recorded on the same path the AI uses), with a guided-JSON fallback for nested schemas and a safety net so nothing is ever silently dropped.

**Architecture:** A dedicated backend endpoint `submit_human_structured_message(entity_id, payload)` validates a payload (structural pre-check → the handler's `validate_output`) and records it via `process_structured_response`, mirroring the AI branch. `get_state()` exposes a `current_input_spec` (the current human speaker's output-tool name/description/resolved-schema/renderable flag) built by a new pure `structured_input.py`. A new frontend module `structured-form.js` renders a generic form from that schema (guided-JSON textarea when `renderable` is false). A no-op guard on the existing `submit_human_message` turns the old silent drop into a visible error.

**Tech Stack:** Python 3 (backend, `uv` + `pytest`), vanilla ES-module JavaScript (frontend), SQLite. No new runtime dependencies.

## Global Constraints

- Package manager: **`uv` only, never `pip`**. Run tests with `uv run pytest`.
- **No new runtime dependency** (the project declares no jsonschema lib; validate by hand).
- Doc strings + type hints mandatory on every new function/method (golden rule 2).
- No magic numbers — use module constants (golden rule 3); reuse `BELIEF_MIN`/`BELIEF_MAX`.
- All caught errors shown in the UI and logged (golden rule 6): no silent drops.
- Keep files under ~500 lines; new logic goes in focused modules (golden rule 8).
- Frontend: relative units, no magic-number sizing; all output user-selectable; light/dark via existing CSS custom properties (golden rules 9/10 + project CSS convention).
- TDD: failing test first, minimal implementation, frequent commits.
- Branch: `feat/structured-phase-human-input` (already created). Commit footer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Spec: `docs/superpowers/specs/2026-07-17-structured-phase-human-input-design.md`.

---

### Task 1: `check_payload_schema` structural pre-check

**Files:**
- Modify: `consensus/methods/parsing.py` (append function + module constant)
- Test: `tests/test_parsing.py` (add a `TestCheckPayloadSchema` class; create the file if absent)

**Interfaces:**
- Produces: `check_payload_schema(payload: dict, schema: dict) -> str` — returns `""` when the payload structurally satisfies `schema` (a JSON-Schema `parameters` object), else a human-readable message. Validates required keys, primitive types (`string`/`number`/`integer`/`boolean`), `enum`, numeric `minimum`/`maximum`, and recurses one level into `array` items and `object` `properties`/`additionalProperties`. Library-free.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_parsing.py
from consensus.methods.parsing import check_payload_schema

POLL_SCHEMA = {
    "type": "object",
    "properties": {
        "belief": {"type": "number", "minimum": 0, "maximum": 1},
        "reasoning": {"type": "string"},
    },
    "required": ["belief", "reasoning"],
}


class TestCheckPayloadSchema:
    def test_accepts_valid_payload(self):
        assert check_payload_schema(
            {"belief": 0.7, "reasoning": "because"}, POLL_SCHEMA) == ""

    def test_missing_required_field_named(self):
        msg = check_payload_schema({"reasoning": "x"}, POLL_SCHEMA)
        assert "belief" in msg

    def test_number_out_of_range(self):
        msg = check_payload_schema(
            {"belief": 5, "reasoning": "x"}, POLL_SCHEMA)
        assert msg != ""

    def test_wrong_type(self):
        msg = check_payload_schema(
            {"belief": "high", "reasoning": "x"}, POLL_SCHEMA)
        assert "belief" in msg

    def test_enum_rejects_unknown_value(self):
        schema = {"type": "object",
                  "properties": {"stance": {"type": "string",
                                            "enum": ["updated", "unchanged"]}},
                  "required": ["stance"]}
        assert check_payload_schema({"stance": "updated"}, schema) == ""
        assert check_payload_schema({"stance": "maybe"}, schema) != ""

    def test_array_of_objects_recurses(self):
        schema = {"type": "object", "properties": {
            "cruxes": {"type": "array", "items": {"type": "object",
                       "properties": {"claim": {"type": "string"},
                                      "belief": {"type": "number",
                                                 "minimum": 0, "maximum": 1}},
                       "required": ["claim", "belief"]}}},
            "required": ["cruxes"]}
        assert check_payload_schema(
            {"cruxes": [{"claim": "c", "belief": 0.5}]}, schema) == ""
        assert check_payload_schema(
            {"cruxes": [{"claim": "c", "belief": 9}]}, schema) != ""

    def test_object_additionalproperties_values_checked(self):
        schema = {"type": "object", "properties": {
            "beliefs": {"type": "object", "additionalProperties": {
                "type": "number", "minimum": 0, "maximum": 1}}},
            "required": ["beliefs"]}
        assert check_payload_schema({"beliefs": {"H1": 0.5}}, schema) == ""
        assert check_payload_schema({"beliefs": {"H1": 2}}, schema) != ""

    def test_non_dict_payload_rejected(self):
        assert check_payload_schema([], POLL_SCHEMA) != ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_parsing.py::TestCheckPayloadSchema -v`
Expected: FAIL with `ImportError: cannot import name 'check_payload_schema'`.

- [ ] **Step 3: Implement `check_payload_schema`**

Append to `consensus/methods/parsing.py`:

```python
#: JSON-Schema primitive types this checker validates natively.
_JSON_PRIMITIVES = ("string", "number", "integer", "boolean")


def check_payload_schema(payload: dict, schema: dict) -> str:
    """Structurally validate ``payload`` against a JSON-Schema ``parameters``.

    A library-free gate run *before* a handler's semantic ``validate_output``
    so handlers never ``KeyError`` on a missing field and the user gets a
    precise message.  Checks required keys, primitive types, ``enum``,
    numeric ``minimum``/``maximum``, and recurses one level into ``array``
    items and nested ``object`` ``properties``/``additionalProperties``.

    Returns ``""`` when acceptable, else a human-readable error.
    """
    if not isinstance(payload, dict):
        return "Input must be a set of fields."
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in payload:
            return f"Missing required field: '{key}'."
    for key, value in payload.items():
        if key in props:
            err = _check_value(value, props[key], key)
            if err:
                return err
    return ""


def _check_value(value: Any, prop: dict, key: str) -> str:
    """Validate one value against its property subschema (see caller)."""
    enum = prop.get("enum")
    if enum is not None and value not in enum:
        return f"'{key}' must be one of {enum}."
    ptype = prop.get("type")
    if ptype in ("number", "integer"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"'{key}' must be a number."
        if "minimum" in prop and value < prop["minimum"]:
            return f"'{key}' must be >= {prop['minimum']}."
        if "maximum" in prop and value > prop["maximum"]:
            return f"'{key}' must be <= {prop['maximum']}."
    elif ptype == "string":
        if not isinstance(value, str):
            return f"'{key}' must be text."
    elif ptype == "boolean":
        if not isinstance(value, bool):
            return f"'{key}' must be true or false."
    elif ptype == "array":
        if not isinstance(value, list):
            return f"'{key}' must be a list."
        items = prop.get("items", {})
        for item in value:
            err = _check_value(item, items, key) if items.get("type") in (
                *_JSON_PRIMITIVES, "array") else _check_object(item, items, key)
            if err:
                return err
    elif ptype == "object":
        return _check_object(value, prop, key)
    return ""


def _check_object(value: Any, prop: dict, key: str) -> str:
    """Validate a nested object's properties / additionalProperties values."""
    if not isinstance(value, dict):
        return f"'{key}' must be a set of fields."
    sub_props = prop.get("properties")
    if isinstance(sub_props, dict) and sub_props:
        for req in prop.get("required", []):
            if req not in value:
                return f"'{key}' is missing '{req}'."
        for k, v in value.items():
            if k in sub_props:
                err = _check_value(v, sub_props[k], f"{key}.{k}")
                if err:
                    return err
        return ""
    add = prop.get("additionalProperties")
    if isinstance(add, dict):
        for k, v in value.items():
            err = _check_value(v, add, f"{key}.{k}")
            if err:
                return err
    return ""
```

(`Any` is already imported at the top of `parsing.py`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_parsing.py::TestCheckPayloadSchema -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/parsing.py tests/test_parsing.py
git commit -m "feat(parsing): add check_payload_schema structural pre-check (#57)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `expand_belief_schema` dynamic-key expansion

**Files:**
- Modify: `consensus/methods/phases/_belief_helpers.py` (add function + `import copy`)
- Test: `tests/test_belief_helpers.py` (add `TestExpandBeliefSchema`; create if absent)

**Interfaces:**
- Consumes: `BELIEFS_TOOL_PARAMETERS`, `hypothesis_labels`, `BELIEF_MIN`, `BELIEF_MAX` (same module).
- Produces: `expand_belief_schema(hypotheses: list[str]) -> dict` — a copy of `BELIEFS_TOOL_PARAMETERS` whose `beliefs` property is expanded from `additionalProperties: {number}` into explicit per-label number `properties` (`H1..Hn`) with `required` = those labels.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_belief_helpers.py
from consensus.methods.phases._belief_helpers import expand_belief_schema


class TestExpandBeliefSchema:
    def test_expands_to_concrete_labels(self):
        schema = expand_belief_schema(["A", "B", "C"])
        beliefs = schema["properties"]["beliefs"]
        assert set(beliefs["properties"]) == {"H1", "H2", "H3"}
        assert beliefs["required"] == ["H1", "H2", "H3"]
        assert "additionalProperties" not in beliefs
        assert beliefs["properties"]["H1"]["type"] == "number"
        assert beliefs["properties"]["H1"]["maximum"] == 1.0

    def test_empty_hypotheses_yields_no_labels(self):
        beliefs = expand_belief_schema([])["properties"]["beliefs"]
        assert beliefs["properties"] == {}

    def test_does_not_mutate_the_template(self):
        from consensus.methods.phases._belief_helpers import (
            BELIEFS_TOOL_PARAMETERS)
        expand_belief_schema(["A"])
        assert "additionalProperties" in (
            BELIEFS_TOOL_PARAMETERS["properties"]["beliefs"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_belief_helpers.py::TestExpandBeliefSchema -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement `expand_belief_schema`**

Add `import copy` near the top of `_belief_helpers.py`, then add:

```python
def expand_belief_schema(hypotheses: list[str]) -> dict:
    """Return BELIEFS_TOOL_PARAMETERS with dynamic keys made concrete.

    The ``beliefs`` map uses ``additionalProperties`` (keys derived at
    runtime from the framed hypotheses), which a form renderer cannot
    enumerate.  Replace it with explicit per-label number properties
    (``H1``..``Hn``) so ``build_input_spec`` reports the schema as
    renderable and the frontend can lay out one field per hypothesis.
    The template itself is never mutated.
    """
    labels = hypothesis_labels(hypotheses)
    schema = copy.deepcopy(BELIEFS_TOOL_PARAMETERS)
    schema["properties"]["beliefs"] = {
        "type": "object",
        "description": BELIEFS_TOOL_PARAMETERS[
            "properties"]["beliefs"]["description"],
        "properties": {
            label: {"type": "number", "minimum": BELIEF_MIN,
                    "maximum": BELIEF_MAX,
                    "description": f"Probability for {label}."}
            for label in labels
        },
        "required": labels,
    }
    return schema
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_belief_helpers.py::TestExpandBeliefSchema -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phases/_belief_helpers.py tests/test_belief_helpers.py
git commit -m "feat(beliefs): add expand_belief_schema dynamic-key expansion (#57)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `resolve_input_schema` hook + belief overrides

**Files:**
- Modify: `consensus/methods/phase_handler.py` (default hook)
- Modify: `consensus/methods/base.py` (DiscussionMethod delegating wrapper, next to `get_output_tool` ~line 419)
- Modify: `consensus/methods/phases/diffuse_beliefs.py`, `consensus/methods/phases/prior_beliefs.py` (overrides)
- Test: `tests/test_resolve_input_schema.py` (new)

**Interfaces:**
- Consumes: `OutputToolSpec`, `expand_belief_schema` (Task 2).
- Produces:
  - `PhaseHandler.resolve_input_schema(self, spec: OutputToolSpec, entity, discussion) -> dict` — default returns `spec.parameters`.
  - `DiscussionMethod.resolve_input_schema(self, spec, entity, discussion) -> dict` — delegates to the current phase's handler (same pattern as `get_output_tool`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resolve_input_schema.py
from consensus.methods import get_method
from tests.flow_e2e_helpers import start_method_discussion


def test_default_hook_returns_spec_parameters():
    from consensus.methods.phase_handler import PhaseHandler
    from consensus.methods.base import OutputToolSpec

    class Dummy(PhaseHandler):
        phase = None
        def get_system_prompt(self, e, d): return ""
        def get_turn_prompt(self, e, d): return ""

    spec = OutputToolSpec(name="t", description="d",
                          parameters={"type": "object", "properties": {}})
    assert Dummy().resolve_input_schema(spec, None, None) == spec.parameters


def test_belief_method_expands_dynamic_keys(tmp_db):
    disc, moderator, pricing, mod, parts = start_method_discussion(
        tmp_db, "belief_diffusion", n_participants=2,
        topic="Is remote work more productive?")
    disc.method_state["hypotheses"] = ["Yes", "No"]
    disc.method_state["current_phase"] = "prior"
    method = get_method("belief_diffusion")
    entity = parts[0]
    spec = method.get_output_tool(entity, disc)
    schema = method.resolve_input_schema(spec, entity, disc)
    assert set(schema["properties"]["beliefs"]["properties"]) == {"H1", "H2"}
```

(Match the real Belief Diffusion method registry name and initial phase name; read `consensus/methods/belief_diffusion.py` for the exact `name` and first structured phase before writing the assertion.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_resolve_input_schema.py -v`
Expected: FAIL with `AttributeError: 'PhaseHandler' object has no attribute 'resolve_input_schema'`.

- [ ] **Step 3: Implement the hook, delegator, and overrides**

In `consensus/methods/phase_handler.py`, in the "Structured output" section (after `get_output_tool`):

```python
def resolve_input_schema(self, spec: OutputToolSpec, entity: Entity,
                         discussion: Discussion) -> dict:
    """Return the JSON schema to render a human input form / pre-check.

    Default: the tool's declared ``parameters`` unchanged.  Handlers
    whose schema uses runtime-derived keys (e.g. a belief map keyed by
    hypothesis label) override this to expand ``additionalProperties``
    into explicit properties so the frontend can enumerate fields.
    """
    return spec.parameters
```

In `consensus/methods/base.py`, next to the `get_output_tool` delegator, add the delegating wrapper (follow the exact lookup pattern used there — resolve the current phase's handler and call through, falling back to `spec.parameters`):

```python
def resolve_input_schema(self, spec: "OutputToolSpec", entity: "Entity",
                         discussion: "Discussion") -> dict:
    """Delegate schema resolution to the current phase's handler."""
    handler = self._handler_for_phase(
        discussion.method_state.get("current_phase", ""))
    if handler is not None:
        return handler.resolve_input_schema(spec, entity, discussion)
    return spec.parameters
```

In `consensus/methods/phases/diffuse_beliefs.py` add the import and override:

```python
from ._belief_helpers import (  # add to the existing import block
    expand_belief_schema,
)

    def resolve_input_schema(self, spec: OutputToolSpec, entity: Entity,
                             discussion: Discussion) -> dict:
        """Expand the belief map to explicit per-hypothesis number fields."""
        hypotheses = discussion.method_state.get("hypotheses", [])
        if not hypotheses:
            return spec.parameters
        return expand_belief_schema(hypotheses)
```

Add the identical override (and import) to `consensus/methods/phases/prior_beliefs.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_resolve_input_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add consensus/methods/phase_handler.py consensus/methods/base.py \
    consensus/methods/phases/diffuse_beliefs.py \
    consensus/methods/phases/prior_beliefs.py tests/test_resolve_input_schema.py
git commit -m "feat(methods): add resolve_input_schema hook + belief overrides (#57)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `structured_input.py` — renderability + spec builder

**Files:**
- Create: `consensus/structured_input.py`
- Test: `tests/test_structured_input.py` (new)

**Interfaces:**
- Consumes: `DiscussionMethod.get_output_tool`, `DiscussionMethod.resolve_input_schema` (Task 3).
- Produces:
  - `schema_is_renderable(schema: dict) -> bool` — True when every top-level property is a supported construct (primitive, `enum`, `boolean`, array of primitives/enums, one-level array-of-objects with supported leaves, resolved object with `properties`); False on unresolved `additionalProperties` or deeper nesting.
  - `build_input_spec(method, entity, discussion) -> dict | None` — `{"tool_name", "description", "schema", "renderable"}` or `None` when the phase yields no output tool.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_structured_input.py
from consensus.structured_input import schema_is_renderable, build_input_spec

POLL = {"type": "object", "properties": {
    "belief": {"type": "number", "minimum": 0, "maximum": 1},
    "reasoning": {"type": "string"}}, "required": ["belief", "reasoning"]}

ENUM = {"type": "object", "properties": {
    "stance": {"type": "string", "enum": ["updated", "unchanged"]}}}

ARRAY_OF_OBJ = {"type": "object", "properties": {
    "cruxes": {"type": "array", "items": {"type": "object", "properties": {
        "claim": {"type": "string"}, "belief": {"type": "number"}}}}}}

RESOLVED_BELIEFS = {"type": "object", "properties": {
    "beliefs": {"type": "object", "properties": {
        "H1": {"type": "number"}, "H2": {"type": "number"}}}}}

MATRIX = {"type": "object", "properties": {
    "ratings": {"type": "object", "additionalProperties": {
        "type": "object", "additionalProperties": {"type": "string"}}}}}


class TestRenderable:
    def test_primitive_form_is_renderable(self):
        assert schema_is_renderable(POLL) is True

    def test_enum_is_renderable(self):
        assert schema_is_renderable(ENUM) is True

    def test_array_of_objects_is_renderable(self):
        assert schema_is_renderable(ARRAY_OF_OBJ) is True

    def test_resolved_beliefs_are_renderable(self):
        assert schema_is_renderable(RESOLVED_BELIEFS) is True

    def test_nested_additionalproperties_not_renderable(self):
        assert schema_is_renderable(MATRIX) is False

    def test_empty_schema_not_renderable(self):
        assert schema_is_renderable({"type": "object", "properties": {}}) is False


class TestBuildInputSpec:
    def test_returns_none_without_method(self):
        assert build_input_spec(None, None, None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_structured_input.py -v`
Expected: FAIL with `ModuleNotFoundError: consensus.structured_input`.

- [ ] **Step 3: Implement `structured_input.py`**

```python
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
        itype = items.get("type")
        if itype in _RENDERABLE_PRIMITIVES:
            return True
        if itype == "object":
            sub = items.get("properties", {})
            return bool(sub) and all(_prop_renderable(p) for p in sub.values())
        return False
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_structured_input.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add consensus/structured_input.py tests/test_structured_input.py
git commit -m "feat: add structured_input helpers (renderable + build_input_spec) (#57)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Expose `current_input_spec` in `get_state`

**Files:**
- Modify: `consensus/app.py` (`get_state`, in the block that already computes `track_evidence_phase` ~lines 489-496)
- Test: `tests/test_structured_input_state.py` (new)

**Interfaces:**
- Consumes: `build_input_spec` (Task 4), `EntityType` (already imported in `app.py`? if not, import from `.models`).
- Produces: `state["current_input_spec"]` — the dict from `build_input_spec` when the current speaker is a human participant (not the moderator) in a structured phase, else `None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_structured_input_state.py
import pytest
from tests.flow_e2e_helpers import start_method_discussion


@pytest.mark.asyncio
async def test_current_input_spec_present_for_human_poll(tmp_db, app_for):
    # app_for: a fixture / helper that wraps a Discussion in a ConsensusApp.
    # Drive a Double Crux discussion (all-human) to the poll_belief phase,
    # make the current speaker a human participant, then assert:
    app = app_for(...)
    state = app.get_state()
    spec = state["current_input_spec"]
    assert spec is not None
    assert spec["tool_name"] == "submit_crux_belief"
    assert spec["renderable"] is True
    assert "belief" in spec["schema"]["properties"]


def test_current_input_spec_none_in_freetext_phase(app_for):
    app = app_for(...)  # a phase with no output tool (e.g. Open Discussion)
    assert app.get_state()["current_input_spec"] is None
```

(Use the existing test scaffolding for building a `ConsensusApp` around a `Discussion` — grep `tests/` for how `get_state` is exercised elsewhere, e.g. `tests/test_app*` — and reuse that fixture instead of inventing `app_for`. The assertions are the contract; the setup should match the repo's existing app-construction helper.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_structured_input_state.py -v`
Expected: FAIL with `KeyError: 'current_input_spec'`.

- [ ] **Step 3: Implement in `get_state`**

In `consensus/app.py`, right after the `track_evidence_phase` block (which already has `method` and `phase` in scope), add:

```python
        # Structured-phase input spec (#57): when the current speaker is a
        # human participant (not the moderator) in a phase that declares an
        # output tool, expose the tool schema so the frontend renders a form
        # instead of forcing raw JSON into the plain textarea.
        from .structured_input import build_input_spec
        from .models import EntityType
        current = self.discussion.current_speaker
        input_spec = None
        if (current is not None
                and current.entity_type == EntityType.HUMAN
                and current.id != self.discussion.moderator_id):
            input_spec = build_input_spec(method, current, self.discussion)
        state["current_input_spec"] = input_spec
```

(If `EntityType` is already imported at module top, drop the local import.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_structured_input_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add consensus/app.py tests/test_structured_input_state.py
git commit -m "feat(app): expose current_input_spec in get_state for human turns (#57)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Backend recording path + safety net + app wrapper

**Files:**
- Modify: `consensus/app_discussion_flow.py` (new `_record_structured_human_turn`, `submit_human_structured_message`; safety-net guard in `submit_human_message`; import `extract_json_block` is not needed — the guard uses a `method_state` no-op check)
- Modify: `consensus/app.py` (`submit_human_structured_message` wrapper next to `submit_human_message` ~line 790)
- Test: `tests/test_structured_human_turn.py` (new)

**Interfaces:**
- Consumes: `check_payload_schema` (Task 1), `serialize_method_state`, `get_active_method`, `Message`, `MessageRole` (already imported in the module).
- Produces:
  - `submit_human_structured_message(discussion, db, entity_id: int, payload: dict) -> dict` — validates turn ownership + structured phase, records via the shared helper; returns the message dict or `{"error": ...}`.
  - `_record_structured_human_turn(discussion, db, entity, method, spec, payload) -> dict` — pre-check → `validate_output` → `process_structured_response` → append + persist.
  - `ConsensusApp.submit_human_structured_message(self, entity_id, payload) -> dict`.
  - `submit_human_message` now returns `{"error": ...}` (instead of silently dropping) when a structured-phase free-text turn records nothing.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_structured_human_turn.py
import pytest
from consensus.app_discussion_flow import (
    submit_human_structured_message, submit_human_message)
from tests.flow_e2e_helpers import start_method_discussion
# Reuse the double-crux scaffolding that drives the phase to poll_belief.
from tests.test_method_flow_e2e import drive_double_crux_to_poll  # add if absent


class TestStructuredHumanTurn:
    def test_valid_payload_records_belief(self, tmp_db):
        disc, db, entity = drive_double_crux_to_poll(tmp_db)
        res = submit_human_structured_message(
            disc, db, entity.id, {"belief": 0.7, "reasoning": "study"})
        assert "error" not in res
        polls = disc.method_state["poll_beliefs"]
        assert any(p["entity_id"] == entity.id and p["belief"] == 0.7
                   for p in polls)

    def test_invalid_payload_returns_error_and_records_nothing(self, tmp_db):
        disc, db, entity = drive_double_crux_to_poll(tmp_db)
        before = list(disc.method_state["poll_beliefs"])
        res = submit_human_structured_message(
            disc, db, entity.id, {"belief": 5, "reasoning": "x"})
        assert "error" in res
        assert disc.method_state["poll_beliefs"] == before

    def test_freetext_prose_in_structured_phase_errors_not_silent(self, tmp_db):
        disc, db, entity = drive_double_crux_to_poll(tmp_db)
        res = submit_human_message(disc, db, entity.id, "I'm about 70% sure")
        assert "error" in res  # golden rule 6: visible, not a silent drop

    def test_freetext_json_block_still_records(self, tmp_db):
        disc, db, entity = drive_double_crux_to_poll(tmp_db)
        res = submit_human_message(
            disc, db, entity.id,
            '```json\n{"belief": 0.6, "reasoning": "ok"}\n```')
        assert "error" not in res
        assert any(p["entity_id"] == entity.id
                   for p in disc.method_state["poll_beliefs"])
```

Add a `drive_double_crux_to_poll(tmp_db)` helper (in `tests/flow_e2e_helpers.py` or the test module) that starts an all-human Double Crux discussion and advances it through `positions → hunt_cruxes → identify_crux (factual)` so the current phase is `poll_belief` and the current speaker is a human participant; return `(discussion, db, current_speaker)`. Model it on the existing `TestDoubleCruxFlow` driver in `tests/test_method_flow_e2e.py`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_structured_human_turn.py -v`
Expected: FAIL with `ImportError: cannot import name 'submit_human_structured_message'`.

- [ ] **Step 3: Implement the flow functions + safety net**

In `consensus/app_discussion_flow.py` add (after `submit_human_message`):

```python
def _record_structured_human_turn(
    discussion: Discussion, db: Database, entity: Entity,
    method, spec, payload: dict,
) -> dict:
    """Validate and record a human structured-turn payload.

    Mirrors the AI forced-tool branch (``generate_ai_turn``): a structural
    pre-check then the handler's semantic ``validate_output``, then
    ``process_structured_response`` writes into ``method_state``.  Returns
    the message dict, or ``{"error": ...}`` (recording nothing) on failure.
    """
    schema = method.resolve_input_schema(spec, entity, discussion)
    error = (check_payload_schema(payload, schema)
             or method.validate_output(payload, entity, discussion))
    if error:
        logger.warning("Rejected structured turn from %s: %s",
                       entity.name, error)
        return {"error": error}

    processed = method.process_structured_response(payload, entity, discussion)
    content = processed.display_content
    if discussion.id:
        db.update_discussion(
            discussion.id,
            method_state=serialize_method_state(discussion.method_state),
        )
    msg = Message(
        entity_id=entity.id, entity_name=entity.name,
        content=content, role=MessageRole.PARTICIPANT,
    )
    discussion.messages.append(msg)
    db.add_message(
        discussion.id, entity.id, content, "participant",
        turn_number=discussion.turn_number,
    )
    return msg.to_dict()


def submit_human_structured_message(
    discussion: Discussion, db: Database, entity_id: int, payload: dict,
) -> dict:
    """Submit a validated structured payload from a human participant (#57).

    The frontend form (or guided-JSON fallback) posts a typed ``payload``;
    it is validated and recorded on the same path an AI's forced tool call
    uses.  Returns the message dict or an error dict.
    """
    entity = discussion.get_entity(entity_id)
    if not entity:
        return {"error": "Entity not found"}
    current = discussion.current_speaker
    if not current or current.id != entity_id:
        return {"error": f"It's not {entity.name}'s turn"}
    method = get_active_method(discussion)
    if not method:
        return {"error": "No active discussion method"}
    spec = method.get_output_tool(entity, discussion)
    if spec is None:
        return {"error": "This phase does not take structured input."}
    return _record_structured_human_turn(
        discussion, db, entity, method, spec, payload)
```

Then modify the method-processing block inside `submit_human_message` (currently lines 123-136) to add the no-op safety net:

```python
    method = get_active_method(discussion)
    if method and not is_pass(content):
        spec = method.get_output_tool(entity, discussion)
        before = (serialize_method_state(discussion.method_state)
                  if spec is not None else None)
        processed = method.process_response(content, entity, discussion)
        content = processed.display_content
        if spec is not None and serialize_method_state(
                discussion.method_state) == before:
            # Structured phase, but free-text extraction recorded nothing —
            # surface it (golden rule 6) instead of the old silent drop.
            logger.warning(
                "Structured phase: could not read %s's free-text turn as "
                "'%s' data.", entity.name, spec.name)
            return {"error": (
                f"This phase needs structured input. Your message could not "
                f"be read as '{spec.name}' data — please use the input form.")}
        phase = method.current_phase(discussion)
        if phase is not None and phase.track_evidence:
            content = record_and_annotate_evidence(
                discussion, entity, discussion.turn_number, content,
                tool_calls=[])
        if discussion.id:
            db.update_discussion(
                discussion.id,
                method_state=serialize_method_state(discussion.method_state),
            )
```

In `consensus/app.py`, after the `submit_human_message` wrapper (~line 796):

```python
    def submit_human_structured_message(
            self, entity_id: int, payload: dict) -> dict:
        """Submit a validated structured payload from a human participant."""
        result = app_discussion_flow.submit_human_structured_message(
            self.discussion, self.db, entity_id, payload,
        )
        self._notify()
        return result
```

- [ ] **Step 4: Run the new tests + the full method-flow suite**

Run: `uv run pytest tests/test_structured_human_turn.py tests/test_method_flow_e2e.py -v`
Expected: PASS — new tests pass and the **existing** Double Crux fenced-JSON E2E still passes (the no-op guard does not fire when extraction succeeds).

- [ ] **Step 5: Commit**

```bash
git add consensus/app_discussion_flow.py consensus/app.py \
    tests/test_structured_human_turn.py tests/flow_e2e_helpers.py
git commit -m "feat: record human structured turns; error (not drop) on unparsed prose (#57)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Expose the endpoint (desktop bridge, server route, api.js)

**Files:**
- Modify: `consensus/desktop.py` (bridge method next to `submit_human_message` ~line 225)
- Modify: `consensus/server.py` (route in the dispatch dict ~line 397)
- Modify: `consensus/static/api.js` (both the pywebview layer ~line 62 and the `_post` layer ~line 201)
- Test: `tests/test_server_routes.py` (add a case if the file exists; otherwise assert via the app method already covered in Task 6 and rely on the Task 9 manual run)

**Interfaces:**
- Consumes: `ConsensusApp.submit_human_structured_message` (Task 6).
- Produces: `api.submitStructuredMessage(entityId, payload)` in the frontend.

- [ ] **Step 1: Add the desktop bridge method**

In `consensus/desktop.py` after `submit_human_message`:

```python
    def submit_human_structured_message(
            self, entity_id: int, payload: dict) -> dict:
        """Submit a structured payload from a human participant (#57)."""
        return self.app.submit_human_structured_message(entity_id, payload)
```

- [ ] **Step 2: Add the server route**

In `consensus/server.py`, in the handler dispatch dict after `submit_human_message`:

```python
            "submit_human_structured_message":
                lambda: app.submit_human_structured_message(
                    data["entity_id"], data.get("payload", {})),
```

- [ ] **Step 3: Add the api.js methods**

In `consensus/static/api.js`, pywebview layer (near line 62):

```javascript
    async submitStructuredMessage(eid, payload) { return await window.pywebview.api.submit_human_structured_message(eid, payload); }
```

`_post` layer (near line 201):

```javascript
    async submitStructuredMessage(eid, payload) { return await this._post('submit_human_structured_message', { entity_id: eid, payload }); }
```

- [ ] **Step 4: Verify wiring**

Run: `uv run pytest tests/ -k "server and route" -v` (if such tests exist; otherwise skip).
Then a Python import smoke check:
Run: `uv run python -c "import consensus.desktop, consensus.server, consensus.app"`
Expected: no ImportError.

- [ ] **Step 5: Commit**

```bash
git add consensus/desktop.py consensus/server.py consensus/static/api.js
git commit -m "feat: expose submit_human_structured_message (bridge, route, api) (#57)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Frontend generic form renderer

**Files:**
- Create: `consensus/static/structured-form.js`
- Modify: `consensus/static/discussion.js` (`updateInputArea` ~lines 247-297: render the form when `state.current_input_spec` is set on a human turn)
- Modify: `consensus/static/discussion-actions.js` (import + a `onSubmitStructured` handler; skip button)
- Modify: `consensus/static/styles.css` (or the discussion stylesheet) — form styling with relative units
- Test: none automated (no JS harness); verified in Task 9's manual run.

**Interfaces:**
- Consumes: `state.current_input_spec` (Task 5), `api.submitStructuredMessage` (Task 7), the existing `onStateUpdate` / `completeTurnFlow` / `processCurrentTurn` / `getEntity` / `showToast` used by `onSendMessage`.
- Produces: `renderStructuredForm(spec, { onSubmit, onSkip }) -> HTMLElement`, `collectStructuredPayload(formEl, spec) -> {payload, error}`.

- [ ] **Step 1: Create `structured-form.js`**

```javascript
// consensus/static/structured-form.js
// Generic schema-driven input form for human turns in structured phases
// (issue #57). Renders one widget per JSON-schema property; falls back to a
// guided-JSON textarea when spec.renderable is false. No external deps.

/** Build a labelled wrapper for one field. */
function fieldRow(labelText, help, inputEl) {
    const row = document.createElement('div');
    row.className = 'sf-row';
    const label = document.createElement('label');
    label.className = 'sf-label';
    label.textContent = labelText;
    if (help) label.title = help;
    row.appendChild(label);
    if (help) {
        const h = document.createElement('div');
        h.className = 'sf-help';
        h.textContent = help;
        row.appendChild(h);
    }
    row.appendChild(inputEl);
    return row;
}

/** Create a single primitive/enum widget from a property subschema. */
function widgetFor(key, prop) {
    if (Array.isArray(prop.enum)) {
        const sel = document.createElement('select');
        sel.dataset.key = key;
        for (const opt of prop.enum) {
            const o = document.createElement('option');
            o.value = opt; o.textContent = opt;
            sel.appendChild(o);
        }
        return sel;
    }
    if (prop.type === 'number' || prop.type === 'integer') {
        const inp = document.createElement('input');
        inp.type = 'number';
        inp.dataset.key = key;
        inp.dataset.jsontype = prop.type;
        if (prop.minimum !== undefined) inp.min = prop.minimum;
        if (prop.maximum !== undefined) inp.max = prop.maximum;
        if (prop.type === 'number') inp.step = 'any';
        return inp;
    }
    if (prop.type === 'boolean') {
        const inp = document.createElement('input');
        inp.type = 'checkbox';
        inp.dataset.key = key;
        inp.dataset.jsontype = 'boolean';
        return inp;
    }
    // string (default), long fields become textareas
    const long = key === 'reasoning' || key === 'position' || key === 'claim';
    const inp = document.createElement(long ? 'textarea' : 'input');
    if (!long) inp.type = 'text';
    inp.dataset.key = key;
    inp.dataset.jsontype = 'string';
    return inp;
}

/** Render the full form element. onSubmit(payload), onSkip() are callbacks. */
export function renderStructuredForm(spec, { onSubmit, onSkip }) {
    const form = document.createElement('div');
    form.className = 'structured-form';

    const title = document.createElement('div');
    title.className = 'sf-title';
    title.textContent = spec.description || spec.tool_name;
    form.appendChild(title);

    const body = document.createElement('div');
    body.className = 'sf-body';
    form.appendChild(body);

    const errEl = document.createElement('div');
    errEl.className = 'sf-error';
    errEl.setAttribute('role', 'alert');
    form.appendChild(errEl);

    let collect;
    if (spec.renderable) {
        collect = renderFields(body, spec.schema);
    } else {
        collect = renderGuidedJson(body, spec.schema);
    }

    const bar = document.createElement('div');
    bar.className = 'sf-actions';
    const submit = document.createElement('button');
    submit.className = 'sf-submit';
    submit.textContent = 'Submit';
    submit.addEventListener('click', () => {
        errEl.textContent = '';
        const { payload, error } = collect();
        if (error) { errEl.textContent = error; return; }
        onSubmit(payload, errEl);
    });
    const skip = document.createElement('button');
    skip.className = 'sf-skip';
    skip.textContent = 'Skip turn';
    skip.addEventListener('click', () => onSkip());
    bar.appendChild(submit);
    bar.appendChild(skip);
    form.appendChild(bar);
    return form;
}

/** Renderable path: one widget per top-level property. Returns a collector. */
function renderFields(body, schema) {
    const props = schema.properties || {};
    const required = schema.required || [];
    const collectors = [];
    for (const [key, prop] of Object.entries(props)) {
        if (prop.type === 'array') {
            collectors.push(renderArray(body, key, prop));
        } else if (prop.type === 'object' && prop.properties) {
            collectors.push(renderObject(body, key, prop));
        } else {
            const w = widgetFor(key, prop);
            body.appendChild(fieldRow(labelFor(key, required), prop.description, w));
            collectors.push(() => readWidget(w));
        }
    }
    return () => assemble(collectors, required);
}

function labelFor(key, required) {
    return required.includes(key) ? `${key} *` : key;
}

/** Read a primitive widget into [key, value] or throw a message. */
function readWidget(w) {
    const key = w.dataset.key;
    if (w.tagName === 'SELECT') return [key, w.value];
    if (w.dataset.jsontype === 'boolean') return [key, w.checked];
    const raw = w.value.trim();
    if (raw === '') return [key, undefined];
    if (w.dataset.jsontype === 'number') return [key, Number(raw)];
    if (w.dataset.jsontype === 'integer') return [key, parseInt(raw, 10)];
    return [key, raw];
}

function assemble(collectors, required) {
    const payload = {};
    for (const c of collectors) {
        const entry = c();
        if (entry && entry.error) return { error: entry.error };
        const [k, v] = entry;
        if (v !== undefined) payload[k] = v;
    }
    for (const r of required) {
        if (!(r in payload)) return { error: `Please fill in '${r}'.` };
    }
    return { payload };
}

/** Array of primitives or one-level objects. Returns a collector -> [key,val]. */
function renderArray(body, key, prop) {
    const wrap = document.createElement('div');
    wrap.className = 'sf-array';
    const rows = [];
    const items = prop.items || {};
    const addRow = () => {
        const row = document.createElement('div');
        row.className = 'sf-array-row';
        const inputs = [];
        if (items.type === 'object' && items.properties) {
            for (const [ik, ip] of Object.entries(items.properties)) {
                const w = widgetFor(ik, ip);
                w.placeholder = ik;
                row.appendChild(w);
                inputs.push(w);
            }
        } else {
            const w = widgetFor(key, items);
            row.appendChild(w);
            inputs.push(w);
        }
        const del = document.createElement('button');
        del.textContent = '×';
        del.className = 'sf-del';
        del.addEventListener('click', () => { wrap.removeChild(row);
            rows.splice(rows.indexOf(entry), 1); });
        row.appendChild(del);
        const entry = { row, inputs, single: items.type !== 'object' };
        rows.push(entry);
        wrap.insertBefore(row, addBtn);
    };
    const addBtn = document.createElement('button');
    addBtn.textContent = `+ add ${key}`;
    addBtn.className = 'sf-add';
    addBtn.addEventListener('click', addRow);
    body.appendChild(fieldRow(key, prop.description, wrap));
    wrap.appendChild(addBtn);
    addRow();
    return () => {
        const arr = [];
        for (const e of rows) {
            if (e.single) {
                const [, v] = readWidget(e.inputs[0]);
                if (v !== undefined) arr.push(v);
            } else {
                const obj = {};
                for (const w of e.inputs) {
                    const [k, v] = readWidget(w);
                    if (v !== undefined) obj[k] = v;
                }
                if (Object.keys(obj).length) arr.push(obj);
            }
        }
        return [key, arr.length ? arr : undefined];
    };
}

/** Resolved nested object (e.g. expanded belief map). collector -> [key,val]. */
function renderObject(body, key, prop) {
    const wrap = document.createElement('div');
    wrap.className = 'sf-object';
    const widgets = [];
    for (const [ik, ip] of Object.entries(prop.properties)) {
        const w = widgetFor(ik, ip);
        wrap.appendChild(fieldRow(ik, ip.description, w));
        widgets.push(w);
    }
    body.appendChild(fieldRow(key, prop.description, wrap));
    return () => {
        const obj = {};
        for (const w of widgets) {
            const [k, v] = readWidget(w);
            if (v !== undefined) obj[k] = v;
        }
        return [key, obj];
    };
}

/** Guided-JSON fallback: schema shown + a JSON textarea. */
function renderGuidedJson(body, schema) {
    const note = document.createElement('div');
    note.className = 'sf-help';
    note.textContent = 'Enter your response as JSON matching this schema:';
    const pre = document.createElement('pre');
    pre.className = 'sf-schema';
    pre.textContent = JSON.stringify(schema, null, 2);
    const ta = document.createElement('textarea');
    ta.className = 'sf-json';
    ta.value = skeletonFor(schema);
    body.appendChild(note);
    body.appendChild(pre);
    body.appendChild(ta);
    return () => {
        try {
            return { payload: JSON.parse(ta.value) };
        } catch (e) {
            return { error: 'Invalid JSON: ' + e.message };
        }
    };
}

/** Minimal JSON skeleton derived from a schema's top-level properties. */
function skeletonFor(schema) {
    const obj = {};
    for (const [k, p] of Object.entries(schema.properties || {})) {
        obj[k] = p.type === 'object' ? {} : p.type === 'array' ? [] :
            p.type === 'number' || p.type === 'integer' ? 0 : '';
    }
    return JSON.stringify(obj, null, 2);
}
```

- [ ] **Step 2: Wire it into `updateInputArea`**

In `consensus/static/discussion.js`, in the human-speaker branch of `updateInputArea`, when `state.current_input_spec` is present, hide the plain textarea/send button and mount the form:

```javascript
// inside updateInputArea, human-speaker branch:
import { renderStructuredForm } from './structured-form.js';
import { onSubmitStructured, onSkipStructured } from './discussion-actions.js';

const spec = state.current_input_spec;
const host = $('#message-input-area');   // the composer container
if (spec) {
    host.querySelector('.structured-form')?.remove();
    const form = renderStructuredForm(spec, {
        onSubmit: (payload, errEl) => onSubmitStructured(payload, errEl),
        onSkip: () => onSkipStructured(),
    });
    $('#message-input').style.display = 'none';
    $('#send-btn').style.display = 'none';
    host.appendChild(form);
} else {
    host.querySelector('.structured-form')?.remove();
    $('#message-input').style.display = '';
    $('#send-btn').style.display = '';
}
```

(Match the real composer container id/classes in `index.html`; grep for `message-input` to find the wrapper element to append into.)

- [ ] **Step 3: Add the action handlers**

In `consensus/static/discussion-actions.js`:

```javascript
/** Submit a structured payload from the form; show errors inline. */
export async function onSubmitStructured(payload, errEl) {
    if (!state.current_speaker_id) return;
    try {
        const result = await api.submitStructuredMessage(
            state.current_speaker_id, payload);
        if (result?.error) { if (errEl) errEl.textContent = result.error; return; }
        const s = await api.getState();
        onStateUpdate(s);
        const completed = await completeTurnFlow();
        if (completed) processCurrentTurn();
    } catch (e) {
        if (errEl) errEl.textContent = 'Failed to submit: ' + e.message;
    }
}

/** Skip a structured turn (submit a pass through the normal path). */
export async function onSkipStructured() {
    if (!state.current_speaker_id) return;
    const result = await api.submitMessage(state.current_speaker_id, '[PASS]');
    if (result?.error) return showToast(result.error);
    const s = await api.getState();
    onStateUpdate(s);
    const completed = await completeTurnFlow();
    if (completed) processCurrentTurn();
}
```

- [ ] **Step 4: Style the form (relative units only)**

Add to the discussion stylesheet (use existing CSS custom properties for colours; `rem`/`%`/`vw` sizing, no fixed px — per golden rule 9 and the project CSS convention):

```css
.structured-form { display: flex; flex-direction: column; gap: 0.5rem; width: 100%; }
.sf-title { font-weight: 600; }
.sf-row { display: flex; flex-direction: column; gap: 0.25rem; }
.sf-help { font-size: 0.85rem; opacity: 0.75; }
.sf-array-row, .sf-object { display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; }
.sf-error { color: var(--error, #c0392b); min-height: 1.2rem; }
.structured-form input, .structured-form select, .structured-form textarea {
    width: 100%; box-sizing: border-box; }
.sf-actions { display: flex; gap: 0.5rem; }
.sf-schema { max-height: 30vh; overflow: auto; user-select: text; }
```

- [ ] **Step 5: Commit** (manual verification happens in Task 9)

```bash
git add consensus/static/structured-form.js consensus/static/discussion.js \
    consensus/static/discussion-actions.js consensus/static/*.css
git commit -m "feat(ui): schema-driven form for human structured-phase turns (#57)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: E2E flow test + manual verification + docs

**Files:**
- Modify: `tests/test_method_flow_e2e.py` (add a human-belief-poll-via-structured-payload assertion)
- Modify: `HANDOVER.md`, `ROADMAP.md` (record #57 done)
- Manual: run the app, drive a Double Crux belief poll as a human via the form.

**Interfaces:**
- Consumes: `submit_human_structured_message`, `current_input_spec` (Tasks 5-7).

- [ ] **Step 1: Write the E2E assertion**

Add to `tests/test_method_flow_e2e.py` a test that drives an all-human Double Crux to `poll_belief`, then for each disagreeing participant calls `app.submit_human_structured_message(pid, {"belief": <p>, "reasoning": ...})` (via the discussion-flow function), advances, and asserts the belief-shift baseline is populated with real numbers:

```python
@pytest.mark.asyncio
async def test_poll_belief_human_structured_payload(self, tmp_db):
    disc, db, p1, p2 = drive_double_crux_to_poll_two(tmp_db)
    submit_human_structured_message(disc, db, p1.id,
                                    {"belief": 0.8, "reasoning": "prior"})
    advance_or_summarize(disc, db)          # existing E2E helper pattern
    submit_human_structured_message(disc, db, p2.id,
                                    {"belief": 0.3, "reasoning": "prior"})
    advance_to_next_phase(disc, db)
    initial = disc.method_state["shared_crux"]["initial_beliefs"]
    assert initial[str(p1.id)] == 0.8 and initial[str(p2.id)] == 0.3
    assert "?" not in initial.values()      # no fabricated-looking gap
```

(Use the exact `initial_beliefs` key shape the poll writes — read `apply_poll_beliefs` / `record_poll_belief` in `_crux_helpers.py` to confirm keys are entity-id strings vs names, and match the assertion.)

- [ ] **Step 2: Run the full backend suite**

Run: `uv run pytest -q`
Expected: PASS — the prior 2459 tests plus the new ones, zero failures.

- [ ] **Step 3: Manual app verification**

Use the `run` / `verify` skill:
```bash
python -m consensus --web --port 8080 --debug
```
Create an all-human Double Crux discussion, drive it to the belief poll, and confirm: the composer shows a **two-field form** (belief number 0-1 + reasoning), submitting records a real number (no `?`), an out-of-range belief shows an inline error, and "Skip turn" passes. Also spot-check the guided-JSON fallback on an ACH matrix phase. Capture a screenshot for the PR.

- [ ] **Step 4: Update HANDOVER.md and ROADMAP.md**

- HANDOVER: move #57 from "Open work" to "What is done" (add a PR row), update the test count, note the new modules (`structured_input.py`, `structured-form.js`, `resolve_input_schema` hook) and the conventions (form is primary path; `submit_human_message` no-op guard; matrix uses guided-JSON).
- ROADMAP: add a "Structured-phase human input" row under an appropriate theme (e.g. Interactive User Input / Reliability) marked ✅ Done; bump the test count.

- [ ] **Step 5: Commit**

```bash
git add tests/test_method_flow_e2e.py HANDOVER.md ROADMAP.md
git commit -m "test+docs: human structured belief poll E2E; record #57 done

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** A1 → Task 6; A2 → Task 1; A3 safety net → Task 6; A4 skip → Task 8 (`onSkipStructured`); B1 `current_input_spec` + `structured_input.py` → Tasks 4-5; B2 `resolve_input_schema` + `expand_belief_schema` → Tasks 2-3; C renderer + guided-JSON + errors → Task 8; D units + flow + manual → Tasks 1-6, 9. Endpoint exposure (implied by A1) → Task 7. No spec section is unassigned.

**Placeholder scan:** All code steps carry real code. The intentionally-deferred bits are the *test scaffolding lookups* (reuse the repo's existing `ConsensusApp` fixture and Double Crux driver rather than inventing them) and the *composer container id* in `index.html` — each flagged inline with how to resolve. These are "match the existing pattern" instructions, not logic placeholders.

**Type consistency:** `check_payload_schema(payload, schema) -> str` (Task 1) is consumed with that signature in Task 6. `resolve_input_schema(spec, entity, discussion) -> dict` is defined (Task 3) and consumed in Tasks 4 and 6. `build_input_spec(method, entity, discussion) -> dict|None` (Task 4) is consumed in Task 5. `current_input_spec` keys (`tool_name`/`description`/`schema`/`renderable`) match between Task 4 (producer) and Task 8 (consumer). `submitStructuredMessage(eid, payload)` matches between Task 7 and Task 8. `expand_belief_schema(hypotheses) -> dict` matches Tasks 2 and 3.
