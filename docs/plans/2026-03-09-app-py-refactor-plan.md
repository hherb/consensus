# app.py Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Split the 1450-line `ConsensusApp` class into pure-function domain modules while preserving all existing behavior and API signatures.

**Architecture:** Extract logic into 5 domain modules (`app_providers`, `app_entities`, `app_discussion_setup`, `app_discussion_flow`, `app_discussion_state`) containing functions that receive explicit parameters. `ConsensusApp` becomes a thin orchestrator delegating to these modules. Add `calculate_cost_with_refresh()` to `PricingCache` to eliminate 4x duplicated cost-calculation pattern.

**Tech Stack:** Python 3.12, pytest, SQLite, asyncio, httpx

**Design doc:** `docs/plans/2026-03-09-app-py-refactor-design.md`

---

### Task 1: Add `calculate_cost_with_refresh()` to PricingCache

**Files:**
- Modify: `consensus/pricing.py:191-203` (add method after `calculate_cost`)
- Test: `tests/test_pricing.py` (create)

**Step 1: Write the failing test**

Create `tests/test_pricing.py`:

```python
"""Tests for consensus.pricing — PricingCache cost calculation."""

import sqlite3
from unittest.mock import patch

import pytest

from consensus.pricing import PricingCache


@pytest.fixture
def pricing_cache(tmp_path):
    """Create a PricingCache with a temporary database."""
    db_path = str(tmp_path / "pricing_test.db")
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS model_pricing "
        "(id INTEGER PRIMARY KEY, model_id TEXT, prompt_cost REAL, "
        "completion_cost REAL, fetched_at REAL)"
    )
    conn.commit()
    cache = PricingCache(conn, conn.cursor())
    return cache


class TestCalculateCostWithRefresh:
    """Tests for the calculate_cost_with_refresh convenience method."""

    def test_returns_cost_when_model_known(self, pricing_cache):
        """Should return cost directly without refreshing if model is known."""
        import time
        pricing_cache.conn.execute(
            "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            ("test-model", 0.001, 0.002, time.time()),
        )
        pricing_cache.conn.commit()

        cost = pricing_cache.calculate_cost_with_refresh(
            "test-model", "", 100, 50,
        )
        assert cost is not None
        assert abs(cost - (100 * 0.001 + 50 * 0.002)) < 1e-10

    def test_returns_none_when_model_unknown_and_refresh_fails(self, pricing_cache):
        """Should return None if model unknown and refresh doesn't help."""
        with patch.object(pricing_cache, "refresh", return_value=False):
            cost = pricing_cache.calculate_cost_with_refresh(
                "unknown-model", "", 100, 50,
            )
        assert cost is None

    def test_refreshes_and_retries_when_model_unknown(self, pricing_cache):
        """Should refresh pricing data and retry when model initially unknown."""
        import time
        call_count = 0
        original_calculate = pricing_cache.calculate_cost

        def mock_calculate(model, url, pt, ct):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # First call: unknown
            return original_calculate(model, url, pt, ct)

        # Insert pricing so second call succeeds
        pricing_cache.conn.execute(
            "INSERT INTO model_pricing (model_id, prompt_cost, completion_cost, fetched_at) "
            "VALUES (?, ?, ?, ?)",
            ("new-model", 0.001, 0.002, time.time()),
        )
        pricing_cache.conn.commit()

        with patch.object(pricing_cache, "calculate_cost", side_effect=mock_calculate):
            with patch.object(pricing_cache, "needs_refresh_for_model", return_value=True):
                with patch.object(pricing_cache, "refresh", return_value=True):
                    cost = pricing_cache.calculate_cost_with_refresh(
                        "new-model", "", 100, 50,
                    )
        assert call_count == 2
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_pricing.py -v`
Expected: FAIL — `AttributeError: 'PricingCache' object has no attribute 'calculate_cost_with_refresh'`

**Step 3: Write minimal implementation**

Add to `consensus/pricing.py` after the `calculate_cost` method (after line 203):

