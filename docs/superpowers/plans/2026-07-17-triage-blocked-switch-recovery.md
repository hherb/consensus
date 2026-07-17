# Triage Blocked-Switch Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Triage handoff blocked by the tool-capability gate pauses the discussion and lets the user fix the offending participant's model and retry, instead of auto-concluding.

**Architecture:** The gate learns to report *all* offending AI members structurally. `complete_turn`'s blocked branch stores a `_pending_method_switch` record in `method_state`, auto-pauses, and returns a new `method_switch_blocked` flag (never `method_complete`). A new `retry_method_switch()` refreshes AI configs from the DB, re-runs the switch, and resumes on success. The frontend shows a recovery dialog (fix model → retry, or conclude), reconnection-safe via `get_state`.

**Tech Stack:** Python 3 (aiohttp/pywebview backends), SQLite, vanilla-JS ES modules, pytest.

**Spec:** `docs/superpowers/specs/2026-07-17-triage-blocked-switch-recovery-design.md`

## Global Constraints

- `uv` only, never pip. Run tests with `uv run pytest` (full suite) / `uv run pytest <file> -v` (task-level).
- TDD: failing test first, then minimal implementation.
- Docstrings + type hints mandatory; no magic numbers; files under ~500 lines.
- All caught errors must be logged and user-visible (golden rule 6).
- Frontend sizing: relative units only; all displayed text user-selectable.
- Branch: `claude/triage-switch-recovery` (already created; spec committed).
- Baseline: 2406 tests passing on main.

---

### Task 1: Structured offender list from the tool gate

**Files:**
- Modify: `consensus/structured_output.py:56-114`
- Test: `tests/test_structured_setup_check.py`

