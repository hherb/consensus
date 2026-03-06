# 10. Contributing

[Back to index](programmer-manual.md) | [Previous: Prompts, Providers, and Security](09-prompts-providers-security.md)

---

## Common Tasks for New Contributors

### Adding a new backend feature

1. Add the business logic method to `ConsensusApp` in `app.py`
2. Expose it in **both**:
   - `server.py` -- add an entry to the `handlers` dict in `handle_api()`
   - `desktop.py` -- add a method to `DesktopBridge` (use `_run_async()` if
     async)
3. If it needs new UI, add the corresponding call to the API adapter classes
   (`DesktopAPI` and `WebAPI`) in `app.js`

### Adding a new database table or column

1. Add the `CREATE TABLE` or `ALTER TABLE` statement to `_create_tables()` or
   a new `_migrate_*()` method in `database.py`
2. Add CRUD methods to `Database`
3. Expose through `ConsensusApp`, `server.py`, and `desktop.py` as needed

### Adding a new prompt template

Add it to the `defaults` list in `Database._seed_default_prompts()`. Note that
seeds only run when the prompts table is empty (fresh database), so existing
databases won't get new defaults automatically. Consider adding a migration
for existing users.

### Adding a new tool

1. Write a handler function (sync or async) that accepts
   `(arguments: dict, context: ToolContext)` and returns a `ToolResult`
2. Create a `PythonToolProvider` and register the tool with a `ToolDefinition`
3. Register the provider in `ConsensusApp._init_builtin_tools()`
4. Users assign the tool to entities via the Profiles tab UI

See [Tool Use](08-tool-use.md) for detailed examples.

### Modifying the frontend

- Edit `static/app.js` for logic changes
- Edit `static/style.css` for styling changes
- Edit `static/index.html` for structural changes
- No build step needed; just refresh the browser / restart the app

### Debugging tips

- **Web mode with `--debug`:** Not currently wired to aiohttp's debug mode,
  but you can add `logging.basicConfig(level=logging.DEBUG)` to `__main__.py`
- **Desktop mode with `--debug`:** Passes `debug=True` to `webview.start()`,
  which enables browser developer tools in the webview
- **Database inspection:** The SQLite file is at the path returned by
  `config.get_db_path()`. Open it with `sqlite3` or any SQLite browser.
- **AI request debugging:** `AIClient` uses Python's `logging` module with
  `logger.debug()` calls for failed requests
- **Tool debugging:** Tool execution results (including errors) are recorded in
  `ToolCallRecord` objects and stored in `messages.tool_calls_json`. Inspect
  message records in the database to see tool call history.

---

## Conventions and Patterns

### Python

- **Dataclasses for data, classes for behaviour.** Domain objects in
  `models.py` are pure data. Behaviour lives in `Moderator`, `ConsensusApp`,
  etc.
- **Async by default.** All AI-calling and HTTP code is async. Desktop mode
  bridges async to sync with `run_coroutine_threadsafe`.
- **Error returns over exceptions.** Most `ConsensusApp` methods return
  `{"error": "..."}` dicts rather than raising exceptions. Exceptions from
  the AI layer are caught and converted to error dicts.
- **Thread safety via lock.** Only one write lock in `Database`. Reads are
  lock-free (SQLite WAL handles concurrent reads).
- **No type annotations on `kwargs`.** The codebase uses `**kwargs: object` for
  generic update methods.

### JavaScript

- **No framework, no build step.** The entire frontend is one JS file.
- **`$` and `$$` helpers.** `$('#id')` is `document.querySelector`,
  `$$('.class')` is `querySelectorAll`.
- **Incremental rendering.** Messages and storyboard entries track how many
  have been rendered and only add new ones. Full re-renders happen for setup
  panels.
- **API adapter pattern.** `DesktopAPI` and `WebAPI` provide the same
  interface, selected at startup based on `window.pywebview` detection.

### State flow

```
Backend mutation
    --> app.get_state() builds full state dict
    --> Sent to frontend (HTTP response or evaluate_js)
    --> onStateUpdate(newState) in app.js
    --> Selective UI re-rendering
```

The frontend never has partial state. Every update receives the entire
application state.

---

## Current Limitations and Future Work

These are known gaps that represent good contribution opportunities:

- **No test suite.** No unit tests, integration tests, or end-to-end tests
  exist. The project would benefit from pytest-based tests for the backend
  modules.
- **No linter or formatter configuration.** No ruff, black, flake8, or mypy
  config.
- **No CI/CD pipeline.** No GitHub Actions or similar.
- **No streaming responses.** `AIClient.stream()` exists but is unused. The
  frontend doesn't handle streaming display.
- **No WebSocket for real-time updates.** In web mode, the frontend only
  receives state updates via HTTP response bodies. There's no push channel
  (the desktop mode does have push via `evaluate_js`).
- **No authentication or authorisation.** Multi-user mode provides session
  isolation and rate limiting, but there is no user login, account system,
  or role-based access control.
- **No MCP tool providers yet.** The `ToolProvider` ABC is designed for future
  MCP (Model Context Protocol) integration, but only `PythonToolProvider` is
  currently implemented.
- **Markdown rendering is basic.** The `renderMarkdown()` function handles
  common cases but doesn't cover the full CommonMark spec.
- **No internationalisation.** All UI text is hardcoded in English.

---

*This manual reflects the codebase as of March 2026. If you find it out of
date, please update it as part of your contribution.*