```python
    def calculate_cost_with_refresh(
        self, model_name: str, base_url: str,
        prompt_tokens: int, completion_tokens: int,
    ) -> Optional[float]:
        """Calculate cost in USD, refreshing pricing data once if model is unknown.

        Wraps ``calculate_cost`` with a single retry after refreshing the
        pricing cache when the model is not yet known.

        Args:
            model_name: The model identifier (e.g. "gpt-4o").
            base_url: The provider's API base URL for disambiguation.
            prompt_tokens: Number of prompt/input tokens used.
            completion_tokens: Number of completion/output tokens used.

        Returns:
            Cost in USD, or None if pricing is unavailable even after refresh.
        """
        cost = self.calculate_cost(model_name, base_url, prompt_tokens, completion_tokens)
        if cost is None and self.needs_refresh_for_model(model_name):
            self.refresh()
            cost = self.calculate_cost(model_name, base_url, prompt_tokens, completion_tokens)
        return cost
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_pricing.py -v`
Expected: 3 passed

**Step 5: Run full test suite for regression**

Run: `cd /Users/hherb/src/consensus && python -m pytest --tb=short -q`
Expected: All existing tests pass

**Step 6: Commit**

```bash
git add consensus/pricing.py tests/test_pricing.py
git commit -m "feat(pricing): add calculate_cost_with_refresh to eliminate duplication"
```

---

### Task 2: Extract `app_providers.py`

**Files:**
- Create: `consensus/app_providers.py`
- Modify: `consensus/app.py:371-435` (replace with delegation)
- Test: `tests/test_app_providers.py` (create)

**Step 1: Write the failing tests**

Create `tests/test_app_providers.py`:

```python
"""Tests for consensus.app_providers — provider CRUD and key management."""

import pytest

from consensus.app_providers import (
    add_provider,
    delete_provider,
    fetch_models,
    get_providers,
    provider_for_frontend,
    update_provider,
)


class TestProviderForFrontend:
    """Tests for redacting provider secrets before sending to frontend."""

    def test_returns_none_for_none(self):
        assert provider_for_frontend(None, {}) is None

    def test_redacts_api_key_env(self):
        p = {"id": 1, "name": "Test", "api_key_env": "SECRET_KEY"}
        result = provider_for_frontend(p, {})
        assert "api_key_env" not in result
        assert result["has_key"] is False

    def test_has_key_true_when_env_set(self):
        p = {"id": 1, "name": "Test", "api_key_env": "MY_KEY"}
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("MY_KEY", "secret123")
            result = provider_for_frontend(p, {})
        assert result["has_key"] is True

    def test_has_key_true_when_byok(self):
        p = {"id": 1, "name": "Test", "api_key_env": ""}
        result = provider_for_frontend(p, {"1": "user-key"})
        assert result["has_key"] is True


class TestAddProvider:
    def test_add_provider_basic(self, tmp_db):
        result = add_provider(tmp_db, "MyProvider", "http://localhost/v1")
        assert result is not None
        assert result["name"] == "MyProvider"
        assert "api_key_env" not in result

    def test_add_provider_with_key(self, tmp_db, tmp_path):
        """Adding a provider with api_key saves to env file."""
        result = add_provider(
            tmp_db, "KeyedProvider", "http://localhost/v1",
            api_key_env="TEST_KEY", api_key="secret",
        )
        assert result is not None


class TestUpdateProvider:
    def test_update_nonexistent_returns_false(self, tmp_db):
        assert update_provider(tmp_db, 9999) is False

    def test_update_existing_provider(self, tmp_db):
        pid = tmp_db.add_provider("P", "http://x", "")
        assert update_provider(tmp_db, pid, name="Updated") is True
        p = tmp_db.get_provider(pid)
        assert p["name"] == "Updated"


class TestDeleteProvider:
    def test_delete_provider(self, tmp_db):
        pid = tmp_db.add_provider("P", "http://x", "")
        assert delete_provider(tmp_db, pid) is True


class TestGetProviders:
    def test_get_providers_redacted(self, tmp_db):
        tmp_db.add_provider("P1", "http://x", "SECRET")
        providers = get_providers(tmp_db, {})
        assert len(providers) >= 1
        for p in providers:
            assert "api_key_env" not in p
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_app_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'consensus.app_providers'`

**Step 3: Write the implementation**

Create `consensus/app_providers.py`:

