# Chapter 10: Institutional Memory

Institutional memory allows AI participants to retain and recall information across discussions. This is useful for long-running research programs, ongoing projects, or any situation where continuity matters.

## Overview

The memory system has three components:

1. **Personal Memories** — Per-entity persistent storage of observations, positions, and reflections
2. **Discussion Search** — Semantic search across all past discussion messages
3. **Knowledge Graph** — Structured triples (subject → relation → object) for factual knowledge

All three require an **embedding service** to be configured.

## Configuring the Embedding Service

Navigate to the **Memory** tab in the setup interface.

| Field | Description | Default |
|-------|-------------|---------|
| **Embedding Endpoint URL** | The API endpoint for generating embeddings | `http://localhost:11434` |
| **Embedding Model** | The model name to use | `nomic-embed-text-v2-moe:latest` |

The default configuration works with a local [Ollama](https://ollama.ai) instance. Install Ollama, then pull the embedding model:

```bash
ollama pull nomic-embed-text-v2-moe
```

Click **"Test Connection"** to verify the embedding service is working. If the test fails, check that Ollama (or your chosen embedding service) is running and the model is available.

Any OpenAI-compatible embedding endpoint will work — you can use OpenAI's API, a local server, or any other compatible service.

## Personal Memories

AI participants with memory tools enabled can:

- **Store memories** — Save observations, conclusions, or reflections that persist across discussions
- **Recall memories** — Semantic search over their personal memory store using natural language queries
- **Forget memories** — Delete specific memories by ID

Each entity's memories are private — they can only access their own memories, not those of other participants.

### How It Works in Practice

An AI participant might store a memory like:
> "In the March 2025 discussion on oncology, the group concluded that CAR-T therapy shows the most promise for solid tumours when combined with checkpoint inhibitors."

In a later discussion, the same entity can recall this memory by searching for "CAR-T therapy solid tumours" and build on the prior reasoning.

## Discussion Search

The `discussion_search` tool allows AI participants to search semantically across all past discussion messages. This is different from personal memories — it searches the actual conversation content from all discussions.

Results include the matching passage, the discussion topic, and context around the match. An optional topic filter narrows results to discussions about a specific subject.

## Knowledge Graph

The knowledge graph stores structured relationships between concepts:

- **Assert** — Add a triple: subject → relation → object (with optional evidence)
  - Example: "gene therapy" → "shows promise for" → "sickle cell disease" (evidence: "2024 Vertex trial results")
- **Query** — Two modes:
  - **Search** — Semantic search over subjects and objects
  - **Neighbors** — Given an exact node, return all connected edges

Relations are free-text, but common ones include: supports, contradicts, implies, causes, is-a, part-of.

## When to Enable Memory

Memory is most valuable when:
- You run multiple discussions on related topics over time
- AI participants need to build on prior conclusions
- You're conducting a research program with evolving questions
- You want AI participants to develop consistent domain expertise

For one-off discussions, memory tools are not necessary.

## Memory in the UI

The **Memory** tab shows the embedding service configuration. The semantic strategy option in context strategies (see [Chapter 11](11_context_strategies.md)) also shows as disabled when no embedding endpoint is configured, since it depends on the same embedding infrastructure.
