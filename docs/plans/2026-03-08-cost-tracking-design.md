# Per-Model Cost Tracking Design

**Date:** 2026-03-08

## Goal

Track API costs per model used, per message, and per discussion. Display costs in the UI so users can monitor spending.

## Pricing Data Source

- OpenRouter's public `GET https://openrouter.ai/api/v1/models` endpoint (no auth required)
- Returns per-token pricing for every model across all providers
- Prices are in USD per single token (e.g. `0.000003` = $0.000003/token)
- Fields used: `pricing.prompt` (input cost) and `pricing.completion` (output cost)

## Pricing Cache (SQLite)

New `model_pricing` table:

| Column | Type | Description |
|--------|------|-------------|
| `model_id` | TEXT PK | OpenRouter full ID, e.g. `anthropic/claude-sonnet-4` |
| `prompt_cost` | REAL | $/token for input |
| `completion_cost` | REAL | $/token for output |
| `last_updated` | REAL | Timestamp of last fetch |

Refresh strategy (on app startup):
- If `last_updated` of any row > 7 days ago → refresh all pricing
- If a model currently in use has no pricing row → refresh all pricing
- Otherwise skip the fetch

## Model Matching

Fuzzy match: strip the `provider/` prefix from OpenRouter model IDs and match against the user's configured model name. E.g. `anthropic/claude-sonnet-4` matches `claude-sonnet-4`.

If multiple OpenRouter entries match the same short name, disambiguate using the provider's `base_url`:
- `api.openai.com` → prefer `openai/*`
- `api.anthropic.com` → prefer `anthropic/*`
- `generativelanguage.googleapis.com` → prefer `google/*`
- etc.

## Cost Calculation

At message save time:
```
cost = (prompt_tokens * prompt_cost) + (completion_tokens * completion_cost)
```

Stored as a new `cost` REAL column on the `messages` table. Computed once at write time, not recalculated.

## Display

- **Per-message:** Small cost label next to each AI message, e.g. "$0.0034"
- **Per-discussion total:** Running sum in the discussion view, e.g. "Discussion cost: $0.42"
- USD only, 4 decimal places for per-message, 2 for totals

## Module Structure

- **`pricing.py`** — `PricingCache` class: fetch from OpenRouter, cache to SQLite, lookup by model name, calculate cost
- **Database migration** — adds `model_pricing` table + `messages.cost` column
- **Frontend** — `app.js` changes to render cost per message and discussion total