**Interfaces:**
- Produces: `find_tool_blocked_entities(discussion, db, method_name=None) -> list[dict]` — each dict `{"entity_id": int, "name": str, "model": str}`; empty list when nothing is blocked.
- Produces (unchanged contract): `_validate_structured_output_support(discussion, db, method_name=None) -> str` — `""` or error string, now naming **all** offenders.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_structured_setup_check.py`. Extend the module imports first:

```python
from consensus.models import Entity
from consensus.structured_output import find_tool_blocked_entities
```

(Note `_validate_structured_output_support` is already imported at the top via `consensus.app_discussion_setup`, which re-exports it.)

Append this class at the end of the file:

```python
class TestFindToolBlockedEntities:
    """find_tool_blocked_entities returns structured offender info —
    the blocked-switch recovery dialog needs ALL offenders, not just
    the first (spec 2026-07-17)."""

    def _add_second_ai(self, tmp_db, disc, model: str) -> int:
        """Add a second AI member with the given model to the roster."""
        pid = tmp_db.add_provider("P2", "http://localhost:9999/v1", "")
        eid = tmp_db.add_entity(
            "Carol", "ai", "#00ffff", pid, model, 0.5, 512, "")
        disc.entities.append(Entity.from_db_row(tmp_db.get_entity(eid)))
        return eid

    def test_lists_all_blocked_ai_members(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        _insert_model(tmp_db, "test/other-model", "temperature,top_p")
        disc = discussion_with_entities
        carol_id = self._add_second_ai(tmp_db, disc, "other-model")
        disc.discussion_method = "delphi"

        blocked = find_tool_blocked_entities(disc, tmp_db)

        assert blocked == [
            {"entity_id": disc.entities[0].id, "name": "Alice",
             "model": "test-model"},
            {"entity_id": carol_id, "name": "Carol",
             "model": "other-model"},
        ]

    def test_empty_for_unstructured_method(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc = discussion_with_entities
        disc.discussion_method = "open_discussion"

        assert find_tool_blocked_entities(disc, tmp_db) == []

    def test_unknown_capability_not_blocked(
            self, tmp_db, discussion_with_entities, monkeypatch):
        """No pricing row (e.g. a local model) passes, as at setup."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        disc = discussion_with_entities
        disc.discussion_method = "delphi"

        assert find_tool_blocked_entities(disc, tmp_db) == []

    def test_explicit_method_name_overrides_discussion(
            self, tmp_db, discussion_with_entities, monkeypatch):
        """The prospective-switch case: discussion still holds the old
        method, the target is passed explicitly."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc = discussion_with_entities
        disc.discussion_method = "triage"

        blocked = find_tool_blocked_entities(disc, tmp_db, "delphi")

        assert len(blocked) == 1 and blocked[0]["name"] == "Alice"

    def test_error_string_names_all_offenders(
            self, tmp_db, discussion_with_entities, monkeypatch):
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        _insert_model(tmp_db, "test/other-model", "temperature,top_p")
        disc = discussion_with_entities
        self._add_second_ai(tmp_db, disc, "other-model")
        disc.discussion_method = "delphi"

        error = _validate_structured_output_support(disc, tmp_db)

        assert "test-model" in error
        assert "other-model" in error
        assert "tool" in error.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_structured_setup_check.py -v`
Expected: the new tests FAIL with `ImportError: cannot import name 'find_tool_blocked_entities'`; all pre-existing tests in the file still pass once the import error is hit (the import failure blocks the whole file — that is the failing signal).

- [ ] **Step 3: Implement**

In `consensus/structured_output.py`, replace the body of `_validate_structured_output_support` (keep its docstring, adding one line noting it delegates to `find_tool_blocked_entities`) and add the new function directly above it:

```python
def find_tool_blocked_entities(
    discussion: "Discussion", db: "Database",
    method_name: Optional[str] = None,
) -> list[dict]:
    """List AI members whose models are known to lack tool support.

    Returns one ``{"entity_id", "name", "model"}`` dict per AI member
    (moderator included) whose model capability is known-False for the
    target method's forced output tools — the structured form the
    blocked-switch recovery dialog needs (spec 2026-07-17).  Empty when
    the target method has no structured phases, when the method name is
    unknown, or when every model is tool-capable or of unknown
    capability (None passes; the runtime path raises loudly instead —
    see module docstring).

    Args:
        discussion: The discussion whose entities are checked.
        db: Database handle providing the pricing cache.
        method_name: Target method; defaults to the discussion's
            current method (setup-time case).  Prospective switches
            must pass the target explicitly.
    """
    target = (
        method_name if method_name is not None
        else discussion.discussion_method
    )
    try:
        method = get_method(target)
    except KeyError:
        return []  # open_discussion — no structured phases
    if not method.requires_structured_output():
        return []
    blocked: list[dict] = []
    for e in discussion.entities:
        if e.entity_type != EntityType.AI or not e.ai_config:
            continue
        supported = db.pricing.supports_tools(
            e.ai_config.model, e.ai_config.base_url)
        if supported is False:
            blocked.append({
                "entity_id": e.id,
                "name": e.name,
                "model": e.ai_config.model,
            })
    return blocked
```

New `_validate_structured_output_support` body (docstring retained from the current implementation, with the Returns line unchanged; the entity loop is replaced by):

```python
    blocked = find_tool_blocked_entities(discussion, db, method_name)
    if not blocked:
        return ""
    target = (
        method_name if method_name is not None
        else discussion.discussion_method
    )
    method = get_method(target)
    if len(blocked) == 1:
        offender = blocked[0]
        models_clause = (
            f"{offender['name']}'s model '{offender['model']}' does "
            "not support tool calls"
        )
    else:
        listing = "; ".join(
            f"{b['name']}'s model '{b['model']}'" for b in blocked
        )
        models_clause = f"these models do not support tool calls: {listing}"
    return (
        f"The {method.display_name} method requires structured "
        f"outputs via native tool calling, but {models_clause}. "
        "Assign tool-capable models or choose a different method."
    )
```

- [ ] **Step 4: Run the task tests and the neighbours that assert on the error string**

Run: `uv run pytest tests/test_structured_setup_check.py tests/test_app_discussion_flow.py tests/test_structured_output.py -v`
Expected: PASS (the existing assertions check `"test-model" in error` and `"tool" in error.lower()`, both preserved). If any other test asserts the exact old wording, update it to assert on model name + "tool" substring instead.

- [ ] **Step 5: Commit**

```bash
git add consensus/structured_output.py tests/test_structured_setup_check.py
git commit -m "feat(methods): structured offender list from the tool-capability gate"
```

---

### Task 2: `switch_discussion_method` carries `blocked_entities`

**Files:**
- Modify: `consensus/app_discussion_flow.py:384-387` (gate call inside `switch_discussion_method`) and the module import at line 15
- Test: `tests/test_app_discussion_flow.py` (class `TestSwitchDiscussionMethodToolCapability`)

**Interfaces:**
- Consumes: `find_tool_blocked_entities` (Task 1).
- Produces: on gate rejection `switch_discussion_method` returns `{"error": str, "blocked_entities": list[dict]}`. All other error returns (`triage` target, unknown method) remain plain `{"error": str}` — they are misconfigurations, not fixable-model cases.

- [ ] **Step 1: Write the failing test**

Add to `TestSwitchDiscussionMethodToolCapability` in `tests/test_app_discussion_flow.py`:

```python
    def test_blocked_switch_reports_blocked_entities(
        self, tmp_db, discussion_with_entities, monkeypatch
    ):
        """The gate error carries the structured offender list the
        recovery dialog needs (spec 2026-07-17)."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc = self._prepare(discussion_with_entities, tmp_db)

        result = switch_discussion_method(disc, tmp_db, "delphi")

        assert result["blocked_entities"] == [{
            "entity_id": disc.entities[0].id,
            "name": "Alice",
            "model": "test-model",
        }]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_app_discussion_flow.py::TestSwitchDiscussionMethodToolCapability -v`
Expected: new test FAILS with `KeyError: 'blocked_entities'`.

- [ ] **Step 3: Implement**

In `consensus/app_discussion_flow.py` line 15, extend the import:

```python
from .structured_output import (
    _validate_structured_output_support, find_tool_blocked_entities,
)
```

In `switch_discussion_method`, replace:

```python
    tool_error = _validate_structured_output_support(
        discussion, db, method_name)
    if tool_error:
        return {"error": tool_error}
```

with:

```python
    tool_error = _validate_structured_output_support(
        discussion, db, method_name)
    if tool_error:
        return {
            "error": tool_error,
            "blocked_entities": find_tool_blocked_entities(
                discussion, db, method_name),
        }
```

Also update the function's docstring paragraph that says "Triage falls through to ``method_complete`` when this returns an error" to: "Triage records a pending switch and pauses when this returns an error (spec 2026-07-17); the error dict carries ``blocked_entities`` so the recovery UI can name the offenders."

- [ ] **Step 4: Run the class**

Run: `uv run pytest tests/test_app_discussion_flow.py::TestSwitchDiscussionMethodToolCapability -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add consensus/app_discussion_flow.py tests/test_app_discussion_flow.py
git commit -m "feat(methods): blocked switch reports offending entities"
```

---

### Task 3: `complete_turn` blocked branch — pause + pending record

**Files:**
- Modify: `consensus/app_discussion_flow.py` (imports; blocked branch inside `complete_turn`, currently lines 639-677)
- Test: `tests/test_app_discussion_flow.py` (rework class `TestCompleteTurnBlockedTriageSwitch`, lines 486-656)

**Interfaces:**
- Consumes: `pause_discussion` from `consensus/app_discussion_state.py` (module-level import is safe: `app_discussion_state` has no module-level import of `app_discussion_flow`).
- Produces: blocked handoff return shape (also produced by Task 5's retry on failure):

```python
{
    "method_switch_blocked": True,
    "switch_error": str,
    "target_method": str,
    "blocked_entities": list[dict],
    "turn_number": int,
    "current_round": int,
    "state": dict,
}
```

- Produces: `method_state["_pending_method_switch"]` = `{"target_method": str, "switch_error": str, "blocked_entities": list[dict]}` (persisted; deliberately NOT added to `switch_discussion_method`'s preserved-keys set, so a later successful switch wipes it).

- [ ] **Step 1: Rework the tests (failing first)**

In `tests/test_app_discussion_flow.py`, promote the three helpers of `TestCompleteTurnBlockedTriageSwitch` to module-level functions so Task 5's retry tests can reuse them. Place them directly above the class, drop the `self` parameter, and update all call sites inside the class (`self._make_triage_pipeline(...)` → `_make_triage_pipeline(...)`, etc.):

```python
def _make_triage_pipeline(tmp_db, sample_provider):
    """Active triage discussion at the confirm phase, driven by a
    human moderator, with one AI panel member (model 'test-model').

    Returns (discussion, moderator_entity, Moderator, PricingCache).
    Mirrors the pipeline pattern in tests/test_belief_diffusion_abort.py.
    """
    # body unchanged from the current method (lines 492-530)


async def _drive_turn(disc, mod, moderator, tmp_db, pricing):
    """One human-moderator turn through the real pipeline."""
    # body unchanged from the current method (lines 532-543)


def _blocked_notices(disc):
    """System messages announcing the blocked switch."""
    # body unchanged from the current method (lines 545-549)
```

Then update the class docstring and tests to the new contract:

```python
class TestCompleteTurnBlockedTriageSwitch:
    """A triage switch blocked by the tool-capability gate must be loud
    AND recoverable: logged, posted into the transcript, returned as
    ``method_switch_blocked`` (never ``method_complete``), the pending
    switch persisted, and the discussion auto-paused so the user can
    fix the model and retry (spec 2026-07-17)."""

    @pytest.mark.asyncio
    async def test_blocked_switch_is_loud(
        self, tmp_db, sample_provider, monkeypatch, caplog,
    ):
        """A blocked switch returns method_switch_blocked + switch_error,
        logs a warning, and posts an explanatory system message."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc, mod, moderator, pricing = _make_triage_pipeline(
            tmp_db, sample_provider)

        with caplog.at_level(logging.WARNING,
                             logger="consensus.app_discussion_flow"):
            result = await _drive_turn(
                disc, mod, moderator, tmp_db, pricing)

        assert result.get("method_switch_blocked") is True
        assert "method_complete" not in result
        assert result["target_method"] == "delphi"
        assert "test-model" in result["switch_error"]
        assert result["blocked_entities"][0]["model"] == "test-model"
        assert disc.discussion_method == "triage"
        assert any("test-model" in rec.message for rec in caplog.records)
        notices = _blocked_notices(disc)
        assert len(notices) == 1
        assert "test-model" in notices[0].content

    @pytest.mark.asyncio
    async def test_blocked_switch_pauses_and_persists_pending(
        self, tmp_db, sample_provider, monkeypatch,
    ):
        """The discussion auto-pauses and the pending switch survives in
        the DB row, so the recovery dialog can reappear after a reload."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc, mod, moderator, pricing = _make_triage_pipeline(
            tmp_db, sample_provider)

        await _drive_turn(disc, mod, moderator, tmp_db, pricing)

        assert disc.status == "paused"
        assert disc.is_active is False
        pending = disc.method_state["_pending_method_switch"]
        assert pending["target_method"] == "delphi"
        assert "test-model" in pending["switch_error"]
        assert pending["blocked_entities"][0]["name"] == "Alice"
        stored = json.loads(
            tmp_db.get_discussion(disc.id)["method_state"])
        assert stored["_pending_method_switch"]["target_method"] == "delphi"

    @pytest.mark.asyncio
    async def test_blocked_switch_notice_posted_only_once(
        self, tmp_db, sample_provider, monkeypatch,
    ):
        """A manual resume without fixing anything re-blocks (and
        re-pauses) but must not repost the transcript notice."""
        from consensus.app_discussion_state import resume_discussion

        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc, mod, moderator, pricing = _make_triage_pipeline(
            tmp_db, sample_provider)

        result = await _drive_turn(disc, mod, moderator, tmp_db, pricing)
        assert result.get("method_switch_blocked") is True

        resume_discussion(disc, tmp_db)
        result = await complete_turn(
            disc, moderator, tmp_db, pricing,
            get_state_fn=lambda: {},
            moderator_summary="Noted.",
        )
        assert result.get("method_switch_blocked") is True
        assert disc.status == "paused"
        assert len(_blocked_notices(disc)) == 1, (
            "the blocked-switch notice was posted more than once")

    @pytest.mark.asyncio
    async def test_blocked_switch_to_different_method_posts_new_notice(
        self, tmp_db, sample_provider, monkeypatch,
    ):
        """The once-only dedup stays per target method: a later blocked
        switch to a different method is new information."""
        from consensus.app_discussion_state import resume_discussion

        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc, mod, moderator, pricing = _make_triage_pipeline(
            tmp_db, sample_provider)

        result = await _drive_turn(disc, mod, moderator, tmp_db, pricing)
        assert result.get("method_switch_blocked") is True
        assert len(_blocked_notices(disc)) == 1

        resume_discussion(disc, tmp_db)
        disc.method_state["chosen_method"] = "ach"
        disc.method_state["recommended_method"] = "ach"
        result = await complete_turn(
            disc, moderator, tmp_db, pricing,
            get_state_fn=lambda: {},
            moderator_summary="Noted.",
        )
        assert result.get("method_switch_blocked") is True
        assert result["target_method"] == "ach"
        notices = _blocked_notices(disc)
        assert len(notices) == 2
        assert "Analysis of Competing Hypotheses" in notices[1].content

    @pytest.mark.asyncio
    async def test_unknown_capability_switch_unchanged(
        self, tmp_db, sample_provider, monkeypatch,
    ):
        """No pricing data (e.g. a local model): the switch proceeds."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        disc, mod, moderator, pricing = _make_triage_pipeline(
            tmp_db, sample_provider)

        result = await _drive_turn(disc, mod, moderator, tmp_db, pricing)

        assert result.get("method_switched") is True
        assert "switch_error" not in result
        assert disc.discussion_method == "delphi"
        assert _blocked_notices(disc) == []
