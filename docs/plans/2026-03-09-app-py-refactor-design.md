# Design: Refactor app.py into Pure-Function Domain Modules

**Date:** 2026-03-09
**Status:** Approved

## Problem

`app.py` is 1450 lines with ~60 methods in a single `ConsensusApp` class. Logic for distinct domains (providers, entities, discussion setup, discussion flow, discussion state) is interleaved, making it hard to test, maintain, and extend.

## Approach

Extract domain logic into pure functions in separate modules. `ConsensusApp` remains the thin orchestrator — each method becomes 3-5 lines that validate, delegate, and notify.

Functions receive explicit parameters (`Discussion`, `Database`, `PricingCache`, etc.) rather than reaching into `self.*`. Functions mutate `Discussion` in-place (it's already a mutable dataclass) and call `db` methods directly. This is pragmatic for now; a future architecture change (participant-driven context loading, see ROADMAP.md) will move toward immutable state.

## New Module Structure

```
consensus/
  app.py                  (~250 lines) — thin ConsensusApp orchestrator
  app_providers.py        (~80 lines)  — provider CRUD + key management
  app_entities.py         (~50 lines)  — entity profile CRUD
  app_discussion_setup.py (~200 lines) — add/remove members, roles, DA logic, start_discussion
  app_discussion_flow.py  (~350 lines) — generate_ai_turn, complete_turn, mediate, conclude
  app_discussion_state.py (~200 lines) — load, export, pause, resume, reopen, reset
  pricing.py              (modified)   — add calculate_cost_with_refresh() method
```

## What Stays in app.py

- `__init__`, `shutdown`
- Event system: `on`, `off`, `emit`, `_notify`, `set_update_callback`
- `get_state`
- BYOK context var management (`set_request_api_keys`, `clear_request_api_keys`)
- API key resolution (`_resolve_key_for_moderator`, `resolve_provider_api_key`)
- Prompt CRUD (3 thin methods wrapping DB)
- Tool CRUD (4 thin methods wrapping DB)
- MCP/expert management (~180 lines — candidate for future extraction)

## Extracted Modules

### app_providers.py

Pure functions for provider management:

- `add_provider(db, name, base_url, api_key_env, api_key) -> Optional[dict]`
- `update_provider(db, provider_id, api_key, **kwargs) -> bool`
- `delete_provider(db, provider_id) -> bool`
- `get_providers(db, request_api_keys) -> list[dict]`
- `fetch_models(db, provider_id, resolve_key_fn) -> list[str]` (async)
- `provider_for_frontend(provider, request_api_keys) -> Optional[dict]` (helper)

### app_entities.py

Pure functions for entity profile CRUD:

- `save_entity(db, name, entity_type, ...) -> Optional[dict]`
- `delete_entity(db, entity_id) -> dict`
- `reactivate_entity(db, entity_id) -> bool`
- `get_entities(db) -> list[dict]`
- `get_inactive_entities(db) -> list[dict]`

### app_discussion_setup.py

Functions that mutate `Discussion` and persist to DB:

- `add_to_discussion(discussion, db, entity_id, is_moderator, also_participant, participant_role) -> dict`
- `remove_from_discussion(discussion, db, entity_id) -> dict | bool`
- `set_moderator(discussion, entity_id, also_participant) -> bool`
- `set_topic(discussion, topic) -> bool`
- `set_participant_role(discussion, db, entity_id, participant_role) -> dict`
- `start_discussion(discussion, db, moderator_obj, moderator_participates, max_rounds) -> dict`
- `_auto_assign_da_tools(db, entity_id) -> None` (private helper)
- `_reorder_da_in_turn_order(discussion) -> None` (private helper)

### app_discussion_flow.py

Async functions for active discussion operations:

- `generate_ai_turn(discussion, moderator, db, pricing, notify_fn) -> dict`
- `complete_turn(discussion, moderator, db, pricing, notify_fn, moderator_summary) -> dict`
- `mediate(discussion, moderator, db, pricing, notify_fn, context) -> dict`
- `conclude_discussion(discussion, moderator, db, pricing, notify_fn) -> dict`
- `reassign_turn(moderator, entity_id, notify_fn) -> dict`
- `submit_human_message(discussion, db, entity_id, content, notify_fn) -> dict`
- `submit_moderator_message(discussion, db, notify_fn, content) -> dict`
- `_is_pass(content) -> bool` (moved from module level)

### app_discussion_state.py

Functions for discussion state management:

- `get_export_data(db, discussion_id) -> dict`
- `load_discussion(db, discussion_id, key_resolver, tool_registry) -> tuple[Discussion, Moderator] | dict`
- `delete_discussions(db, discussion_ids) -> dict`
- `restore_discussion(db, discussion_id) -> dict`
- `pause_discussion(discussion, db) -> dict`
- `resume_discussion(discussion, db) -> dict`
- `reopen_discussion(discussion, db) -> dict`
- `reset(db, key_resolver, tool_registry) -> tuple[Discussion, Moderator]`

## PricingCache Change

Add one method to `PricingCache`:

```python
def calculate_cost_with_refresh(
    self, model_name: str, base_url: str,
    prompt_tokens: int, completion_tokens: int,
) -> Optional[float]:
    """Calculate cost, refreshing pricing data once if model is unknown."""
    cost = self.calculate_cost(model_name, base_url, prompt_tokens, completion_tokens)
    if cost is None and self.needs_refresh_for_model(model_name):
        self.refresh()
        cost = self.calculate_cost(model_name, base_url, prompt_tokens, completion_tokens)
    return cost
```

All 4 duplicated cost-calculation blocks in `app.py` replaced with single calls to this method.

## Caller Impact

**None.** `server.py`, `desktop.py`, and `session.py` all call `ConsensusApp` methods, which retain their exact same signatures. The refactoring is entirely internal.

## Testing Strategy

- Each new module gets its own test file (`test_app_providers.py`, etc.)
- Pure functions are tested with real `Discussion()` objects and mock/in-memory DB
- Existing `test_app.py` (493 lines) continues to work as integration tests
- Type hints and docstrings mandatory on all extracted functions

## Coding Standards

- Type hints on all function signatures and return types
- Docstrings on all public functions
- No magic numbers
- Relative units in any CSS (not applicable here)
