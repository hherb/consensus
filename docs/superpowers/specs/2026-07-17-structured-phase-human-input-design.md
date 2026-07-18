# Structured-phase human input — design

_2026-07-17. Closes issue #57: "Human participants must type raw JSON in
structured phases (no input form)." Framework-wide; surfaced during the
PR #56 (Double Crux pre-belief poll) review._

## Problem

Phases that set `requires_structured_output = True` (28 phases across the
methods, e.g. Double Crux `hunt_cruxes` / `identify_crux` / `poll_belief` /
`resolve`, Belief Diffusion `diffuse`, ACH `evaluate_matrix`, Delphi
`estimate`, voting `vote`, …) force **AI** turns through a declared output
tool. `generate_turn` (`moderator.py`) sees `method.get_output_tool(entity,
discussion)` return an `OutputToolSpec` and routes to
`generate_structured_turn`, which forces the tool call, validates via
`validate_output`, and hands the payload to the handler's
`process_structured_response`. The AI turn is then recorded from a validated
payload (`app_discussion_flow.py:250-262`).

**Human** turns never touch that path. `submit_human_message`
(`app_discussion_flow.py:105-147`) unconditionally calls the handler's
free-text `process_response`, which records a contribution only when it can
parse a fenced JSON block (e.g. `extract_poll_belief`, `extract_cruxes`,
`extract_beliefs`). A human who types natural prose ("I'm about 70% sure")
is **silently dropped**: nothing is recorded, they become a straggler, the
phase re-prompts them up to its round cap (e.g. `MAX_POLL_ROUNDS`) with no
explanation, and they end up with a `?` in the outcome (e.g. `initial_beliefs`
/ the belief-shift table). Only a human who happens to type valid JSON is
captured.

The frontend cannot help: the composer is a single `<textarea>`
(`discussion.js` / `discussion-actions.js`), `get_state()` exposes the phase
*name* but neither `requires_structured_output` nor the tool's parameter
schema, and there is **zero** form-rendering infrastructure anywhere in
`consensus/static/` (even `ask_user` is just another free-text bubble).