```

Add `import json` to the test module imports if not present (it is not — the current imports are `logging`, `time`).

- [ ] **Step 2: Run to verify the new expectations fail**

Run: `uv run pytest tests/test_app_discussion_flow.py::TestCompleteTurnBlockedTriageSwitch -v`
Expected: FAIL — `method_switch_blocked` is absent, `method_complete` is present, status stays `"active"`.

- [ ] **Step 3: Implement**

In `consensus/app_discussion_flow.py`:

Add the module-level import (after the `.pricing` import at line 14):

```python
from .app_discussion_state import pause_discussion, resume_discussion
```

(`resume_discussion` is used by Task 5; importing both now avoids touching the line twice.)

Replace the blocked-switch tail of `complete_turn` (from `switch_error = switch_result["error"]` at line 639 through the `return {"method_complete": True, "switch_error": ...}` dict at lines 671-677 — the final unconditional `method_complete` return for genuinely ended methods at lines 678-683 stays untouched):

```python
                    switch_error = switch_result["error"]
                    blocked_entities = switch_result.get(
                        "blocked_entities", [])
                    logger.warning(
                        "Triage could not switch discussion %s to %r: %s",
                        discussion.id, chosen, switch_error,
                    )
                    # Record the pending switch so the user can fix the
                    # offending model and retry (spec 2026-07-17); the
                    # record survives reload via method_state and is
                    # wiped by init_state on a later successful switch.
                    discussion.method_state["_pending_method_switch"] = {
                        "target_method": chosen,
                        "switch_error": switch_error,
                        "blocked_entities": blocked_entities,
                    }
                    # Scalar last-target key, deliberately: after an
                    # intervening blocked switch to a different method,
                    # re-notifying about an earlier target again is new
                    # information, not spam.
                    already_notified = discussion.method_state.get(
                        "_switch_error_posted")
                    if mod and discussion.id and already_notified != chosen:
                        discussion.method_state[
                            "_switch_error_posted"] = chosen
                        notice = (
                            "**The recommended method could not be "
                            f"adopted.** {switch_error}"
                        )
                        sys_msg = Message(
                            entity_id=mod.id, entity_name=mod.name,
                            content=notice, role=MessageRole.SYSTEM,
                        )
                        discussion.messages.append(sys_msg)
                        db.add_message(
                            discussion.id, mod.id, notice, "system",
                            turn_number=discussion.turn_number,
                        )
                    if discussion.id:
                        db.update_discussion(
                            discussion.id,
                            method_state=serialize_method_state(
                                discussion.method_state),
                        )
                    # Pause instead of concluding: the frontend shows a
                    # recovery dialog and retries via retry_method_switch.
                    if discussion.status == "active":
                        pause_discussion(discussion, db)
                    return {
                        "method_switch_blocked": True,
                        "switch_error": switch_error,
                        "target_method": chosen,
                        "blocked_entities": blocked_entities,
                        "turn_number": discussion.turn_number,
                        "current_round": discussion.current_round,
                        "state": get_state_fn(),
                    }
