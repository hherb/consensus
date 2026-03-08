# Cost Tracking Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Track per-model API costs by fetching pricing from OpenRouter, caching in SQLite, computing cost per message, and displaying in the UI.

**Architecture:** New `pricing.py` module fetches and caches OpenRouter pricing data. Cost is computed at message-save time using token counts already captured. Frontend shows per-message cost and discussion total.

**Tech Stack:** Python (httpx for fetch), SQLite (pricing cache + cost column), vanilla JS frontend.

---

### Task 1: Database Migration — `model_pricing` table + `messages.cost` column

**Files:**
- Create: `consensus/migrations/003_cost_tracking.sql`

**Step 1: Create migration file**

```sql
CREATE TABLE IF NOT EXISTS model_pricing (
    model_id TEXT PRIMARY KEY,
    prompt_cost REAL NOT NULL DEFAULT 0,
    completion_cost REAL NOT NULL DEFAULT 0,
    last_updated REAL NOT NULL DEFAULT 0
);

ALTER TABLE messages ADD COLUMN cost REAL;
```

**Step 2: Verify migration applies**

Run: `python -c "from consensus.database import Database; db = Database('/tmp/test_cost.db'); print('OK'); import os; os.remove('/tmp/test_cost.db')"`
Expected: `OK` (no errors)

**Step 3: Commit**

```bash
git add consensus/migrations/003_cost_tracking.sql
git commit -m "Add migration for model_pricing table and messages.cost column"
```

---

### Task 2: Pricing Module — `pricing.py`

**Files:**
- Create: `consensus/pricing.py`

**Step 1: Create `PricingCache` class**

```python
"""Fetch and cache model pricing from OpenRouter."""

import logging
import sqlite3
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
PRICING_MAX_AGE_SECONDS = 7 * 24 * 3600  # 1 week

# Map base_url hostnames to OpenRouter provider prefixes for disambiguation
_HOST_TO_PROVIDER = {
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "generativelanguage.googleapis.com": "google",
    "api.mistral.ai": "mistralai",
    "api.groq.com": "groq",
    "api.deepseek.com": "deepseek",
    "api.together.xyz": "together",
    "openrouter.ai": "openrouter",
}


class PricingCache:
    """Fetch model pricing from OpenRouter and cache in SQLite."""

    def __init__(self, conn: sqlite3.Connection,
                 lock: threading.Lock) -> None:
        self._conn = conn
        self._lock = lock

    def _needs_refresh(self) -> bool:
        """Check if pricing data is stale (> 1 week old) or empty."""
        cur = self._conn.execute(
            "SELECT MIN(last_updated) FROM model_pricing"
        )
        row = cur.fetchone()
        if row is None or row[0] is None:
            return True
        return (time.time() - row[0]) > PRICING_MAX_AGE_SECONDS

    def needs_refresh_for_model(self, model_name: str) -> bool:
        """Check if a specific model has no pricing data."""
        cur = self._conn.execute(
            "SELECT 1 FROM model_pricing WHERE model_id LIKE ?",
            (f"%/{model_name}",),
        )
        return cur.fetchone() is None

    def refresh(self) -> bool:
        """Fetch pricing from OpenRouter and update cache.

        Returns True if successful, False on failure.
        """
        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.get(OPENROUTER_MODELS_URL)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            logger.warning("Failed to fetch pricing from OpenRouter",
                           exc_info=True)
            return False

        models = data.get("data", [])
        if not models:
            logger.warning("OpenRouter returned no models")
            return False

        now = time.time()
        rows = []
        for m in models:
            model_id = m.get("id", "")
            pricing = m.get("pricing") or {}
            prompt_cost = _parse_cost(pricing.get("prompt"))
            completion_cost = _parse_cost(pricing.get("completion"))
            if model_id and (prompt_cost or completion_cost):
                rows.append((model_id, prompt_cost, completion_cost, now))

        if not rows:
            return False

        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO model_pricing "
                "(model_id, prompt_cost, completion_cost, last_updated) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()

        logger.info("Updated pricing for %d models from OpenRouter",
                     len(rows))
        return True

    def lookup(self, model_name: str,
               base_url: str = "") -> Optional[tuple[float, float]]:
        """Look up (prompt_cost, completion_cost) for a model name.

        Uses fuzzy matching: strips provider/ prefix from OpenRouter IDs.
        If base_url is provided, uses it to disambiguate.
        Returns None if no match found.
        """
        # Try exact match first (user might use full OpenRouter ID)
        cur = self._conn.execute(
            "SELECT prompt_cost, completion_cost FROM model_pricing "
            "WHERE model_id = ?",
            (model_name,),
        )
        row = cur.fetchone()
        if row:
            return (row[0], row[1])

        # Fuzzy match: find all entries ending with /model_name
        cur = self._conn.execute(
            "SELECT model_id, prompt_cost, completion_cost "
            "FROM model_pricing WHERE model_id LIKE ?",
            (f"%/{model_name}",),
        )
        matches = cur.fetchall()

        if not matches:
            # Try matching just the model part (strip version suffixes etc)
            # e.g. "gpt-4o" should match "openai/gpt-4o-2024-08-06"
            cur = self._conn.execute(
                "SELECT model_id, prompt_cost, completion_cost "
                "FROM model_pricing WHERE model_id LIKE ?",
                (f"%/{model_name}%",),
            )
            matches = cur.fetchall()

        if not matches:
            return None

        if len(matches) == 1:
            return (matches[0][1], matches[0][2])

        # Disambiguate using base_url
        if base_url:
            preferred = _provider_from_url(base_url)
            if preferred:
                for m in matches:
                    if m[0].startswith(preferred + "/"):
                        return (m[1], m[2])

        # Fall back to first match
        return (matches[0][1], matches[0][2])

    def calculate_cost(self, model_name: str, base_url: str,
                       prompt_tokens: int,
                       completion_tokens: int) -> Optional[float]:
        """Calculate cost in USD for a given request.

        Returns None if pricing not available for this model.
        """
        pricing = self.lookup(model_name, base_url)
        if pricing is None:
            return None
        prompt_cost, completion_cost = pricing
        return (prompt_tokens * prompt_cost) + \
               (completion_tokens * completion_cost)


def _parse_cost(value: object) -> float:
    """Parse a cost value from OpenRouter (string or number)."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _provider_from_url(base_url: str) -> str:
    """Extract OpenRouter provider prefix from a base URL."""
    host = urlparse(base_url).hostname or ""
    for pattern, provider in _HOST_TO_PROVIDER.items():
        if pattern in host:
            return provider
    return ""
```

