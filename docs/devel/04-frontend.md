# 4. Frontend (HTML / CSS / JS)

[Back to index](programmer-manual.md) | [Previous: Backend Modules](03-backend-modules.md) | [Next: Database](05-database.md)

---

The frontend is a **single-page application** built with vanilla JavaScript
organised as **ES modules** -- no frameworks, no build step, no npm.

## Module Structure

The frontend JS has been split from a monolithic `app.js` into focused modules:

| Module | Purpose |
|--------|---------|
| `app.js` | Entry point, event wiring, tab switching |
| `api.js` | `DesktopAPI` / `WebAPI` adapter classes |
| `state.js` | Global `state` object, `onStateUpdate()`, render callbacks |
| `utils.js` | `$`, `$$`, `escHtml`, `renderMarkdown`, `showToast` |
| `setup.js` | New Discussion tab rendering |
| `providers.js` | Providers tab |
| `profiles.js` | Profiles tab (entity editor, tool assignment) |
| `prompts.js` | Prompts tab |
| `history.js` | History tab |
| `discussion.js` | Active discussion view (messages, storyboard) |
| `discussion-actions.js` | Pause/resume, conclude, reassign, mediate |
| `documents.js` | Document panel (upload, URL add, list, remove) |
| `images.js` | Image panel (upload, URL add, grid, lightbox) |
| `experts.js` | MCP expert consultation dialog |
| `mcp.js` | MCP server management dialog |
| `memory.js` | Memory configuration settings |
| `export.js` | JSON/HTML/PDF export |
| `auth.js` | Authentication UI (login/register/OAuth) |
| `byok.js` | BYOK key management UI |

## `index.html` -- Page Structure

Two top-level `<section>` elements, toggled with the `.hidden` class:

- **`#setup-phase`** -- shown before a discussion starts. Contains a tab bar
  with tabs:
  - *New Discussion* -- topic input, entity picker, document panel, image panel,
    discussion method selector, start button
  - *Providers* -- manage API provider endpoints
  - *Profiles* -- manage entity profiles (human/AI), including tool assignments
  - *Prompts* -- edit prompt templates
  - *History* -- browse and load past discussions
  - *Memory* -- memory system configuration (Ollama endpoint, model)

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

## API Adapters (`api.js`)

Two classes (`DesktopAPI` and `WebAPI`) provide the same interface but
communicate differently:
- `DesktopAPI` calls `window.pywebview.api.<method>()` (synchronous Python bridge)
- `WebAPI` uses `fetch('/api/<method>', ...)` (HTTP POST with JSON, or
  multipart for file uploads)

The correct adapter is selected at startup:
```javascript
api = window.pywebview ? new DesktopAPI() : new WebAPI();
```

Both adapters expose methods for tool management, document management
(`uploadDocument()`, `addDocumentByUrl()`, `getDiscussionDocuments()`),
image management (`uploadImage()`, `addImageFromUrl()`,
`getDiscussionImages()`), and all other app methods.

## Global State (`state.js`)

A single `state` object holds the full application state, updated by
`onStateUpdate(newState)`. The state module manages render callbacks and
exports helper functions like `getEntity()`.

### `onStateUpdate(newState)`

Called whenever the backend pushes new state (desktop mode) or after each API
response (web mode). It merges the new state, re-renders all UI panels, and
manages the setup/discussion phase transitions.

### Key rendering functions

| Function | Module | Renders |
|----------|--------|---------|
| `renderProviders()` | `providers.js` | Provider list in the Providers tab |
| `renderProfiles()` | `profiles.js` | Entity profile list in the Profiles tab |
| `renderPrompts()` | `prompts.js` | Prompt template list in the Prompts tab |
| `renderHistory()` | `history.js` | Discussion history list in the History tab |
| `renderSetupTab()` | `setup.js` | New Discussion tab (topic, entities, method) |
| `renderDocumentPanel()` | `documents.js` | Document list, upload, URL input |
| `renderImagePanel()` | `images.js` | Image grid, upload, URL input |
| `renderDiscussionEntities()` | `discussion.js` | Participant sidebar during discussion |
| `renderNewMessages()` | `discussion.js` | Message feed (incremental) |
| `renderNewStoryboard()` | `discussion.js` | Storyboard panel (incremental) |

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

### Document panel (`documents.js`)

The New Discussion tab includes a "Reference Documents" card where users can
attach documents for AI participants to analyse via RAG tools. Supports:
- File upload (PDF, HTML, TXT) via native file picker (desktop) or `<input>`
- URL ingestion (fetches and extracts text)
- Thumbnail list of attached documents with remove buttons

### Image panel (`images.js`)

The New Discussion tab includes a "Reference Images" card where users can
attach images for visual context. Supports:
- File upload (PNG, JPEG, GIF, WebP) via native file picker (desktop) or `<input>`
- URL ingestion (fetches image with SSRF protection)
- Thumbnail grid with lightbox (click to enlarge)
- Remove buttons per image
- Inline message images (`renderMessageImages()` appends thumbnails to messages
  that have `image_ids`)

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

### Authentication UI (multi-user mode)

In multi-user web mode, the frontend manages authentication state through two
globals:

- `authUser` — the current authenticated user object, or `null`
- `authRequired` — whether the server requires authentication

**Bootstrap flow:**
1. `bootstrap()` creates the API adapter
2. In web mode, calls `checkAuthStatus()` which fetches `/auth/status`
3. If `authRequired && !authUser`, shows the login screen
4. Otherwise, initialises the app normally

**UI elements:**
- **`#auth-phase`** — login/register forms with a toggle between them
- **`#user-bar`** — top bar showing the authenticated user's display name and a
  sign-out button
- **OAuth buttons** — rendered dynamically based on configured providers
  returned from `/auth/status`

**401 handling:** `WebAPI._post()` intercepts HTTP 401 responses and redirects
to the login screen automatically.

**Listener guard:** A `_authListenersAttached` flag prevents duplicate event
listeners when toggling between `showAuthPhase()` and `showAppPhase()`.

---

[Next: Database](05-database.md)