```

- [ ] **Step 4: Run the class, then the file**

Run: `uv run pytest tests/test_app_discussion_flow.py -v`
Expected: PASS. Also run `uv run pytest tests/test_method_state_persistence.py tests/test_triage_handlers.py -v` (the other files touching `switch_discussion_method`) — expected PASS.

- [ ] **Step 5: Commit**

```bash
git add consensus/app_discussion_flow.py tests/test_app_discussion_flow.py
git commit -m "feat(methods): blocked triage handoff pauses instead of concluding"
```

---

### Task 4: `get_state` exposes the pending switch

**Files:**
- Modify: `consensus/app.py:459-514` (`get_state`)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `state["pending_method_switch"]` — the `_pending_method_switch` dict while `discussion_method == "triage"` and the record exists; `None` otherwise.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app.py` (uses the existing `app` / `app_with_entities` fixtures):

```python
class TestPendingMethodSwitchState:
    """get_state exposes a pending blocked method switch so the
    recovery dialog survives reload/reconnect (spec 2026-07-17)."""

    _PENDING = {
        "target_method": "delphi",
        "switch_error": "model lacks tool support",
        "blocked_entities": [
            {"entity_id": 2, "name": "Alice", "model": "llama3"},
        ],
    }

    def test_exposed_while_pending(self, app_with_entities):
        app, mod_id, p1_id, p2_id = app_with_entities
        app.discussion.discussion_method = "triage"
        app.discussion.method_state["_pending_method_switch"] = dict(
            self._PENDING)

        pending = app.get_state()["pending_method_switch"]

        assert pending["target_method"] == "delphi"
        assert pending["blocked_entities"][0]["name"] == "Alice"

    def test_none_when_absent(self, app_with_entities):
        app, *_ = app_with_entities
        app.discussion.discussion_method = "triage"

        assert app.get_state()["pending_method_switch"] is None

    def test_none_for_non_triage_method(self, app_with_entities):
        """A stale record under a non-triage method is not exposed —
        after a successful switch there is nothing to recover."""
        app, *_ = app_with_entities
        app.discussion.discussion_method = "delphi"
        app.discussion.method_state["_pending_method_switch"] = dict(
            self._PENDING)

        assert app.get_state()["pending_method_switch"] is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_app.py::TestPendingMethodSwitchState -v`
Expected: FAIL with `KeyError: 'pending_method_switch'`.

- [ ] **Step 3: Implement**

In `consensus/app.py` `get_state`, after the `panel_advisory` block (line 507) and before the `pending_user_input` block, insert:

```python
        # Pending blocked method switch (triage handoff, spec
        # 2026-07-17) — exposed so the recovery dialog reappears after
        # a reload/reconnect, like pending_user_input below.
        pending_switch = None
        if self.discussion.discussion_method == "triage":
            pending_switch = self.discussion.method_state.get(
                "_pending_method_switch")
        state["pending_method_switch"] = pending_switch
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_app.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add consensus/app.py tests/test_app.py
git commit -m "feat(app): expose pending method switch in get_state"
```

---

### Task 5: `retry_method_switch` + entity-config refresh

