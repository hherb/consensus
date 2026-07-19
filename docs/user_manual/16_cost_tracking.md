# Chapter 16: Cost Tracking and Budgets

Every AI-generated message in Consensus has a cost — the API charges for the tokens consumed. Consensus tracks these costs in real time and provides budget controls.

## How Costs Are Calculated

Consensus uses pricing data from OpenRouter to calculate per-message costs. The pricing cache:

- Fetches model pricing from `https://openrouter.ai/api/v1/models`
- Caches the data locally, refreshing if older than one week
- Uses fuzzy model name matching to find the right price (handles aliases and provider-specific naming)
- Calculates cost as: `(input_tokens × input_price) + (output_tokens × output_price)`

## Live Cost Display

During a discussion, the **cost badge** in the header shows:
- The running total of API costs for the discussion
- If a budget is set: total spent out of the limit (e.g., "$1.23 / $5.00")

The badge changes colour:
- **Normal** — well within budget
- **Orange** — at 80% or more of the budget limit

## Per-Message Costs

Each AI message in the chat shows its individual cost alongside other metadata:
- Model name
- Token counts (prompt tokens / completion tokens)
- Latency (time to generate)
- Cost for that specific message

## Setting a Budget

During discussion setup, enter a dollar amount in the **Budget** field:
- The field is **pre-filled with $1.00** — it is not empty by default, so an untouched discussion stops at one dollar
- **$0** = no limit
- Any positive number up to the **$100** maximum sets a hard budget

### When the Budget Is Reached

When cumulative costs hit the limit, a dialog appears with two options:
1. **Set a new limit** — Enter a higher budget and continue
2. **Conclude** — End the discussion immediately with a moderator synthesis

The discussion pauses automatically when the limit is reached — no further API calls are made until you choose an option.

## Cost in Exports

Exported discussions (JSON and HTML formats) include:
- Per-message cost data
- Total discussion cost in the export header

## Tips for Managing Costs

1. **Use context strategies wisely.** The `sliding_window` strategy (default) limits how many messages are sent as context, directly controlling input token costs. `full` history gets expensive fast for long discussions. See [Chapter 11](11_context_strategies.md).

2. **Set window sizes appropriately.** A window of 10 messages costs roughly half as much per turn as a window of 20.

3. **Choose models strategically.** Smaller/cheaper models work well for many participants while reserving expensive models for the moderator or key analysts.

4. **Set budgets as guardrails.** Even if you expect a discussion to be cheap, setting a budget prevents runaway costs from unexpectedly long discussions or tool-heavy turns.

5. **Monitor the cost badge.** Glance at it periodically during long discussions to ensure costs are tracking as expected.
