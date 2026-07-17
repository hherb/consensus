# Triage Blocked-Switch Recovery — Design

Date: 2026-07-17
Status: draft (awaiting owner approval)

## Problem

When Guided Triage hands off to its chosen method, `switch_discussion_method`
(`consensus/app_discussion_flow.py`) runs the issue-#23 tool-capability gate.
If any AI member's model is known to lack tool support, the switch is
rejected — and `complete_turn` returns `method_complete: True` +
`switch_error`, which the frontend (`handleTurnLimitFlags` in
`consensus/static/discussion-actions.js`) answers by **concluding the
discussion outright**. The user loses a live discussion over a fixable
model assignment.

## Owner decisions (2026-07-17)

1. **Recovery options:** fix the offending participant's model and retry
   the switch, or conclude the discussion. No "choose a different method"
   path, no triage re-run (YAGNI).
2. **Model change semantics:** the fix updates the saved **entity profile**
   (existing `save_entity` path); the live discussion's in-memory copy is
   refreshed at retry time. No per-discussion model override mechanism.
3. **Wait state:** the discussion is **auto-paused** while the blocked
   switch waits for the user. The blocked-switch info is persisted in
   `method_state` and exposed via `get_state`, so the recovery dialog
   survives reload/reconnect (same pattern as `pending_user_input`).
4. **Approach:** dedicated pending-switch state + explicit
   `retry_method_switch` endpoint (approach A). Resume is not overloaded;
   no frontend-only orchestration.

## Backend design

### 1. Gate returns structured offenders — `consensus/structured_output.py`

New pure function:

```python
def find_tool_blocked_entities(
    discussion, db, method_name=None,
) -> list[dict]:
    """[{"entity_id": int, "name": str, "model": str}, ...] for every
    AI member whose model is known to lack tool support (supports_tools
    is False). Empty when the target method has no structured phases.
    Unknown capability (None) still passes, as today."""
```

`_validate_structured_output_support` becomes a thin wrapper that formats
that list into its error string — now naming **all** blocked participants,
not just the first. Its `""`-or-error-string contract is unchanged, so the
setup-time gate (`start_discussion`) needs no changes.

### 2. `switch_discussion_method` — `consensus/app_discussion_flow.py`

On gate rejection the error dict gains the structured list:

```python
{"error": msg, "blocked_entities": [...]}
```

No other change: discussion method/state/messages stay untouched on
rejection, exactly as today.

### 3. `complete_turn` blocked-switch branch — pause instead of conclude

Current behavior kept: `logger.warning`, and the once-per-target system
notice (`_switch_error_posted` scalar semantics unchanged).

New behavior replacing the `method_complete: True` return:

- Store and persist
  `method_state["_pending_method_switch"] = {"target_method": chosen,
  "switch_error": switch_error, "blocked_entities": [...]}` — the same
  key names as the `complete_turn` return below, so the frontend dialog
  consumes one shape whether it arrives live or via `get_state` after a
  reconnect.
- Auto-pause via the existing `pause_discussion()`
  (`consensus/app_discussion_state.py`) — posts `-- Discussion paused --`.
- Return:

```python
{
    "method_switch_blocked": True,
    "switch_error": switch_error,
    "target_method": chosen,
    "blocked_entities": [...],
    "turn_number": ..., "current_round": ...,
    "state": get_state_fn(),
}
```

**No code path returns `method_complete` for a blocked switch anymore** —
that flag is what made the frontend conclude.

### 4. Reconnection safety — `consensus/app.py` `get_state`

`state["pending_method_switch"]` mirrors
`method_state["_pending_method_switch"]` while `discussion_method ==
"triage"` and the key is present; `None` otherwise. Same pattern as
`pending_user_input`.

### 5. New `retry_method_switch()` — flow function + app wrapper

Flow function in `consensus/app_discussion_flow.py`; thin
`ConsensusApp.retry_method_switch()` wrapper in `consensus/app.py`.

Guards (error dict on violation): discussion loaded (`discussion.id`),
`discussion_method == "triage"`, `_pending_method_switch` present, status
not `"concluded"` (accept `"paused"` — the normal case — and `"active"`,
for robustness against a manual resume).

Steps:

1. **Refresh AI members from DB:** for each AI entity in
   `discussion.entities`, re-read its row (`db.get_entity`), rebuild via
   `Entity.from_db_row`, and swap the fresh `ai_config` onto the live
   object (entity identity and roster order preserved; missing rows are
   skipped). This is what makes the profile edit take effect in the
   in-memory discussion.
2. Re-run `switch_discussion_method(discussion, db, chosen)`.
3. **Success:** `_pending_method_switch` vanishes naturally — it starts
   with `_` but is *not* in the preserved-keys set of
   `switch_discussion_method`, so `init_state` wipes it (this is by
   design; do not add it to the preserved set). Then, mirroring
   `complete_turn`'s successful-switch path: `resume_discussion()` if
   paused (posts `-- Discussion resumed --`),
   `apply_method_turn_order(discussion, reset_index=True)`,
   `stamp_turn_index` + persist `method_state`. Return
   `{"method_switched": True, "new_method": ..., "state": ...}` —
   the same shape `complete_turn` returns for an unblocked handoff.