**Files:**
- Modify: `consensus/app_discussion_flow.py` (two new functions after `switch_discussion_method`)
- Modify: `consensus/app.py` (wrapper after `resume_discussion`, line 885)
- Test: `tests/test_app_discussion_flow.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `_make_triage_pipeline` / `_drive_turn` / `_blocked_notices` module-level test helpers (Task 3); `switch_discussion_method` blocked shape (Task 2); `resume_discussion` import (Task 3).
- Produces:
  - `refresh_ai_configs(discussion: Discussion, db: Database) -> None`
  - `retry_method_switch(discussion: Discussion, db: Database, get_state_fn: Callable[[], dict]) -> dict` returning `{"method_switched": True, "new_method": dict, "turn_number": int, "current_round": int, "state": dict}` on success, the Task 3 `method_switch_blocked` shape on repeat failure, or `{"error": str}` when there is nothing to retry.
  - `ConsensusApp.retry_method_switch() -> dict`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_app_discussion_flow.py` (import `retry_method_switch` in the module's `from consensus.app_discussion_flow import (...)` block; add `from consensus.models import EntityType` if not already imported — it is, via line 18):

```python
class TestRetryMethodSwitch:
    """retry_method_switch — blocked-switch recovery (spec 2026-07-17)."""

    async def _block(self, tmp_db, sample_provider, monkeypatch):
        """Drive a triage discussion into the blocked-paused state."""
        monkeypatch.setattr(tmp_db.pricing, "refresh", lambda: False)
        _insert_model(tmp_db, "test/test-model", "temperature,top_p")
        disc, mod, moderator, pricing = _make_triage_pipeline(
            tmp_db, sample_provider)
        result = await _drive_turn(disc, mod, moderator, tmp_db, pricing)
        assert result.get("method_switch_blocked") is True
        assert disc.status == "paused"
        return disc

    @pytest.mark.asyncio
    async def test_retry_succeeds_after_model_fix(
        self, tmp_db, sample_provider, monkeypatch,
    ):
        """Fixing the profile's model and retrying switches the method,
        refreshes the in-memory config, and resumes the discussion."""
        disc = await self._block(tmp_db, sample_provider, monkeypatch)
        _insert_model(tmp_db, "test/good-model", "temperature,tools")
        ai = next(e for e in disc.entities
                  if e.entity_type == EntityType.AI)
        tmp_db.update_entity(ai.id, model="good-model")

        result = retry_method_switch(disc, tmp_db, get_state_fn=lambda: {})

        assert result.get("method_switched") is True
        assert result["new_method"]["name"] == "delphi"
        assert disc.discussion_method == "delphi"
        assert ai.ai_config.model == "good-model"
        assert disc.status == "active"
        assert disc.is_active is True
        assert "_pending_method_switch" not in disc.method_state
        # The discussion is positioned to run under the new method:
        # delphi's first phase, rotation restarted (spec: "next turn
        # runs under the new method").
        assert disc.method_state["current_phase"] == "estimate"
        assert disc.current_turn_index == 0
        assert disc.turn_order == [ai.id]

    @pytest.mark.asyncio
    async def test_retry_still_blocked(
        self, tmp_db, sample_provider, monkeypatch,
    ):
        """Retrying without fixing anything stays paused, keeps the
        pending record fresh, and posts no duplicate notice."""
        disc = await self._block(tmp_db, sample_provider, monkeypatch)

        result = retry_method_switch(disc, tmp_db, get_state_fn=lambda: {})

        assert result.get("method_switch_blocked") is True
        assert "test-model" in result["switch_error"]
        assert disc.status == "paused"
        assert disc.discussion_method == "triage"
        pending = disc.method_state["_pending_method_switch"]
        assert pending["target_method"] == "delphi"
        assert len(_blocked_notices(disc)) == 1

    def test_no_pending_returns_error(
        self, tmp_db, discussion_with_entities,
    ):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.discussion_method = "triage"

        result = retry_method_switch(disc, tmp_db, get_state_fn=lambda: {})

        assert "error" in result

    def test_non_triage_returns_error(
        self, tmp_db, discussion_with_entities,
    ):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.discussion_method = "delphi"
        disc.method_state["_pending_method_switch"] = {
            "target_method": "ach", "switch_error": "x",
            "blocked_entities": [],
        }

        result = retry_method_switch(disc, tmp_db, get_state_fn=lambda: {})

        assert "error" in result

    def test_concluded_returns_error(
        self, tmp_db, discussion_with_entities,
    ):
        disc = discussion_with_entities
        disc.id = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.discussion_method = "triage"
        disc.status = "concluded"
        disc.method_state["_pending_method_switch"] = {
            "target_method": "delphi", "switch_error": "x",
            "blocked_entities": [],
        }

        result = retry_method_switch(disc, tmp_db, get_state_fn=lambda: {})

        assert "error" in result
```

Add to `tests/test_app.py` (app-level integration — pending record clears from state):

```python
class TestRetryMethodSwitchWrapper:
    """ConsensusApp.retry_method_switch delegates and refreshes state."""

    def test_error_without_pending(self, app_with_entities):
        app, *_ = app_with_entities
        result = app.retry_method_switch()
        assert "error" in result
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_app_discussion_flow.py::TestRetryMethodSwitch tests/test_app.py::TestRetryMethodSwitchWrapper -v`
Expected: FAIL with `ImportError` / `AttributeError` (functions do not exist yet).

- [ ] **Step 3: Implement**

In `consensus/app_discussion_flow.py`, directly after `switch_discussion_method`:

```python
def refresh_ai_configs(discussion: Discussion, db: Database) -> None:
    """Reload each AI member's profile from the DB onto the live objects.

    Profile edits made while a discussion is loaded (e.g. fixing a
    non-tool-capable model from the blocked-switch recovery dialog)
    only touch the database row; the in-memory Entity keeps the
    AIConfig snapshot taken when it joined.  Swap in a fresh snapshot,
    preserving entity identity and roster order.  Rows that no longer
    exist are skipped.
    """
    for entity in discussion.entities:
        if entity.entity_type != EntityType.AI:
            continue
        row = db.get_entity(entity.id)
        if not row:
            continue
        entity.ai_config = Entity.from_db_row(row).ai_config


def retry_method_switch(
    discussion: Discussion, db: Database,
    get_state_fn: Callable[[], dict],
) -> dict:
    """Retry a Triage handoff blocked by the tool-capability gate.

    Refreshes AI members' profiles from the DB (so a model fix made in
    the UI takes effect), re-runs the switch, and resumes the paused
    discussion on success (spec 2026-07-17).  Returns the same
    ``method_switched`` shape ``complete_turn`` produces for an
    unblocked handoff, the ``method_switch_blocked`` shape when still
    blocked, or an error dict when there is nothing to retry.
    """
    if not discussion.id:
        return {"error": "No active discussion"}
    if discussion.status == "concluded":
        return {"error": "Discussion is already concluded"}
    if discussion.discussion_method != "triage":
        return {"error": "No pending method switch to retry"}
    pending = discussion.method_state.get("_pending_method_switch")
    if not pending:
        return {"error": "No pending method switch to retry"}

    refresh_ai_configs(discussion, db)
    chosen = pending["target_method"]
    switch_result = switch_discussion_method(discussion, db, chosen)
    if "error" in switch_result:
        switch_error = switch_result["error"]
        blocked_entities = switch_result.get("blocked_entities", [])
        logger.warning(
            "Retry of method switch for discussion %s to %r still "
            "blocked: %s", discussion.id, chosen, switch_error,
        )
        # Keep the pending record fresh for the dialog; the transcript
        # notice is NOT reposted (same target — _switch_error_posted).
        discussion.method_state["_pending_method_switch"] = {
            "target_method": chosen,
            "switch_error": switch_error,
            "blocked_entities": blocked_entities,
        }
        db.update_discussion(
            discussion.id,
            method_state=serialize_method_state(discussion.method_state),
        )
        return {
            "method_switch_blocked": True,
            "switch_error": switch_error,
            "target_method": chosen,
            "blocked_entities": blocked_entities,
            "turn_number": discussion.turn_number,
            "current_round": discussion.current_round,
            "state": get_state_fn(),
        }

    # Success. _pending_method_switch was wiped by init_state — it is
    # deliberately NOT in switch_discussion_method's preserved set.
    if discussion.status == "paused":
        resume_discussion(discussion, db)
    # Mirror complete_turn's successful-handoff path: the new method's
    # first phase reorders turns from the full roster.
    apply_method_turn_order(discussion, reset_index=True)
    stamp_turn_index(discussion)
    db.update_discussion(
        discussion.id,
        method_state=serialize_method_state(discussion.method_state),
    )
    return {
        "method_switched": True,
        "new_method": switch_result,
        "turn_number": discussion.turn_number,
        "current_round": discussion.current_round,
        "state": get_state_fn(),
    }
```

In `consensus/app.py`, after `resume_discussion` (line 885):

```python
    def retry_method_switch(self) -> dict:
        """Retry a Triage method handoff blocked by the tool gate."""
        result = app_discussion_flow.retry_method_switch(
            self.discussion, self.db, self.get_state,
        )
        self._notify()
        return result
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_app_discussion_flow.py tests/test_app.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add consensus/app_discussion_flow.py consensus/app.py tests/test_app_discussion_flow.py tests/test_app.py
git commit -m "feat(methods): retry_method_switch recovers a blocked triage handoff"
```

---

### Task 6: API exposure — bridge, REST, api.js

**Files:**
- Modify: `consensus/desktop.py` (after `resume_discussion`, line 498)
- Modify: `consensus/server.py` (RPC map, after the `"resume_discussion"` entry at line ~408)
- Modify: `consensus/static/api.js` (both transport sections)

**Interfaces:**
- Consumes: `ConsensusApp.retry_method_switch()` (Task 5).
- Produces: `api.retryMethodSwitch()` in JS (both pywebview and REST transports); RPC method name `retry_method_switch`.

- [ ] **Step 1: Implement (no unit-test harness covers these one-line registrations; the full suite guards regressions)**

`consensus/desktop.py`, after `resume_discussion` (line 498):

```python
    def retry_method_switch(self) -> dict:
        """Retry a Triage method handoff blocked by the tool gate."""
        return self.app.retry_method_switch()
```

`consensus/server.py`, in the RPC map directly after the `"resume_discussion"` entry:

```python
            "retry_method_switch": lambda: app.retry_method_switch(),
```

`consensus/static/api.js`: add next to the existing `resumeDiscussion` entries in **both** sections —

pywebview section:

```javascript
    async retryMethodSwitch() { return await window.pywebview.api.retry_method_switch(); }
```

REST section:

```javascript
    async retryMethodSwitch() { return await this._post('retry_method_switch'); }
```

(If the pywebview section has no `resumeDiscussion` line, anchor on `pauseDiscussion`/`completeTurn`; keep each new line's style identical to its neighbours.)

- [ ] **Step 2: Sanity-check**

Run: `uv run python -c "import consensus.desktop, consensus.server"` — expected: no output, exit 0.
Run: `uv run pytest tests/test_server.py -v` if that file exists (`ls tests/test_server*.py`); otherwise skip.

- [ ] **Step 3: Commit**

```bash
git add consensus/desktop.py consensus/server.py consensus/static/api.js
git commit -m "feat(api): expose retry_method_switch on bridge and REST"
```

---

### Task 7: Frontend recovery dialog

**Files:**
- Create: `consensus/static/method-switch.js`
- Modify: `consensus/static/index.html` (new dialog markup, after the reassign dialog)
- Modify: `consensus/static/discussion-actions.js:154-177` (`handleTurnLimitFlags`)
- Modify: `consensus/static/app.js` (imports, listener wiring, reconnect check at line ~245)

**Interfaces:**
- Consumes: `api.retryMethodSwitch()` (Task 6); backend shapes from Tasks 3/5; `state.saved_entities` rows (`id`, `name`, `entity_type`, `avatar_color`, `provider_id`, `model`, `temperature`, `max_tokens`, `system_prompt`).
- Produces: `initMethodSwitchDialog({onConclude, processCurrentTurn})` and `showSwitchBlockedDialog(data)` exported from `method-switch.js`, where `data` is `{target_method, switch_error, blocked_entities}`.

- [ ] **Step 1: Add the dialog markup**

In `consensus/static/index.html`, after the reassign dialog's closing `</div>` (line ~636), insert:

```html
    <!-- ===== METHOD SWITCH BLOCKED DIALOG ===== -->
    <div id="switch-blocked-dialog" class="dialog-overlay hidden">
        <div class="dialog">
            <h3>Method Switch Blocked</h3>
            <p id="switch-blocked-error" class="text-muted"></p>
            <p class="text-muted">Assign a tool-capable model to each
               participant below, then retry — or conclude the
               discussion.</p>
            <div id="switch-blocked-list"></div>
            <div class="dialog-actions">
                <button id="switch-blocked-conclude-btn" class="btn btn-ghost">Conclude Discussion</button>
                <button id="switch-blocked-retry-btn" class="btn btn-primary">Retry Switch</button>
            </div>
        </div>
    </div>
```

(No new CSS: `dialog-overlay`, `dialog`, `form-group`, `dialog-actions`, `text-muted` all exist and use relative sizing.)

- [ ] **Step 2: Create `consensus/static/method-switch.js`**

```javascript
/**
 * @module method-switch
 * Recovery dialog for a Triage method handoff blocked by the
 * tool-capability gate (spec 2026-07-17): the user assigns a
 * tool-capable model to each offending participant and retries the
 * switch, or concludes the discussion. The backend keeps the
 * discussion paused while this dialog waits.
 */

import { $, show, hide, showToast, escHtml, TOAST_WARNING_DURATION_MS } from './utils.js';
import { state, onStateUpdate } from './state.js';
import { api } from './api.js';
import { renderDiscussion } from './discussion.js';

// Injected by initMethodSwitchDialog — avoids a circular import with
// discussion-actions.js (mirrors initApi's callback pattern).
let deps = { onConclude: null, processCurrentTurn: null };

// The blocked entities currently shown, as sent by the backend.
let currentBlocked = [];

/**
 * Wire the dialog's static buttons. Call once at app init.
 * @param {object} injected - {onConclude, processCurrentTurn}
 */
export function initMethodSwitchDialog(injected) {
    deps = injected;
    $('#switch-blocked-retry-btn').addEventListener('click', onRetrySwitch);
    $('#switch-blocked-conclude-btn').addEventListener('click', async () => {
        hide('#switch-blocked-dialog');
        await deps.onConclude();
    });
}

/**
 * Populate one participant row's model dropdown from its provider.
 * Mirrors profiles.js loadModelsForProvider, with per-row elements.
 * @param {object} row - saved_entities row for the blocked participant
 * @param {HTMLSelectElement} select
 * @param {HTMLInputElement} custom
 */
async function loadModels(row, select, custom) {
    select.innerHTML = '<option value="">Loading models...</option>';
    let models = [];
    try {
        models = await api.fetchModels(row.provider_id);
    } catch (e) { /* provider offline — the custom input still works */ }
    if (models && models.length > 0) {
        select.innerHTML =
            '<option value="">-- Select Model --</option>' +
            models.map(m =>
                `<option value="${escHtml(m)}" ${m === row.model ? 'selected' : ''}>${escHtml(m)}</option>`
            ).join('');
    } else {
        select.innerHTML = '<option value="">No models found</option>';
        custom.value = row.model || '';
    }
}

/**
 * Show (or refresh) the recovery dialog.
 * @param {object} data - {target_method, switch_error, blocked_entities}
 */
export async function showSwitchBlockedDialog(data) {
    currentBlocked = data.blocked_entities || [];
    $('#switch-blocked-error').textContent = data.switch_error || '';
    const list = $('#switch-blocked-list');
    list.innerHTML = currentBlocked.map(b => `
        <div class="form-group" data-entity-id="${b.entity_id}">
            <label for="switch-model-select-${b.entity_id}">
                ${escHtml(b.name)} — current model: ${escHtml(b.model)}
            </label>
            <select id="switch-model-select-${b.entity_id}"></select>
            <input id="switch-model-custom-${b.entity_id}" type="text"
                   placeholder="Or type a model name"
                   style="margin-top:0.25rem">
        </div>
    `).join('');
    show('#switch-blocked-dialog');
    for (const b of currentBlocked) {
        const row = (state.saved_entities || []).find(e => e.id === b.entity_id);
        if (!row) continue;
        await loadModels(
            row,
            $(`#switch-model-select-${b.entity_id}`),
            $(`#switch-model-custom-${b.entity_id}`),
        );
    }
}

