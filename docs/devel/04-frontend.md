# 4. Frontend (HTML / CSS / JS)

[Back to index](programmer-manual.md) | [Previous: Backend Modules](03-backend-modules.md) | [Next: Database](05-database.md)

---

The frontend is a **single-page application** built with vanilla JavaScript --
no frameworks, no build step, no npm.

## `index.html` -- Page Structure

Two top-level `<section>` elements, toggled with the `.hidden` class:

- **`#setup-phase`** -- shown before a discussion starts. Contains a tab bar
  with 5 tabs:
  - *New Discussion* -- topic input, entity picker, start button
  - *Providers* -- manage API provider endpoints
  - *Profiles* -- manage entity profiles (human/AI), including tool assignments
  - *Prompts* -- edit prompt templates
  - *History* -- browse and load past discussions

- **`#discussion-phase`** -- shown during an active discussion. Three-column
  grid:
  - Left: participant sidebar with speaking indicator
  - Centre: message feed + input area
  - Right: storyboard panel (running summaries)

Five modal dialogs for editing providers, entities, prompts, moderator input,
and turn reassignment.

## `style.css` -- Theming and Layout

**Dark/light theme:** CSS custom properties in `:root` define dark mode
colours. A `@media (prefers-color-scheme: light)` block overrides them for
light mode. The theme follows the OS preference automatically.

**Layout:** The discussion phase uses CSS Grid with three columns
(`200px 1fr 260px`). Responsive breakpoint at 900px collapses to a single
column.

**No external dependencies.** All styling is self-contained.

## `app.js` -- Frontend Application

The frontend JS is structured as follows:

### API Adapters

Two classes (`DesktopAPI` and `WebAPI`) provide the same interface but
communicate differently:
- `DesktopAPI` calls `window.pywebview.api.<method>()` (synchronous Python bridge)
- `WebAPI` uses `fetch('/api/<method>', ...)` (HTTP POST)

The correct adapter is selected at startup:
```javascript
api = window.pywebview ? new DesktopAPI() : new WebAPI();
```

Both adapters expose methods for tool management (`listTools()`,
`getEntityTools()`, `assignTool()`, `removeTool()`, `setToolOverride()`) in
addition to all other app methods.

### Global state

A single `state` object holds the full application state, updated by
`onStateUpdate(newState)`.

### `onStateUpdate(newState)` -- the state refresh function

Called whenever the backend pushes new state (desktop mode) or after each API
response (web mode). It merges the new state, re-renders all UI panels, and
manages the setup/discussion phase transitions.

### Key rendering functions

| Function | Renders |
|----------|---------|
| `renderProviders()` | Provider list in the Providers tab |
| `renderProfiles()` | Entity profile list in the Profiles tab |
| `renderPrompts()` | Prompt template list in the Prompts tab |
| `renderHistory()` | Discussion history list in the History tab |
| `renderAvailableEntities()` | Selectable entity list for discussion setup |
| `renderDiscussionRoster()` | Entities added to the current discussion |
| `renderDiscussionEntities()` | Participant sidebar during discussion |
| `renderMessages()` | Message feed (incremental -- only new messages) |
| `renderStoryboard()` | Storyboard panel (incremental) |

### Discussion automation

When the current speaker is AI, the frontend automatically calls
`api.generateAiTurn()` followed by `api.completeTurn()`. This is handled in
`runTurnCycle()`, which loops automatically for consecutive AI speakers.

### Tool call display

Messages that include tool calls render an inline collapsible section showing
each tool invocation, its arguments, and the result. This allows users to
inspect the AI's tool usage during turn generation.

### Entity profile tool assignment

The Profiles tab includes a tool assignment interface within the entity editor.
Users can:
- View available tools
- Assign tools to entities with an access mode (`private`, `shared`,
  `moderator_only`)
- Remove tool assignments

### Markdown rendering

`renderMarkdown()` converts a subset of Markdown to HTML (headers, bold,
italic, code blocks, lists). HTML is escaped first to prevent XSS.

### Export

The frontend handles JSON, HTML, and PDF export. JSON and HTML exports use
data fetched via `api.getExportData()`. PDF export opens a print dialog (via
`window.print()` in web mode, or directly in desktop mode).

### BYOK UI

In multi-user web mode, the frontend provides a "Set Key" button for each
provider. Keys are stored in `sessionStorage` (never persisted) and sent via
the `X-API-Keys` HTTP header on each request.

### Pause/resume UI

The discussion view includes Pause and Resume buttons in the control panel.
While paused, a participant management panel appears allowing the user to add
or remove entities from the discussion. The status display reflects the paused
state.

### Entity soft-delete UI

The Profiles tab shows dimmed styling for inactive (soft-deleted) profiles with
a Reactivate button. Delete confirmation dialogs include informative toast
messages explaining whether the entity was hard-deleted or deactivated.

---

[Next: Database](05-database.md)
