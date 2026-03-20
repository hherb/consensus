# Chapter 3: Profiles and Entities

Profiles define the participants that can join discussions. Each profile is an **entity** with a name, type, appearance, and (for AI entities) model configuration.

## Entity Types

### Human

A real person who types messages through the Consensus interface. Human entities have no AI configuration — they participate by reading the discussion and writing responses when it is their turn.

### AI (LLM)

An AI participant backed by a language model. You configure which provider and model it uses, along with generation parameters.

### Expert

A specialised AI entity backed by an MCP (Model Context Protocol) server. Experts are not regular discussion participants — they are consulted on-demand for specific queries and then step back. See [Chapter 15](15_mcp_integration.md) for details.

## Creating a Profile

Navigate to the **Profiles** tab and click **"+ Create New Profile"**.

### Common Fields

| Field | Description |
|-------|-------------|
| **Name** | The display name (e.g., "Dr. Chen", "GPT-4o Analyst", "Alice") |
| **Type** | Human or AI |
| **Avatar Color** | Choose from 8 preset colour swatches or enter a hex code. This colour identifies the entity in the discussion thread |

### AI-Specific Fields

| Field | Description | Default |
|-------|-------------|---------|
| **Provider** | Which API provider to use | (first configured) |
| **Model** | Select from the provider's model list, or type a model name | (varies) |
| **Temperature** | Controls randomness (0 = deterministic, 2 = very creative) | 0.7 |
| **Max Tokens** | Maximum response length in tokens | 4096 |
| **System Prompt** | Optional override — replaces the default prompt template entirely | (empty = use template) |

### Tool Assignments

Below the main fields, you'll see checkboxes for available tools. These control what capabilities the AI entity has during discussions:

- **Web Search** — search the web and fetch pages
- **Ask User** — pause and ask the human user a question
- **Document Tools** — read, search, and summarise attached documents
- **Python Execution** — run sandboxed Python code
- **Memory Tools** — store and recall persistent memories
- **Image Tools** — describe and work with images

Tool assignments are per-entity defaults. They can be overridden for specific discussions. See [Chapter 8](08_tools_and_capabilities.md) for details on each tool.

## Design Tips for AI Profiles

**Diverse models produce richer discussions.** Consider creating profiles using different providers or models. A discussion between GPT-4o, Claude, and Gemini will surface different perspectives than three instances of the same model.

**Use system prompts for persona.** The optional system prompt lets you give an AI entity a specific expertise, communication style, or analytical lens. For example:
- *"You are an epidemiologist with 20 years of clinical experience. Be precise with statistics and cautious about causal claims."*
- *"You are a venture capital analyst. Focus on market size, competitive dynamics, and unit economics."*

**Temperature matters.** Lower temperatures (0.1–0.4) produce more focused, consistent reasoning. Higher temperatures (0.8–1.2) produce more creative, divergent thinking. Match the temperature to the entity's role in the discussion.

## Managing Profiles

- Profiles are listed with **active** and **inactive** sections
- Click any profile to edit it
- Delete profiles you no longer need
- Profiles persist across discussions — create them once, reuse them many times

## Next Steps

With providers and profiles set up, you're ready to create your first discussion. Continue to [Chapter 4: Setting Up a Discussion](04_setting_up_a_discussion.md).