This violates golden rule 6 ("all caught errors must be shown to the user in
the UI and logged") — the drop is silent — and makes structured methods
effectively unusable for human participants.

## Goal

A human taking a turn in a structured phase gets a real, schema-driven input
**form** (never a raw-JSON demand), their input is validated and recorded
through the **same** path the AI uses, and nothing is ever silently dropped —
any unreadable input produces a visible, actionable error.

Owner decisions (2026-07-17, brainstorming):

1. **Target UX: form + safety net.** Route human turns through the same
   validate → record path; render a real form from the phase's tool schema;
   nested schemas fall back to a guided JSON box with the schema shown.
2. **Slice scope: full generic renderer now.** Build widgets for primitives,
   enums, arrays, and one-level arrays-of-objects, plus dynamic-key number
   maps (belief distributions). Only the truly nested 2-level matrix falls
   back to guided JSON.

## Design

Four parts: a backend structured-submit path (A), state exposure with
dynamic-key resolution (B), a frontend generic form renderer (C), and tests
(D).

### A. Backend — a human structured-submit path

**A1. New app method** `submit_human_structured_message(entity_id: int,
payload: dict) -> dict` in `app_discussion_flow.py`, exposed via the desktop
bridge (`desktop.py`), an aiohttp route (`server.py`), and `api.js`
(`submitStructuredMessage`). It mirrors the AI branch at
`app_discussion_flow.py:250-262`:

- Resolve the entity, active method, and current phase; compute
  `spec = method.get_output_tool(entity, discussion)`.
- If `spec is None` — the phase is not structured for this entity — return
  `{"error": "This phase does not take structured input."}` (defensive; the
  UI never calls this outside a structured phase).
- **Structural pre-check** (A2) → **semantic** `method.validate_output(
  payload, entity, discussion)`. On any error, return `{"error": "<message>
  <short schema hint>"}`, record nothing, do not consume the turn, and log
  the failure (golden rule 6).
- On success, call `method.process_structured_response(payload, entity,
  discussion)`, append the human message with `processed.display_content`,
  persist `method_state` (mirroring `submit_human_message`), and return
  `{"ok": True}` (plus whatever fields `submit_human_message` returns, for a
  consistent frontend contract).

**A2. Structural pre-check** — a new **pure function**
`check_payload_schema(payload: dict, schema: dict) -> str` in
`methods/parsing.py`. Library-free (the project declares no jsonschema
dependency and we add none): checks required keys are present, and that each
present property's value matches its declared JSON type / `enum` /
numeric `minimum`/`maximum` (recursing one level into arrays and
array-of-objects). Returns `""` when acceptable, else a precise message.
Runs *before* `validate_output` so handler validators never `KeyError` on a
missing field, and the human gets a specific error. It is a structural gate
only — semantic rules (belief sums, cross-field constraints) remain the
handler's `validate_output`.

**A3. Safety net on the free-text path.** In `submit_human_message`, when the
current phase yields an output tool for this entity (structured) and the
content is not a pass, stop running the lossy free-text extractor. If the
content parses as a JSON dict, route it through the same
pre-check → `validate_output` → `process_structured_response` path used by
A1; otherwise return `{"error": "This phase needs structured input — use the
form (expected: <schema hint>)."}` and log. This closes the silent-drop even
if the form is bypassed (old client, race, direct API call).

**The AI's exhausted-retry fallback to `process_response`
(`app_discussion_flow.py:250-262`) is untouched** — it is a separate,
AI-only branch reached when `generate_structured_turn` returns
`structured_output=None`, and it must keep degrading to free text.

**A4. Pass / skip preserved.** A human may still decline a turn: the form's
"Skip turn" action submits a normal pass, handled by the existing
`is_pass(content)` logic. No structured payload is recorded for a pass.

### B. State exposure & dynamic-key resolution

**B1.** `get_state()` (`app.py`) gains a `current_input_spec` key, populated
**only when the current speaker is a human participant (not the moderator)
in a structured phase**:

```python
current_input_spec = {
    "tool_name":   spec.name,
    "description": spec.description,
    "schema":      resolved_schema,   # 'parameters' with dynamic keys expanded
    "renderable":  bool,              # False -> frontend uses guided-JSON fallback
}
```

Otherwise `current_input_spec` is `None`. It is built by a new **pure helper
module** `consensus/structured_input.py` (`build_input_spec(method, entity,
discussion)`, `schema_is_renderable(schema)`), keeping `app.py` thin and the
logic unit-testable without a running app. The moderator guard mirrors the
frontend check at `discussion-actions.js:65` (`entity_type == 'human'` and
`id != moderator_id`).

**B2. Dynamic-key resolution.** A new optional hook
`PhaseHandler.resolve_input_schema(self, spec: OutputToolSpec, entity,
discussion) -> dict` (default returns `spec.parameters` unchanged). Handlers
whose schema uses runtime-derived keys override it:

- The belief-distribution handlers (`DiffuseBeliefsHandler`,
  `PriorBeliefsHandler`, and any other user of `BELIEFS_TOOL_PARAMETERS`)
  override it to expand `beliefs.additionalProperties: {number}` into
  explicit `properties: {H1: {number}, H2: {number}, ...}` with
  `required: [H1, H2, ...]`, via a shared `expand_belief_schema(hypotheses)`
  in `_belief_helpers.py`. The keys come from
  `method_state["hypotheses"]`.
- The matrix handler (`EvaluateMatrixHandler`) does **not** override — its
  2-level nested `additionalProperties` stays unresolved, so
  `schema_is_renderable` returns `False` and the frontend uses the
  guided-JSON fallback (matching the scope decision).

`schema_is_renderable` is computed **server-side** as the single source of
truth: it returns `True` when every property is a supported construct
(primitive, `enum`, `boolean`, array of primitives/enums, one-level
array-of-objects with supported leaves) and `False` on any unresolved
`additionalProperties` or deeper nesting. The frontend renderer also falls
back defensively per field if it meets something unexpected.

### C. Frontend — generic form renderer

**C1.** New self-contained ES module `consensus/static/structured-form.js`
(keeps `discussion.js` from growing past its budget).
`updateInputArea` (`discussion.js:247-297`) renders the structured form in
place of the plain textarea when `state.current_input_spec` is present on a
human turn.

**C2. `renderStructuredForm(spec)`** walks `spec.schema.properties`:

- `number` / `integer` → `<input type="number">` (min/max/step from schema);
- `string` → text input, or `<textarea>` for `reasoning` / long fields;
- `string` + `enum` → `<select>`;
- `boolean` → checkbox;
- `array` of primitives/enums → repeatable rows (add / remove);
- `array` of objects (one level, e.g. cruxes `{claim, belief, why_pivotal}`,
  votes `{motion_id, vote, rationale}`) → repeatable field-groups.

Field labels and help text come from each property's `description`; required
fields are marked and checked client-side (required present, numbers in
range) before submit.

**C3. Guided-JSON fallback** (`renderable === false`, e.g. the matrix): a
`<textarea>` prefilled with a schema-derived JSON skeleton, the
human-readable schema rendered alongside; `JSON.parse` on submit surfaces
parse errors inline. Never a bare "type raw JSON" demand.

**C4. Submit & errors.** The form gathers a typed payload and calls
`api.submitStructuredMessage(speaker_id, payload)`. On `{error}`, the message
is shown **inline in the form** (not a transient toast) and entered values
are preserved. On success, the flow proceeds exactly like `onSendMessage`'s
success path (`getState` → `completeTurnFlow` → `processCurrentTurn`). A
**"Skip turn"** button submits a pass (A4).

**C5. Constraints.** All rendered output is user-selectable (golden rule 10);
sizing uses relative units with no magic numbers (golden rule 9 + the
project CSS convention); light/dark is inherited from the existing CSS custom
properties.

### D. Testing (TDD — failing test first)

- **Pure-function units** (the bulk): `build_input_spec`,
  `schema_is_renderable`, `expand_belief_schema`, and `check_payload_schema`
  across the varied schemas (poll, resolve, crux-selection, cruxes, votes,
  beliefs, matrix) — asserting form-vs-fallback classification, dynamic-key
  expansion, and precise pre-check messages.
- **Real-pipeline flow** (no network, human participant): a new test plus an
  extension to `tests/test_method_flow_e2e.py` in which a human completes the
  **belief poll via a structured payload** and lands a **real number, not
  `?`**, with the belief-shift table populated; the **validation-error path**
  (bad payload → `{error}`, turn not consumed); and the
  `submit_human_message` **safety net** for a structured phase (prose → visible
  error, not silent drop).
- **Frontend renderer**: this project has no JS test harness (already noted
  for #28). The renderer is verified manually in the running app via the
  `run` / `verify` skill, with a Playwright click-through of the belief-poll
  form if practical. Recorded here as the one manually-verified surface.

## Scope guardrails (YAGNI)

Not in this slice: the 2-level matrix as a grid widget (guided-JSON suffices);
token-aware anything; any change to the AI structured path; new runtime
dependencies; a form for human *moderator* summary turns (those use
`submit_moderator_message`, a separate free-text path).

## Files

- `consensus/app_discussion_flow.py` — `submit_human_structured_message`
  (A1), safety-net guard in `submit_human_message` (A3).
- `consensus/methods/parsing.py` — `check_payload_schema` (A2).
- `consensus/structured_input.py` — new: `build_input_spec`,
  `schema_is_renderable` (B1).
- `consensus/app.py` — `current_input_spec` in `get_state` (B1).
- `consensus/methods/phase_handler.py` — `resolve_input_schema` default (B2).
- `consensus/methods/phases/_belief_helpers.py` — `expand_belief_schema`;
  overrides in `diffuse_beliefs.py` / `prior_beliefs.py` / peers (B2).
- `consensus/desktop.py`, `consensus/server.py`, `consensus/static/api.js` —
  expose the new endpoint (A1).
- `consensus/static/structured-form.js` — new renderer (C).
- `consensus/static/discussion.js`, `discussion-actions.js` — wiring (C1, C4).
- `tests/…` — units + flow tests (D).

## Rollback / risk

Additive: a new endpoint, a new state key, a new optional handler hook with an
identity default, and a new frontend module. The existing AI structured path
and free-text human path are unchanged except for the A3 safety-net guard
(which only fires in structured phases, where the old behaviour was a silent
drop — strictly an improvement). No schema/DB migration.