**Step 2: Commit**

```bash
git add consensus/pricing.py
git commit -m "Add PricingCache module for OpenRouter pricing lookup"
```

---

### Task 3: Wire Pricing Into Database and App

**Files:**
- Modify: `consensus/database.py` — add `cost` param to `add_message()`, expose pricing cache
- Modify: `consensus/app.py` — initialize pricing, compute cost at message save time

**Step 1: Update `database.py` — expose pricing cache and update `add_message()`**

In `Database.__init__()` (after `run_migrations` call), add:

```python
from .pricing import PricingCache
self.pricing = PricingCache(self.conn, self._lock)
```

In `Database.add_message()`, add `cost: Optional[float] = None` parameter. Update the INSERT to include the `cost` column:

```python
def add_message(self, discussion_id: int, entity_id: int,
                content: str, role: str, turn_number: int = 0,
                model_used: str = "", prompt_tokens: int = 0,
                completion_tokens: int = 0, total_tokens: int = 0,
                latency_ms: int = 0, temperature_used: float = 0,
                prompt_id: int = 0,
                tool_calls_json: str = "",
                cost: Optional[float] = None) -> int:
```

Update the SQL INSERT to add `cost` column and value.

In `Database.get_discussion_messages()` (or wherever messages are loaded), ensure the `cost` column is included in the SELECT.

**Step 2: Update `app.py` — startup pricing refresh + cost calculation on message save**

In `ConsensusApp.__init__()`, after `self.db = Database(...)`, add startup pricing refresh:

```python
self._refresh_pricing_if_needed()
```

Add method:

```python
def _refresh_pricing_if_needed(self) -> None:
    """Refresh pricing cache on startup if stale or missing models."""
    try:
        if self.db.pricing._needs_refresh():
            self.db.pricing.refresh()
    except Exception:
        logger.warning("Failed to refresh pricing on startup", exc_info=True)
```

In the code where `db.add_message()` is called after AI generation (around line 663-675), compute cost before saving:

```python
cost = self.db.pricing.calculate_cost(
    resp.model,
    current.ai_config.base_url if current.ai_config else "",
    resp.prompt_tokens,
    resp.completion_tokens,
)
```

Pass `cost=cost` to the `db.add_message()` call. Do this for ALL places where `add_message()` is called with AI-generated content (participant turns, moderator summaries, conclusions, etc.).

**Step 3: Commit**

```bash
git add consensus/database.py consensus/app.py
git commit -m "Wire pricing cache into database and app, compute cost per message"
```

---

### Task 4: Update Message Model to Include Cost

**Files:**
- Modify: `consensus/models.py` — add `cost` field to `Message`, include in `to_dict()` and `from_db_row()`

**Step 1: Add `cost` field to Message dataclass**

After the `latency_ms` field (line 140), add:

```python
cost: Optional[float] = None
```

**Step 2: Update `to_dict()`**

Inside the `if self.model_used:` block, add:

```python
if self.cost is not None:
    d["cost"] = round(self.cost, 6)
```

**Step 3: Update `from_db_row()`**

Add to the constructor call:

```python
cost=row.get("cost"),
```

**Step 4: Commit**

```bash
git add consensus/models.py
git commit -m "Add cost field to Message model"
```

---

### Task 5: Frontend — Display Per-Message Cost and Discussion Total

**Files:**
- Modify: `consensus/static/app.js` — render cost in message metadata, add discussion cost total

**Step 1: Update message metadata rendering**

In the live discussion view (around line 1280-1282), where `metaHtml` is built, add cost:

```javascript
let metaHtml = '';
if (msg.model_used) {
    let costStr = msg.cost != null ? ` | $${msg.cost.toFixed(4)}` : '';
    metaHtml = `<span class="text-muted" style="font-size:0.7rem;margin-left:0.5rem">${msg.model_used} | ${msg.total_tokens}tok | ${msg.latency_ms}ms${costStr}</span>`;
}
```

Do the same for the HTML export rendering (around line 1496-1497).

**Step 2: Add discussion cost total**

After the discussion view renders all messages, compute and display a running total. Add a small element in the discussion header/footer area:

```javascript
function calculateDiscussionCost(messages) {
    let total = 0;
    for (const m of messages) {
        if (m.cost != null) total += m.cost;
    }
    return total;
}
```

Display as e.g. `"Cost: $0.42"` in the discussion info area.

**Step 3: Update JSON export**

In the export logic (around line 1422-1429), add `cost` to the `ai_metadata` object:

```javascript
if (m.model_used) {
    msg.ai_metadata = {
        model: m.model_used,
        tokens: m.total_tokens,
        prompt_tokens: m.prompt_tokens,
        completion_tokens: m.completion_tokens,
        latency_ms: m.latency_ms,
        cost: m.cost,
    };
}
```

**Step 4: Commit**

```bash
git add consensus/static/app.js
git commit -m "Display per-message cost and discussion cost total in UI"
```

---

### Task 6: Handle Pricing Refresh for Unknown Models

**Files:**
- Modify: `consensus/app.py` — trigger refresh when encountering an unpriced model

**Step 1: Add on-demand refresh logic**

In the cost calculation code (added in Task 3), if `calculate_cost()` returns `None`, try refreshing pricing once and retry:

```python
cost = self.db.pricing.calculate_cost(
    resp.model, base_url, resp.prompt_tokens, resp.completion_tokens,
)
if cost is None and self.db.pricing.needs_refresh_for_model(resp.model):
    self.db.pricing.refresh()
    cost = self.db.pricing.calculate_cost(
        resp.model, base_url, resp.prompt_tokens, resp.completion_tokens,
    )
```

**Step 2: Commit**

```bash
git add consensus/app.py
git commit -m "Trigger pricing refresh on-demand for unknown models"
```

---

### Task 7: Final Verification

**Step 1: Run the app and verify**

Run: `python -m consensus --web --debug`

- Create a discussion with an AI participant
- Run a turn, verify cost appears next to the AI message
- Check the discussion total updates
- Verify the JSON export includes cost data

**Step 2: Check the database**

```bash
python -c "
import sqlite3
conn = sqlite3.connect('path/to/consensus.db')
cur = conn.execute('SELECT model_id, prompt_cost, completion_cost FROM model_pricing LIMIT 5')
for row in cur: print(row)
"
```

Verify pricing rows are populated.