```python
"""Provider management — CRUD operations and key redaction.

Pure functions that operate on a Database instance and return
frontend-safe dicts. No dependency on ConsensusApp.
"""

from typing import Optional

from .ai_client import AIClient
from .config import has_api_key, remove_api_key, save_api_key
from .database import Database

# Sentinel value for key removal in update_provider
_REMOVE_KEY_SENTINEL = "__REMOVE__"


def provider_for_frontend(
    provider: Optional[dict],
    request_api_keys: dict[str, str],
) -> Optional[dict]:
    """Redact secrets before sending provider data to the frontend.

    Args:
        provider: Raw provider dict from the database, or None.
        request_api_keys: Per-request BYOK keys mapping provider_id -> key.

    Returns:
        A copy of the provider dict with ``api_key_env`` removed and
        ``has_key`` set to True if a key is available via env or BYOK.
        Returns None if provider is None.
    """
    if not provider:
        return None
    p = dict(provider)
    env_var = p.get("api_key_env") or ""
    provider_id = p.get("id", 0)
    has_env = has_api_key(env_var)
    has_byok = bool(request_api_keys.get(str(provider_id), ""))
    p["has_key"] = has_env or has_byok
    p.pop("api_key_env", None)
    return p


def add_provider(
    db: Database,
    name: str,
    base_url: str,
    api_key_env: str = "",
    api_key: str = "",
    request_api_keys: Optional[dict[str, str]] = None,
) -> Optional[dict]:
    """Add a new API provider and return its frontend-safe data.

    If *api_key* is provided alongside *api_key_env*, the key is saved
    to the env file and only the env var name is stored in the database.

    Args:
        db: Database instance.
        name: Display name for the provider.
        base_url: The provider's API base URL.
        api_key_env: Environment variable name for the API key.
        api_key: The actual API key value (saved to env file if provided).
        request_api_keys: Per-request BYOK keys for frontend redaction.

    Returns:
        Frontend-safe provider dict, or None on failure.
    """
    if api_key and api_key_env:
        save_api_key(api_key_env, api_key)
    pid = db.add_provider(name, base_url, api_key_env)
    return provider_for_frontend(db.get_provider(pid), request_api_keys or {})


def update_provider(
    db: Database,
    provider_id: int,
    api_key: str = "",
    **kwargs: object,
) -> bool:
    """Update an existing provider's fields.

    If *api_key* is a non-empty string, the key is saved. If *api_key*
    is the sentinel ``"__REMOVE__"``, the stored key is deleted.

    Args:
        db: Database instance.
        provider_id: ID of the provider to update.
        api_key: New key value, removal sentinel, or empty to skip.
        **kwargs: Fields to update (name, base_url, api_key_env, etc.).

    Returns:
        True if the provider was found and updated, False otherwise.
    """
    provider = db.get_provider(provider_id)
    if not provider:
        return False
    env_var = kwargs.get("api_key_env") or provider["api_key_env"]
    if api_key == _REMOVE_KEY_SENTINEL and env_var:
        remove_api_key(env_var)
    elif api_key and env_var:
        save_api_key(env_var, api_key)
    db.update_provider(provider_id, **kwargs)
    return True


def delete_provider(db: Database, provider_id: int) -> bool:
    """Delete a provider by ID.

    Args:
        db: Database instance.
        provider_id: ID of the provider to delete.

    Returns:
        True (always succeeds).
    """
    db.delete_provider(provider_id)
    return True


def get_providers(
    db: Database,
    request_api_keys: dict[str, str],
) -> list[dict]:
    """Return all configured providers with secrets redacted.

    Args:
        db: Database instance.
        request_api_keys: Per-request BYOK keys for frontend redaction.

    Returns:
        List of frontend-safe provider dicts.
    """
    return [
        provider_for_frontend(p, request_api_keys)
        for p in db.get_providers()
    ]


async def fetch_models(
    db: Database,
    provider_id: int,
    resolve_key_fn: callable,
) -> list[str]:
    """Fetch available models from a provider's API.

    Args:
        db: Database instance.
        provider_id: ID of the provider to query.
        resolve_key_fn: Callable(provider_id, api_key_env) -> str that
            resolves the API key from env or BYOK.

    Returns:
        List of model name strings, or empty list if provider not found.
    """
    provider = db.get_provider(provider_id)
    if not provider:
        return []
    api_key = resolve_key_fn(provider_id, provider["api_key_env"] or "")
    async with AIClient(provider["base_url"], api_key) as client:
        return await client.list_models()
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_app_providers.py -v`
Expected: All passed

**Step 5: Wire into ConsensusApp**

In `consensus/app.py`, replace the provider methods (lines 371-435) with delegation:

```python
from . import app_providers

# Replace _provider_for_frontend, add_provider, update_provider,
# delete_provider, get_providers, fetch_models with thin wrappers:

def _provider_for_frontend(self, p: Optional[dict]) -> Optional[dict]:
    """Redact secrets before sending provider data to the frontend."""
    return app_providers.provider_for_frontend(p, _request_api_keys_var.get({}))

def add_provider(self, name: str, base_url: str,
                 api_key_env: str = "", api_key: str = "") -> Optional[dict]:
    """Add a new API provider and return its data."""
    return app_providers.add_provider(
        self.db, name, base_url, api_key_env, api_key,
        _request_api_keys_var.get({}),
    )

def update_provider(self, provider_id: int,
                    api_key: str = "", **kwargs: object) -> bool:
    """Update an existing provider's fields."""
    return app_providers.update_provider(self.db, provider_id, api_key, **kwargs)

def delete_provider(self, provider_id: int) -> bool:
    """Delete a provider by ID."""
    return app_providers.delete_provider(self.db, provider_id)

def get_providers(self) -> list[dict]:
    """Return all configured providers (keys redacted)."""
    return app_providers.get_providers(self.db, _request_api_keys_var.get({}))

async def fetch_models(self, provider_id: int) -> list[str]:
    """Fetch available models from a provider's API."""
    return await app_providers.fetch_models(
        self.db, provider_id, self.resolve_provider_api_key,
    )
```

**Step 6: Run full test suite for regression**

Run: `cd /Users/hherb/src/consensus && python -m pytest --tb=short -q`
Expected: All tests pass (including existing `test_app.py`)

**Step 7: Commit**

```bash
git add consensus/app_providers.py consensus/app.py tests/test_app_providers.py
git commit -m "refactor: extract provider management into app_providers.py"
```

---

### Task 3: Extract `app_entities.py`

**Files:**
- Create: `consensus/app_entities.py`
- Modify: `consensus/app.py:441-477` (replace with delegation)
- Test: `tests/test_app_entities.py` (create)

**Step 1: Write the failing tests**

Create `tests/test_app_entities.py`:

```python
"""Tests for consensus.app_entities — entity profile CRUD."""

import pytest

from consensus.app_entities import (
    delete_entity,
    get_entities,
    get_inactive_entities,
    reactivate_entity,
    save_entity,
)


class TestSaveEntity:
    def test_create_new_entity(self, tmp_db, sample_provider):
        result = save_entity(
            tmp_db, "TestBot", "ai",
            provider_id=sample_provider, model="test-model",
        )
        assert result is not None
        assert result["name"] == "TestBot"
        assert result["entity_type"] == "ai"

    def test_update_existing_entity(self, tmp_db, sample_provider):
        eid = tmp_db.add_entity("Old", "ai", "#fff", sample_provider, "m", 0.5, 512, "")
        result = save_entity(
            tmp_db, "New", "ai", entity_id=eid,
            provider_id=sample_provider, model="m",
        )
        assert result["name"] == "New"


class TestDeleteEntity:
    def test_delete_entity(self, tmp_db, sample_ai_entity):
        result = delete_entity(tmp_db, sample_ai_entity)
        assert isinstance(result, dict)


class TestGetEntities:
    def test_get_entities(self, tmp_db, sample_ai_entity):
        entities = get_entities(tmp_db)
        assert len(entities) >= 1

    def test_get_inactive_entities(self, tmp_db, sample_ai_entity):
        tmp_db.delete_entity(sample_ai_entity)
        inactive = get_inactive_entities(tmp_db)
        assert isinstance(inactive, list)


class TestReactivateEntity:
    def test_reactivate(self, tmp_db, sample_ai_entity):
        tmp_db.delete_entity(sample_ai_entity)
        result = reactivate_entity(tmp_db, sample_ai_entity)
        assert isinstance(result, bool)
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_app_entities.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'consensus.app_entities'`

**Step 3: Write the implementation**

Create `consensus/app_entities.py`:

```python
"""Entity profile management — CRUD operations.

Pure functions that operate on a Database instance. No dependency
on ConsensusApp.
"""

from typing import Optional

from .database import Database


def save_entity(
    db: Database,
    name: str,
    entity_type: str,
    avatar_color: str = "#3b82f6",
    provider_id: int = 0,
    model: str = "",
    temperature: float = 0.7,
    max_tokens: int = 1024,
    system_prompt: str = "",
    entity_id: int = 0,
) -> Optional[dict]:
    """Create or update a persistent entity profile.

    Args:
        db: Database instance.
        name: Display name for the entity.
        entity_type: One of "human", "ai", or "expert".
        avatar_color: Hex color for the entity's avatar.
        provider_id: ID of the AI provider (0 for humans).
        model: Model name (empty for humans).
        temperature: Sampling temperature for AI entities.
        max_tokens: Maximum tokens for AI responses.
        system_prompt: Custom system prompt for AI entities.
        entity_id: If nonzero, update this entity instead of creating.

    Returns:
        The entity dict from the database, or None on failure.
    """
    if entity_id:
        db.update_entity(
            entity_id, name=name, entity_type=entity_type,
            avatar_color=avatar_color, provider_id=provider_id,
            model=model, temperature=temperature,
            max_tokens=max_tokens, system_prompt=system_prompt,
        )
    else:
        entity_id = db.add_entity(
            name, entity_type, avatar_color, provider_id,
            model, temperature, max_tokens, system_prompt,
        )
    return db.get_entity(entity_id)


def delete_entity(db: Database, entity_id: int) -> dict:
    """Delete or deactivate an entity profile by ID.

    Args:
        db: Database instance.
        entity_id: ID of the entity to delete.

    Returns:
        Result dict from the database operation.
    """
    return db.delete_entity(entity_id)


def reactivate_entity(db: Database, entity_id: int) -> bool:
    """Reactivate a previously deactivated entity profile.

    Args:
        db: Database instance.
        entity_id: ID of the entity to reactivate.

    Returns:
        True if the entity was reactivated, False otherwise.
    """
    return db.reactivate_entity(entity_id)


def get_entities(db: Database) -> list[dict]:
    """Return all saved active entity profiles.

    Args:
        db: Database instance.

    Returns:
        List of entity dicts.
    """
    return db.get_entities()


def get_inactive_entities(db: Database) -> list[dict]:
    """Return all inactive (soft-deleted) entity profiles.

    Args:
        db: Database instance.

    Returns:
        List of inactive entity dicts.
    """
    return db.get_inactive_entities()
```

**Step 4: Run test to verify it passes**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_app_entities.py -v`
Expected: All passed

**Step 5: Wire into ConsensusApp**

Replace entity methods in `consensus/app.py` with thin delegation:

```python
from . import app_entities

def save_entity(self, name: str, entity_type: str, ...) -> Optional[dict]:
    """Create or update a persistent entity profile."""
    return app_entities.save_entity(
        self.db, name, entity_type, avatar_color, provider_id,
        model, temperature, max_tokens, system_prompt, entity_id,
    )

def delete_entity(self, entity_id: int) -> dict:
    """Delete or deactivate an entity profile by ID."""
    return app_entities.delete_entity(self.db, entity_id)

def reactivate_entity(self, entity_id: int) -> bool:
    """Reactivate a previously deactivated entity profile."""
    return app_entities.reactivate_entity(self.db, entity_id)

def get_entities(self) -> list[dict]:
    """Return all saved active entity profiles."""
    return app_entities.get_entities(self.db)

def get_inactive_entities(self) -> list[dict]:
    """Return all inactive (soft-deleted) entity profiles."""
    return app_entities.get_inactive_entities(self.db)
```

**Step 6: Run full test suite**

Run: `cd /Users/hherb/src/consensus && python -m pytest --tb=short -q`
Expected: All pass

**Step 7: Commit**

```bash
git add consensus/app_entities.py consensus/app.py tests/test_app_entities.py
git commit -m "refactor: extract entity management into app_entities.py"
```

---

### Task 4: Extract `app_discussion_setup.py`

**Files:**
- Create: `consensus/app_discussion_setup.py`
- Modify: `consensus/app.py:503-816` (replace with delegation)
- Test: `tests/test_app_discussion_setup.py` (create)

**Step 1: Write the failing tests**

Create `tests/test_app_discussion_setup.py`:

```python
"""Tests for consensus.app_discussion_setup — discussion setup and membership."""

import pytest

from consensus.app_discussion_setup import (
    add_to_discussion,
    auto_assign_da_tools,
    remove_from_discussion,
    reorder_da_in_turn_order,
    set_moderator,
    set_participant_role,
    set_topic,
    start_discussion,
)
from consensus.models import Discussion, Entity, EntityType, MessageRole
from consensus.moderator import Moderator