4. **Failure:** update `_pending_method_switch` with the fresh error and
   blocked list, persist, stay paused. Return the `method_switch_blocked`
   shape again (state included). No duplicate transcript notice for the
   same target (existing `_switch_error_posted` behavior).

### 6. API exposure

- `consensus/desktop.py`: bridge method `retry_method_switch()`.
- `consensus/server.py`: RPC-map entry
  `"retry_method_switch": lambda: app.retry_method_switch()`.
- `consensus/static/api.js`: `retryMethodSwitch()` on both transports.

### 7. "Conclude anyway"

No backend change: `conclude_discussion` has no status guard and already
works on paused discussions.

## Frontend design

### `handleTurnLimitFlags` — `consensus/static/discussion-actions.js`

New branch **before** `method_complete`:

```js
if (result?.method_switch_blocked) {
    renderDiscussion();
    showSwitchBlockedDialog(result);
    return true;   // stop the turn loop; discussion is paused server-side
}
```

The `method_complete` branch drops its `switch_error` ternary (the backend
no longer emits that combination) and returns to the plain completion
toast + conclude.

### New module `consensus/static/method-switch.js`

`discussion-actions.js` is at 423 lines; the dialog logic goes in a new
small ES module (500-line rule).

`showSwitchBlockedDialog({target_method, switch_error, blocked_entities})`:

- Explanation line: the recommended method could not be adopted + the
  error text (user-selectable, golden rule 10).
- One row per blocked entity: name, current model, provider model
  dropdown + custom-model text fallback — the same select+custom-input
  pattern as `profiles.js` (`api.fetchModels(provider_id)`), with the
  dialog's own element IDs (e.g. `#switch-blocked-dialog`), not the
  profile editor's. Entity fields come from `state.saved_entities`
  (full row needed for `save_entity`).
- Buttons: **Retry switch** (primary), **Conclude discussion**
  (secondary).

Retry flow: for each row whose model changed →
`api.saveEntity({...fullRow, model: newModel})`; then
`api.retryMethodSwitch()`. On `method_switched`: hide dialog, toast
"Method switched to <display name>", `onStateUpdate(result.state)`,
`renderDiscussion()`, `processCurrentTurn()` (the discussion is active
again). On `method_switch_blocked`: update the dialog's error text and
rows in place, keep it open (discussion stays paused).

Conclude flow: hide dialog, call the existing conclude action.

### Reconnect hook — `consensus/static/app.js`

Next to the existing `pending_user_input` check (~line 245): if
`state.pending_method_switch` is present, show the recovery dialog with
that data. Works for reload and multi-user reconnect; the discussion is
paused, so nothing races.

### Markup/CSS — `consensus/static/index.html` + stylesheet

Dialog markup reuses the existing dialog classes; sizing in relative
units only (~90vw/90vh cap per project rules); all displayed text
selectable.

## Testing design

Backend (pytest, no network; stub `PricingCache.supports_tools` as the
existing `TestBlockedSwitch*` tests in `tests/test_app_discussion_flow.py`
do):

- `find_tool_blocked_entities`: multiple offenders all reported; empty for
  non-structured targets; `None` capability passes.
- `_validate_structured_output_support`: error string names all offenders;
  `""` contract preserved.
- Updated existing blocked-switch tests: blocked handoff now returns
  `method_switch_blocked` (not `method_complete`), pauses the discussion,
  persists `_pending_method_switch`, still posts exactly one notice per
  target.
- `get_state` exposes `pending_method_switch` while pending; `None` after
  a successful switch and for non-triage methods.
- `retry_method_switch` guards: no discussion / not triage / no pending /
  concluded → error dict.
- Retry failure: capability still False → still paused, pending updated,
  `method_switch_blocked` returned, no duplicate notice.
- Retry success: profile model changed in DB (capability True/unknown) →
  in-memory `ai_config` refreshed, method switched, status active again,
  turn order reset to the new method's first phase, pending state gone
  from `method_state` and `get_state`.
- Full flow test through the real pipeline (`complete_turn` driver, per
  the HANDOVER convention): triage confirm → blocked → paused → fix model
  → retry → `method_switched` → next turn runs under the new method.

Frontend: no JS test harness exists in this project — the dialog is
verified statically and via the backend state contract (accepted
limitation, as with the #28 evidence button).

## Out of scope

- Choosing a different method from the recovery dialog.
- Per-discussion model overrides.
- Filtering the model dropdown to tool-capable models only (the retry
  loop already contains a wrong pick gracefully; revisit if it annoys).
- A JS test harness.
