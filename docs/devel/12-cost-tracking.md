# 12. Cost Tracking

[Back to index](programmer-manual.md) | [Previous: Authentication](11-authentication.md) | [Next: MCP Expert Plugins](13-mcp-expert-plugins.md)

---

Consensus tracks the cost of each AI-generated message using pricing data
fetched from OpenRouter. This allows users to see per-message and
per-discussion cost totals in the UI.

## Overview

```
ConsensusApp
    |
    +-- PricingCache (pricing.py)
    |     |
    |     +-- model_pricing table (SQLite cache)
    |     +-- OpenRouter API (https://openrouter.ai/api/v1/models)
    |
    +-- Moderator.generate_turn()
          |
          +-- AIClient.complete() → AIResponse (prompt_tokens, completion_tokens)
          +-- PricingCache.calculate_cost(model, base_url, tokens)
          +-- Message.cost stored in database
```

## PricingCache

Defined in `pricing.py`. Manages a local SQLite cache of model pricing data
from OpenRouter.

### Construction

```python
pricing = PricingCache(conn=db.conn, lock=db._lock)
```

Takes a SQLite connection and threading lock (shared with `Database` for
write serialisation).

### Key methods

| Method | Purpose |
|--------|---------|
| `refresh()` | Fetch all model pricing from OpenRouter API, update SQLite cache. Returns `True` on success |
| `lookup(model_name, base_url)` | Find pricing for a model. Returns `(prompt_cost, completion_cost)` or `None` |
| `calculate_cost(model_name, base_url, prompt_tokens, completion_tokens)` | Calculate USD cost for a message. Returns `float` or `None` |
| `needs_refresh_for_model(model_name)` | Check if the model is missing from cache |

### Cache behaviour

- Pricing data is stored in the `model_pricing` SQLite table
- Cache auto-refreshes when older than 7 days (`_needs_refresh()`)
- `ConsensusApp` triggers a refresh check on startup

### Model name matching

OpenRouter uses a `provider/model` naming convention (e.g.
`anthropic/claude-opus-4-6`), while Consensus stores just the model name
from each provider's API (e.g. `claude-opus-4-6`). `PricingCache.lookup()`
uses several strategies to find a match:

1. **Direct lookup** — exact match against `model_pricing.model_id`
2. **Provider prefix** — prepend a provider prefix derived from the API
   `base_url` (e.g. `https://api.anthropic.com/v1` → `anthropic/`)
3. **Model aliases** — a static map of known name differences (e.g.
   `deepseek-reasoner` → `deepseek/deepseek-r1`)
4. **Version variants** — try hyphen↔dot conversions for version numbers
   (e.g. `claude-3.5-sonnet` ↔ `claude-3-5-sonnet`)
5. **Date suffix stripping** — remove trailing date suffixes like
   `-20250605` and retry matching

Helper functions:
- `_strip_date_suffix(name)` — remove `YYYYMMDD` date suffixes
- `_version_variants(name)` — generate hyphen/dot version permutations
- `_parse_cost(value)` — handle string or number cost formats from the API
- `_provider_from_url(base_url)` — map API base URLs to OpenRouter provider
  prefixes

---

## Database Schema

```sql
model_pricing (
    model_id        TEXT PRIMARY KEY,
    prompt_cost     REAL NOT NULL DEFAULT 0,
    completion_cost REAL NOT NULL DEFAULT 0,
    last_updated    REAL NOT NULL DEFAULT 0
)
```

The `messages` table has a `cost REAL` column added by migration
`003_cost_tracking.sql`.

---

## Integration Points

### Message generation

After `AIClient.complete()` returns an `AIResponse`, the cost is calculated:
```python
cost = self.pricing.calculate_cost(
    model_name=response.model,
    base_url=entity.ai_config.base_url,
    prompt_tokens=response.prompt_tokens,
    completion_tokens=response.completion_tokens
)
```

The cost is stored in `Message.cost` and persisted to the database.

### Frontend display

The frontend displays:
- **Per-message cost** — shown in the message metadata alongside token counts
  and latency
- **Discussion cost total** — sum of all message costs, displayed in the
  discussion header area

---

## Environment Variables

No additional environment variables are required. Pricing data is fetched from
the public OpenRouter API endpoint without authentication.

---

[Next: MCP Expert Plugins](13-mcp-expert-plugins.md)