class TestAddToDiscussion:
    def test_add_entity(self, tmp_db, sample_ai_entity):
        disc = Discussion()
        result = add_to_discussion(disc, tmp_db, sample_ai_entity)
        assert "error" not in result
        assert len(disc.entities) == 1

    def test_duplicate_entity_returns_error(self, tmp_db, sample_ai_entity):
        disc = Discussion()
        add_to_discussion(disc, tmp_db, sample_ai_entity)
        result = add_to_discussion(disc, tmp_db, sample_ai_entity)
        assert "error" in result

    def test_nonexistent_entity_returns_error(self, tmp_db):
        disc = Discussion()
        result = add_to_discussion(disc, tmp_db, 9999)
        assert "error" in result

    def test_add_as_moderator(self, tmp_db, sample_ai_entity):
        disc = Discussion()
        add_to_discussion(disc, tmp_db, sample_ai_entity, is_moderator=True)
        assert disc.moderator_id == sample_ai_entity


class TestRemoveFromDiscussion:
    def test_remove_entity(self, tmp_db, sample_ai_entity, sample_human_entity):
        disc = Discussion()
        add_to_discussion(disc, tmp_db, sample_ai_entity, is_moderator=True)
        add_to_discussion(disc, tmp_db, sample_human_entity)
        result = remove_from_discussion(disc, tmp_db, sample_human_entity)
        assert result is True
        assert len(disc.entities) == 1


class TestSetTopic:
    def test_set_topic(self):
        disc = Discussion()
        assert set_topic(disc, "AI regulation") is True
        assert disc.topic == "AI regulation"


class TestSetModerator:
    def test_set_moderator(self, tmp_db, sample_ai_entity):
        disc = Discussion()
        add_to_discussion(disc, tmp_db, sample_ai_entity)
        assert set_moderator(disc, sample_ai_entity) is True
        assert disc.moderator_id == sample_ai_entity


class TestReorderDaTurnOrder:
    def test_da_moved_to_end(self):
        disc = Discussion(turn_order=[1, 2, 3], current_turn_index=0)
        disc.member_roles = {1: "standard", 2: "devils_advocate", 3: "standard"}
        reorder_da_in_turn_order(disc)
        assert disc.turn_order[-1] == 2

    def test_no_da_is_noop(self):
        disc = Discussion(turn_order=[1, 2, 3], current_turn_index=0)
        disc.member_roles = {1: "standard", 2: "standard", 3: "standard"}
        reorder_da_in_turn_order(disc)
        assert disc.turn_order == [1, 2, 3]
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_app_discussion_setup.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write the implementation**

Create `consensus/app_discussion_setup.py`. Extract the following methods as functions:

- `add_to_discussion(discussion, db, entity_id, ...) -> dict`
- `remove_from_discussion(discussion, db, entity_id) -> dict | bool`
- `set_moderator(discussion, entity_id, also_participant) -> bool`
- `set_topic(discussion, topic) -> bool`
- `set_participant_role(discussion, db, entity_id, participant_role) -> dict`
- `start_discussion(discussion, db, moderator_obj, moderator_participates, max_rounds) -> dict`
- `auto_assign_da_tools(db, entity_id) -> None`
- `reorder_da_in_turn_order(discussion) -> None`

Each function takes the same parameters as the original method but with `self.discussion` / `self.db` passed explicitly. Move `_is_pass` helper here too if needed for start, or keep it in `app_discussion_flow.py`.

The full implementation should be a direct extraction of lines 503-816 from `app.py`, converting `self.discussion` to `discussion` parameter and `self.db` to `db` parameter. The `_notify()` calls are removed from the extracted functions — the caller (`ConsensusApp`) handles notification.

Note: `start_discussion` needs a `get_state_fn` callback parameter (or returns a result that the caller wraps with state), since it currently calls `self.get_state()`. Simplest: have it return a success dict, and let `ConsensusApp.start_discussion` append the state.