/**
 * Save changed models to the entity profiles, then retry the switch.
 */
async function onRetrySwitch() {
    const retryBtn = $('#switch-blocked-retry-btn');
    retryBtn.disabled = true;
    try {
        for (const b of currentBlocked) {
            const row = (state.saved_entities || []).find(e => e.id === b.entity_id);
            if (!row) continue;
            const custom = $(`#switch-model-custom-${b.entity_id}`);
            const select = $(`#switch-model-select-${b.entity_id}`);
            const newModel = (custom.value.trim() || select.value);
            if (!newModel || newModel === row.model) continue;
            await api.saveEntity({
                name: row.name,
                entity_type: row.entity_type,
                avatar_color: row.avatar_color,
                provider_id: row.provider_id,
                model: newModel,
                temperature: row.temperature,
                max_tokens: row.max_tokens,
                system_prompt: row.system_prompt || '',
                entity_id: row.id,
            });
        }
        const result = await api.retryMethodSwitch();
        if (result?.method_switched) {
            hide('#switch-blocked-dialog');
            if (result.state) onStateUpdate(result.state);
            else onStateUpdate(await api.getState());
            showToast('Method switched to '
                + (result.new_method?.display_name || 'the chosen method'));
            renderDiscussion();
            deps.processCurrentTurn();
        } else if (result?.method_switch_blocked) {
            if (result.state) onStateUpdate(result.state);
            showToast('Switch still blocked: ' + result.switch_error,
                TOAST_WARNING_DURATION_MS, 'warning');
            await showSwitchBlockedDialog(result);
        } else if (result?.error) {
            showToast(result.error);
        }
    } catch (e) {
        showToast('Retry failed: ' + e.message);
    } finally {
        retryBtn.disabled = false;
    }
}
```

Before writing, confirm `TOAST_WARNING_DURATION_MS` is exported from `utils.js` (discussion-actions.js already imports it); if the export name differs, match it.

- [ ] **Step 3: Route the new flag in `discussion-actions.js`**

Add the import at the top of `consensus/static/discussion-actions.js`:

```javascript
import { showSwitchBlockedDialog } from './method-switch.js';
```

In `handleTurnLimitFlags` (line 154), insert a branch before the `method_complete` check, and simplify the `method_complete` branch (the backend no longer pairs it with `switch_error`):

```javascript
    if (result?.method_switch_blocked) {
        // Blocked triage handoff: the backend paused the discussion;
        // the dialog lets the user fix the model and retry.
        renderDiscussion();
        showSwitchBlockedDialog(result);
        return true;
    }
    if (result?.method_complete) {
        renderDiscussion();
        showToast('All method phases complete — concluding discussion');
        await onConclude();
        return true;
    }
