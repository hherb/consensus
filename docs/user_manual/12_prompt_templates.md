# Chapter 12: Prompt Templates

Prompt templates control the instructions given to AI participants and moderators. Consensus comes with sensible defaults, but you can customise them to change how AI entities behave.

## Understanding Prompt Templates

Navigate to the **Prompts** tab to see and manage templates. Each template has:

| Field | Description |
|-------|-------------|
| **Name** | A descriptive name for the template |
| **Role** | Who uses it: **Moderator** or **Participant** |
| **Target** | What type of entity: **AI** or **Human** (human prompts are used as guidance text shown in the UI) |
| **Task** | When it's used (see below) |
| **Content** | The actual prompt text, with template variables |

## Task Types

| Task | When it's used |
|------|---------------|
| **System** | The system message sent at the start of every AI interaction. Defines the entity's role and behaviour |
| **Turn** | Instructions for generating a discussion turn response |
| **Summarize** | Instructions for the moderator when writing turn summaries (storyboard entries) |
| **Mediate** | Instructions for the moderator when mediating a dispute or redirecting the discussion |
| **Conclude** | Instructions for the moderator when generating the final discussion synthesis |
| **Open** | Instructions for the moderator's opening message |
| **Guidance** | Contextual guidance shown to human participants (displayed in the UI, not sent to AI) |

## Template Variables

Prompt templates can include variables that are replaced with actual values at runtime:

| Variable | Replaced with |
|----------|-------------|
| `{entity_name}` | The participant's display name |
| `{topic}` | The discussion topic text |
| `{participants}` | A formatted list of all participants in the discussion |
| `{speaker_name}` | The name of the current speaker |
| `{turn_number}` | The current turn number |
| `{context}` | The conversation history (formatted according to context strategy) |

## Customising Templates

### Editing Defaults

Click any template in the list to edit it. Changes apply to all future discussions that use the default templates. Existing discussions are not affected.

### Per-Entity System Prompt Override

For more targeted customisation, use the **System Prompt** field in an entity's profile (see [Chapter 3](03_profiles_and_entities.md)). This completely replaces the system prompt template for that specific entity, without changing the global templates.

### Creating New Templates

Click **"+ Add Prompt"** to create a new template. You might want custom templates for:
- Domain-specific moderator behaviour (e.g., a medical discussion moderator that enforces evidence standards)
- Alternative participant styles (e.g., a template that emphasises quantitative reasoning)
- Specialised summary formats (e.g., structured summaries with explicit claims and evidence)

## Tips

- **Keep system prompts focused.** Overly long system prompts consume tokens on every turn without proportional benefit.
- **Test changes incrementally.** Modify one template at a time and run a short discussion to see the effect.
- **Use entity-level overrides for personas.** The global templates define general behaviour; use the entity's custom system prompt for specific expertise and personality.
