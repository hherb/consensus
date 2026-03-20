# Consensus User Manual

**Consensus** is a moderated discussion platform for structured multi-party dialogues between humans and AI entities. A designated moderator — human or AI — manages the flow, turn-taking, and synthesis of each discussion.

This manual walks you through everything you need to get started and make the most of the platform.

---

## Table of Contents

1. [Getting Started](01_getting_started.md) — Installation, launch modes, and first run
2. [Providers and Models](02_providers_and_models.md) — Configuring AI backends and API keys
3. [Profiles and Entities](03_profiles_and_entities.md) — Creating human, AI, and expert participants
4. [Setting Up a Discussion](04_setting_up_a_discussion.md) — Topics, rosters, methods, and options
5. [Discussion Methods](05_discussion_methods.md) — The 13 analytical frameworks available
6. [Running a Discussion](06_running_a_discussion.md) — The live discussion interface and moderator controls
7. [Human Participation](07_human_participation.md) — How humans join, speak, and interact during discussions
8. [Tools and Capabilities](08_tools_and_capabilities.md) — Web search, documents, code execution, memory, and more
9. [Documents and Images](09_documents_and_images.md) — Attaching reference materials to discussions
10. [Institutional Memory](10_institutional_memory.md) — Persistent AI memory, discussion search, and the knowledge graph
11. [Context Strategies](11_context_strategies.md) — Controlling how much history each participant sees
12. [Prompt Templates](12_prompt_templates.md) — Customising moderator and participant behaviour
13. [History and Export](13_history_and_export.md) — Reviewing, resuming, and exporting past discussions
14. [Multi-User and Authentication](14_multi_user_and_auth.md) — Deploying for multiple users with auth
15. [MCP Integration](15_mcp_integration.md) — Connecting external tools and exposing Consensus to AI agents
16. [Cost Tracking and Budgets](16_cost_tracking.md) — Monitoring and limiting API spend

---

## Quick Start

```bash
# Install as a CLI tool (makes the `consensus` command available)
uv tool install ".[all]"

# Launch in desktop mode
consensus

# Or launch as a web server
consensus --web
```

For detailed installation instructions, see [Chapter 1: Getting Started](01_getting_started.md).