```

Also update the function's docstring line "or an exhausted discussion method" to mention the blocked-switch pause path.

- [ ] **Step 4: Wire init + reconnect in `app.js`**

Add to the import block:

```javascript
import { initMethodSwitchDialog, showSwitchBlockedDialog } from './method-switch.js';
```

Ensure `processCurrentTurn` is included in the existing `./discussion-actions.js` import list (line 15); add it if absent.

In `init()`'s listener-wiring section (near the `#cost-limit-*` listeners, line ~137):

```javascript
    initMethodSwitchDialog({ onConclude, processCurrentTurn });
```

In the initial-state load (line ~241-246), after the `pending_user_input` check:

```javascript
        // Re-show the blocked-switch recovery dialog after a reload
        if (s && s.pending_method_switch) showSwitchBlockedDialog(s.pending_method_switch);
```

- [ ] **Step 5: Verify**

- Run: `uv run pytest` (full suite) — expected: all pass (frontend changes cannot break Python tests; this catches accidental backend edits).
- Static JS sanity: `node --check consensus/static/method-switch.js` if node is available (`command -v node`); otherwise re-read the diff for syntax errors.
- Grep the wiring: `grep -n "retryMethodSwitch\|method_switch_blocked\|pending_method_switch\|initMethodSwitchDialog" consensus/static/*.js consensus/server.py consensus/desktop.py` — every producer/consumer pair from the Interfaces blocks must appear.

