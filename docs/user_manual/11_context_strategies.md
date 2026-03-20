# Chapter 11: Context Strategies

Context strategies control how much of the discussion history each AI participant sees when generating their response. This is important for managing costs, staying within model context limits, and focusing attention.

## Why Context Strategies Matter

Every message sent to an AI model includes a "context" — the conversation history that the model reads before generating its response. Longer context means:
- **Higher cost** (more input tokens)
- **Higher latency** (more to process)
- **Risk of exceeding model limits** (each model has a maximum context length)

But shorter context means:
- **Lost information** (the model can't reference earlier turns)
- **Repetition** (the model may re-cover ground already discussed)

Context strategies let you find the right balance for each participant and discussion.

## Available Strategies

### Full History (`full`)

Sends the complete discussion history to the participant. Every message from the start of the discussion is included.

**Pros:** Nothing is lost — the model has perfect recall of everything said.
**Cons:** Cost grows linearly with discussion length. Will eventually exceed the model's context window for long discussions.
**Best for:** Short discussions (under ~30 messages) or when completeness is critical.

### Sliding Window (`sliding_window`) — Default

Sends only the last N messages, where N is the configured window size (default: 20).

**Pros:** Predictable cost. Always fits within context limits. Fast.
**Cons:** Earlier turns are invisible to the model.
**Best for:** Most discussions. The default choice.

### Summary + Recent (`summary`)

Sends moderator summaries (storyboard entries) for older turns, plus the last N messages in full. This gives the model a compressed view of early discussion plus full detail on recent turns.

**Pros:** Good balance of coverage and cost. Model knows what was discussed earlier via summaries.
**Cons:** Summary quality depends on the moderator's summarisation. Some nuance is lost in compression.
**Best for:** Long discussions where early context matters but full history is too expensive.

### Semantic (RAG) (`semantic`)

Uses embedding-based retrieval to select the most relevant messages from the entire discussion history. Combines a recency floor (25% of the window = most recent messages always included) with cosine-similarity search over older messages based on relevance to the current discussion state.

**Pros:** Dynamically selects the most relevant context regardless of when it was said.
**Cons:** Requires an embedding service. Slightly higher latency for the retrieval step. May miss context that's relevant but not semantically similar to current discussion.
**Best for:** Long discussions where specific earlier points need to be recalled, and you have an embedding service running.
**Requires:** Embedding endpoint configured in the Memory tab. Shows as disabled in the UI when not configured.

### Token-Aware (Auto) (`token_window`)

Dynamically fills the available token budget based on the model's known context length and reserved output tokens. Loads messages from most recent backward until the budget is full.

**Pros:** Automatically adapts to each model's capabilities. No manual window size tuning needed.
**Cons:** Requires known context length data (fetched from OpenRouter pricing cache). Falls back to sliding window if context length is unknown.
**Best for:** Mixed-model discussions where different participants have different context limits.

## Configuring Context Strategies

### Discussion-Level Default

In the **Discussion Settings** card during setup:
1. Select the **Default Context Strategy** from the dropdown
2. Set the **Context Window Size** (applies to sliding_window, summary, and semantic strategies)

These defaults apply to all participants unless individually overridden.

### Per-Entity Override

In the discussion roster, each entity has a context strategy selector. Use this to give specific participants different strategies. For example:
- Give the moderator `full` history (so it can write comprehensive summaries)
- Give regular participants `sliding_window` with a window of 10 (to save costs)
- Give a research-focused participant `semantic` (so it can recall relevant earlier points)

### Window Size

The window size (3–200) determines:
- For `sliding_window`: How many recent messages to include
- For `summary`: How many recent messages to include alongside summaries
- For `semantic`: The total number of messages to include (split between recency floor and semantic retrieval)
- For `token_window`: Used as fallback if model context length is unknown