**Step 4: Run tests**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_app_discussion_setup.py -v`
Expected: All passed

**Step 5: Wire into ConsensusApp and run full suite**

Run: `cd /Users/hherb/src/consensus && python -m pytest --tb=short -q`
Expected: All pass

**Step 6: Commit**

```bash
git add consensus/app_discussion_setup.py consensus/app.py tests/test_app_discussion_setup.py
git commit -m "refactor: extract discussion setup into app_discussion_setup.py"
```

---

### Task 5: Extract `app_discussion_flow.py`

**Files:**
- Create: `consensus/app_discussion_flow.py`
- Modify: `consensus/app.py:817-1082` (replace with delegation)
- Test: `tests/test_app_discussion_flow.py` (create)

**Step 1: Write the failing tests**

Create `tests/test_app_discussion_flow.py`. Key tests:

```python
"""Tests for consensus.app_discussion_flow — active discussion operations."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from consensus.app_discussion_flow import (
    is_pass,
    submit_human_message,
    submit_moderator_message,
)
from consensus.models import Discussion, Entity, EntityType, MessageRole


class TestIsPass:
    def test_pass_bracket(self):
        assert is_pass("[PASS]") is True

    def test_pass_plain(self):
        assert is_pass("PASS") is True

    def test_pass_formatted(self):
        assert is_pass("*Alice passed this round.*") is True

    def test_not_pass(self):
        assert is_pass("I think we should consider...") is False

    def test_pass_with_whitespace(self):
        assert is_pass("  [PASS]  ") is True

    def test_pass_with_markdown(self):
        assert is_pass("**[PASS]**") is True


class TestSubmitHumanMessage:
    def test_submit_message(self, tmp_db, discussion_with_entities):
        disc = discussion_with_entities
        # Set up a started discussion
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        speaker = disc.current_speaker
        result = submit_human_message(disc, tmp_db, speaker.id, "Hello world")
        assert "error" not in result
        assert result["content"] == "Hello world"

    def test_wrong_turn_returns_error(self, tmp_db, discussion_with_entities):
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        # Try to submit as entity that's not current speaker
        other = [e for e in disc.entities if e.id != disc.current_speaker.id][0]
        result = submit_human_message(disc, tmp_db, other.id, "Hello")
        assert "error" in result


class TestSubmitModeratorMessage:
    def test_submit_moderator_message(self, tmp_db, discussion_with_entities):
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        result = submit_moderator_message(disc, tmp_db, "Moderator says hello")
        assert "error" not in result

    def test_no_moderator_returns_error(self, tmp_db):
        disc = Discussion()
        result = submit_moderator_message(disc, tmp_db, "Hello")
        assert "error" in result
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_app_discussion_flow.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write the implementation**

Create `consensus/app_discussion_flow.py`. Extract:

- `is_pass(content) -> bool` (renamed from `_is_pass`, now public and testable)
- `submit_human_message(discussion, db, entity_id, content) -> dict`
- `submit_moderator_message(discussion, db, content) -> dict`
- `generate_ai_turn(discussion, moderator, db, pricing, emit_fn) -> dict` (async)
- `complete_turn(discussion, moderator, db, pricing, emit_fn, moderator_summary) -> dict` (async)
- `reassign_turn(moderator, entity_id) -> dict`
- `mediate(discussion, moderator, db, pricing, emit_fn, context) -> dict` (async)

Key change: all `self.db.pricing.calculate_cost(...)` + refresh pattern replaced with `pricing.calculate_cost_with_refresh(...)`.

Notification callbacks (`_notify`) are NOT called inside these functions. `ConsensusApp` calls `self._notify()` after delegation.

**Step 4: Run tests**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_app_discussion_flow.py -v`
Expected: All passed

**Step 5: Wire into ConsensusApp and run full suite**

Run: `cd /Users/hherb/src/consensus && python -m pytest --tb=short -q`
Expected: All pass

**Step 6: Commit**

```bash
git add consensus/app_discussion_flow.py consensus/app.py tests/test_app_discussion_flow.py
git commit -m "refactor: extract discussion flow into app_discussion_flow.py"
```

---

### Task 6: Extract `app_discussion_state.py`

**Files:**
- Create: `consensus/app_discussion_state.py`
- Modify: `consensus/app.py:1138-1414` (replace with delegation)
- Test: `tests/test_app_discussion_state.py` (create)

**Step 1: Write the failing tests**

Create `tests/test_app_discussion_state.py`:

```python
"""Tests for consensus.app_discussion_state — discussion state management."""

import time

import pytest

from consensus.app_discussion_state import (
    conclude_discussion,
    get_export_data,
    load_discussion,
    pause_discussion,
    reopen_discussion,
    reset_discussion,
    resume_discussion,
)
from consensus.models import Discussion, Entity, EntityType, MessageRole


class TestPauseDiscussion:
    def test_pause_active_discussion(self, tmp_db, discussion_with_entities):
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        result = pause_discussion(disc, tmp_db)
        assert "error" not in result
        assert disc.status == "paused"
        assert disc.is_active is False

    def test_pause_inactive_returns_error(self, tmp_db):
        disc = Discussion()
        result = pause_discussion(disc, tmp_db)
        assert "error" in result


class TestResumeDiscussion:
    def test_resume_paused_discussion(self, tmp_db, discussion_with_entities):
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        pause_discussion(disc, tmp_db)
        result = resume_discussion(disc, tmp_db)
        assert "error" not in result
        assert disc.status == "active"
        assert disc.is_active is True

    def test_resume_active_returns_error(self, tmp_db, discussion_with_entities):
        disc = discussion_with_entities
        did = tmp_db.create_discussion(disc.topic, disc.moderator_id)
        disc.id = did
        result = resume_discussion(disc, tmp_db)
        assert "error" in result


class TestGetExportData:
    def test_nonexistent_discussion(self, tmp_db):
        result = get_export_data(tmp_db, 9999)
        assert "error" in result


class TestResetDiscussion:
    def test_reset_returns_fresh_state(self, tmp_db):
        disc, mod = reset_discussion(tmp_db, lambda pid, env: "", None)
        assert disc.topic == ""
        assert disc.entities == []
```

**Step 2: Run test to verify it fails**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_app_discussion_state.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write the implementation**

Create `consensus/app_discussion_state.py`. Extract:

- `get_export_data(db, discussion_id) -> dict`
- `load_discussion(db, discussion_id, key_resolver, tool_registry) -> tuple[Discussion, Moderator] | dict`
- `pause_discussion(discussion, db) -> dict`
- `resume_discussion(discussion, db) -> dict`
- `reopen_discussion(discussion, db) -> dict`
- `conclude_discussion(discussion, moderator, db, pricing, emit_fn) -> dict` (async, moved from flow since it transitions state)
- `reset_discussion(db, key_resolver, tool_registry) -> tuple[Discussion, Moderator]`
- `delete_discussions(db, discussion_ids) -> dict`
- `restore_discussion(db, discussion_id) -> dict`

Note: `conclude_discussion` could go in either flow or state. Place it here since its primary purpose is transitioning discussion status. The `ConsensusApp.conclude_discussion` wrapper handles `_notify`.

**Step 4: Run tests**

Run: `cd /Users/hherb/src/consensus && python -m pytest tests/test_app_discussion_state.py -v`
Expected: All passed

**Step 5: Wire into ConsensusApp and run full suite**

Run: `cd /Users/hherb/src/consensus && python -m pytest --tb=short -q`
Expected: All pass

**Step 6: Commit**

```bash
git add consensus/app_discussion_state.py consensus/app.py tests/test_app_discussion_state.py
git commit -m "refactor: extract discussion state management into app_discussion_state.py"
```

---

### Task 7: Final cleanup and verification

**Files:**
- Modify: `consensus/app.py` (final cleanup — remove dead imports, verify line count)

**Step 1: Verify app.py size**

Run: `wc -l consensus/app.py`
Expected: ~250-350 lines (down from 1450)

**Step 2: Verify no dead imports**

Run: `cd /Users/hherb/src/consensus && python -c "from consensus.app import ConsensusApp; print('OK')"`
Expected: OK

**Step 3: Run full test suite**

Run: `cd /Users/hherb/src/consensus && python -m pytest --tb=short -q`
Expected: All tests pass (existing + new)

**Step 4: Verify line counts of new modules**

Run: `wc -l consensus/app*.py`
Expected: Each under 400 lines, total similar to original 1450

**Step 5: Commit final cleanup**

```bash
git add consensus/app.py
git commit -m "refactor: final cleanup of app.py after module extraction"
```

---

## Summary

| Task | Module | Lines (approx) | Tests |
|------|--------|----------------|-------|
| 1 | `pricing.py` (modify) | +20 | `test_pricing.py` |
| 2 | `app_providers.py` | ~130 | `test_app_providers.py` |
| 3 | `app_entities.py` | ~90 | `test_app_entities.py` |
| 4 | `app_discussion_setup.py` | ~250 | `test_app_discussion_setup.py` |
| 5 | `app_discussion_flow.py` | ~300 | `test_app_discussion_flow.py` |
| 6 | `app_discussion_state.py` | ~250 | `test_app_discussion_state.py` |
| 7 | `app.py` cleanup | ~300 | existing `test_app.py` |
