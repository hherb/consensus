# 7. API Surface Reference

[Back to index](programmer-manual.md) | [Previous: Data Flow and Lifecycle](06-data-flow-and-lifecycle.md) | [Next: Tool Use](08-tool-use.md)

---

## REST API (Web Mode)

All endpoints: `POST /api/{method}` with JSON body.

### State

| Method | JSON body fields | Returns |
|--------|-----------------|---------|
| `get_state` | *(none)* | Full application state |

### Providers

| Method | JSON body fields | Returns |
|--------|-----------------|---------|
| `add_provider` | `name`, `base_url`, `api_key_env?`, `api_key?` | Provider dict |
| `update_provider` | `provider_id`, `name?`, `base_url?`, `api_key_env?`, `api_key?` | `true` |
| `delete_provider` | `provider_id` | `true` |
| `fetch_models` | `provider_id` | List of model ID strings |

### Entities

| Method | JSON body fields | Returns |
|--------|-----------------|---------|
| `save_entity` | `name`, `entity_type`, `avatar_color?`, `provider_id?`, `model?`, `temperature?`, `max_tokens?`, `system_prompt?`, `entity_id?` | Entity dict |
| `delete_entity` | `entity_id` | `{"deleted": true}` or `{"deactivated": true}` |
| `reactivate_entity` | `entity_id` | `true` |
| `get_inactive_entities` | *(none)* | List of entity dicts |

### Prompts

| Method | JSON body fields | Returns |
|--------|-----------------|---------|
| `save_prompt` | `prompt_id?`, `name`, `role`, `target`, `task`, `content` | Prompt dict |
| `delete_prompt` | `prompt_id` | `true` |

### Discussion Setup

| Method | JSON body fields | Returns |
|--------|-----------------|---------|
| `add_to_discussion` | `entity_id`, `is_moderator?`, `also_participant?` | Entity dict |
| `remove_from_discussion` | `entity_id` | `true` |
| `set_moderator` | `entity_id`, `also_participant?` | `true` |
| `set_topic` | `topic` | `true` |

### Discussion Lifecycle

| Method | JSON body fields | Returns |
|--------|-----------------|---------|
| `start_discussion` | `moderator_participates?` | Full state |
| `submit_human_message` | `entity_id`, `content` | Message dict |
| `submit_moderator_message` | `content` | Message dict |
| `generate_ai_turn` | *(none)* | Message dict (includes `tool_calls` if any) or `{"error": ...}` |
| `complete_turn` | `moderator_summary?` | `{next_speaker, turn_number, state}` |
| `reassign_turn` | `entity_id` | `{reassigned_to, state}` |
| `mediate` | `context?` | Message dict |
| `conclude` | *(none)* | Full state |
| `pause_discussion` | *(none)* | Full state |
| `resume_discussion` | *(none)* | Full state |
| `add_participant` | `entity_id` | Full state |
| `remove_participant` | `entity_id` | Full state |

### Tools

| Method | JSON body fields | Returns |
|--------|-----------------|---------|
| `list_tools` | *(none)* | List of `{name, description, parameters, provider}` |
| `get_entity_tools` | `entity_id` | List of tool assignment dicts |
| `assign_tool` | `entity_id`, `tool_name`, `access_mode?` | `true` |
| `remove_tool` | `entity_id`, `tool_name` | `true` |
| `set_tool_override` | `discussion_id`, `entity_id`, `tool_name`, `enabled` | `true` |

### History / Export

| Method | JSON body fields | Returns |
|--------|-----------------|---------|
| `get_export_data` | `discussion_id` | Discussion dict (read-only) |
| `load_discussion` | `discussion_id` | Full state |
| `reset` | *(none)* | `true` |

### Non-API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/health` | GET | Health check (returns `{"status": "ok"}`) |
| `/{path}` | GET | Static file serving (index.html, app.js, style.css) |

### Response Format

Every successful API response wraps the result:
```json
{"result": <return value>, "state": <full app state>}
```

This means the frontend always receives the latest state after any mutation.

### BYOK Header

In multi-user mode, the frontend sends API keys via the `X-API-Keys` HTTP
header as a JSON-encoded map of `provider_id -> key`:
```json
{"1": "sk-abc123", "3": "sk-ant-xyz"}
```

---

## pywebview Bridge (Desktop Mode)

The `DesktopBridge` class mirrors the same methods. From JavaScript:

```javascript
const result = await window.pywebview.api.generate_ai_turn();
const state  = await window.pywebview.api.get_state();
const tools  = await window.pywebview.api.list_available_tools();
```

The method names and parameters match the REST API. The bridge handles
sync-to-async conversion internally.

---

[Next: Tool Use](08-tool-use.md)