- [ ] **Step 6: Commit**

```bash
git add consensus/static/method-switch.js consensus/static/index.html consensus/static/discussion-actions.js consensus/static/app.js
git commit -m "feat(ui): blocked method switch recovery dialog"
```

---

### Task 8: Docs, full suite, handover

**Files:**
- Modify: `HANDOVER.md` (drop the "Blocked Triage switch" UX-gap section; add a merged-work row and any new deferred notes)
- Modify: `ROADMAP.md` (add a ✅ row under **Reliability**: blocked Triage handoff recovery)

**Interfaces:** none (documentation only).

- [ ] **Step 1: Update HANDOVER.md**

- Remove the "UX gap (older follow-up)" section (the feature now exists).
- Add a row to the "What is done" table: `| Blocked Triage switch recovery (pause + retry) | — (HANDOVER UX gap) | #<PR> |` (fill the PR number after opening it; use the next task's PR).
- Update the test-count line with the real number from the final suite run.
- Add one "Conventions" bullet: `_pending_method_switch` is internal method_state bookkeeping, deliberately NOT in `switch_discussion_method`'s preserved set — a successful switch must wipe it.

- [ ] **Step 2: Update ROADMAP.md**

Add under **Reliability**:

```markdown
| ✅ Done | Blocked method-switch recovery | A Triage handoff rejected by the tool-capability gate pauses the discussion and shows a recovery dialog (fix the offending participant's model → retry via `retry_method_switch`, or conclude) instead of auto-concluding. Pending switch survives reload via `method_state` + `get_state` |
```

- [ ] **Step 3: Full suite**

Run: `uv run pytest`
Expected: all tests pass (baseline 2406 + new tests from Tasks 1-5). Record the final count for HANDOVER.md.

- [ ] **Step 4: Commit**

```bash
git add HANDOVER.md ROADMAP.md
git commit -m "docs: record blocked-switch recovery in HANDOVER and ROADMAP"
```

- [ ] **Step 5: Push and open the PR**

```bash
git push -u origin claude/triage-switch-recovery
gh pr create --title "feat: blocked Triage method-switch recovery (pause + fix model + retry)" --body "$(cat <<'EOF'
## Summary
- A Triage handoff blocked by the issue-#23 tool-capability gate no longer auto-concludes the discussion: it pauses, records a pending switch, and surfaces a recovery dialog.
- The user assigns a tool-capable model to the offending participant(s) (profile update) and retries via the new `retry_method_switch` endpoint, which refreshes in-memory AI configs from the DB and resumes on success; "Conclude discussion" remains the fallback.
- Reconnection-safe: the pending switch is persisted in `method_state` and exposed as `pending_method_switch` in `get_state`.

Spec: `docs/superpowers/specs/2026-07-17-triage-blocked-switch-recovery-design.md`
Plan: `docs/superpowers/plans/2026-07-17-triage-blocked-switch-recovery.md`

## Test plan
- [ ] `uv run pytest` (full suite)
- [ ] New coverage: structured offender list, blocked-branch pause + persistence, `get_state` exposure, retry success/failure through the real `complete_turn` pipeline

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Then backfill the PR number in HANDOVER.md's table row, amend or add a tiny commit, and push.
