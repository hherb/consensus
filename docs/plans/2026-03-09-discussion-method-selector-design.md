# Discussion Method Selector UI — Design

## Problem
The backend supports multiple discussion methods (open_discussion, ACH, belief_diffusion) with full API endpoints, but the frontend has no UI to select them. Users are stuck with the default open_discussion method.

## Approach
Add a native `<select>` dropdown to the existing options card in the New Discussion tab, with a description line below that updates on selection.

## Changes

### api.js
Add to both DesktopAPI and WebAPI:
- `listDiscussionMethods()` → backend `list_discussion_methods`
- `setDiscussionMethod(name)` → backend `set_discussion_method`

### index.html
In the options card (line ~126), add:
- `<select id="discussion-method">` dropdown with label
- `<span id="method-description">` for description text below

### setup.js
- `loadDiscussionMethods()` — fetch methods, populate select, cache list
- `onMethodChange()` — update description + call API
- Called from `renderSetupTab()`

### app.js
- Wire change event listener for `#discussion-method`

## Data flow
1. Setup tab render → `loadDiscussionMethods()` fetches method metadata
2. Populates `<select>` with display_name, value = name
3. Default = "open_discussion"
4. On change → description updates + `api.setDiscussionMethod()` called

## No backend changes needed
Endpoints already exist in server.py and desktop.py.
