# Delete History Discussions + App Icon Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add bulk soft-delete for discussions in History tab with auto-purge, and set app icon for desktop mode.

**Architecture:** Soft delete via `deleted_at` column on `discussions` table, filtered in `get_discussions()`. Purge on startup removes rows older than 7 days. Frontend adds checkboxes + bulk delete with undo toast. App icon passed to pywebview's `create_window()`.

**Tech Stack:** Python/SQLite backend, vanilla JS frontend, pywebview desktop

---

### Task 1: Database Migration — Add `deleted_at` Column

**Files:**
- Modify: `consensus/database.py:39` (add migration call)
- Modify: `consensus/database.py` (add `_migrate_discussion_deleted_at` method after `_migrate_tools`)

**Step 1: Add migration method**

In `consensus/database.py`, after the `_migrate_tools` method (~line 606), add:

```python
def _migrate_discussion_deleted_at(self) -> None:
    """Add deleted_at column to discussions for soft-delete support."""
    cols = {row[1] for row in self.conn.execute("PRAGMA table_info(discussions)")}
    if "deleted_at" not in cols:
        with self._lock:
            self.conn.execute(
                "ALTER TABLE discussions ADD COLUMN deleted_at REAL"
            )
            self.conn.commit()
```

**Step 2: Call the migration in `__init__`**

In `consensus/database.py` line 42, after `self._migrate_tools()`, add:

```python
self._migrate_discussion_deleted_at()
```

**Step 3: Verify**

Run: `python -c "from consensus.database import Database; db = Database(); print('migration ok')"`

**Step 4: Commit**

```bash
git add consensus/database.py
git commit -m "Add deleted_at column migration for discussion soft-delete"
```

---

### Task 2: Database Methods — Soft Delete, Restore, Purge

**Files:**
- Modify: `consensus/database.py:920-925` (update `get_discussions`)
- Modify: `consensus/database.py` (add new methods after `update_discussion` ~line 940)

**Step 1: Filter soft-deleted from `get_discussions()`**

Change `get_discussions()` (line 920-925) from:

```python
def get_discussions(self) -> list[dict]:
    """Return all discussions ordered by start time (newest first)."""
    return [dict(r) for r in
            self.conn.execute(
                "SELECT * FROM discussions ORDER BY started_at DESC"
            ).fetchall()]
```

To:

```python
def get_discussions(self) -> list[dict]:
    """Return non-deleted discussions ordered by start time (newest first)."""
    return [dict(r) for r in
            self.conn.execute(
                "SELECT * FROM discussions "
                "WHERE deleted_at IS NULL "
                "ORDER BY started_at DESC"
            ).fetchall()]
```

**Step 2: Add soft-delete, restore, and purge methods**

After `update_discussion()` (~line 940), add:

```python
MAX_DAYS_KEEP_DELETED = 7

def soft_delete_discussions(self, discussion_ids: list[int]) -> int:
    """Soft-delete discussions by setting deleted_at. Returns count deleted."""
    if not discussion_ids:
        return 0
    placeholders = ",".join("?" * len(discussion_ids))
    with self._lock:
        cur = self.conn.execute(
            f"UPDATE discussions SET deleted_at = ? "
            f"WHERE id IN ({placeholders}) AND deleted_at IS NULL",
            (time.time(), *discussion_ids),
        )
        self.conn.commit()
    return cur.rowcount

def restore_discussion(self, discussion_id: int) -> bool:
    """Restore a soft-deleted discussion."""
    with self._lock:
        cur = self.conn.execute(
            "UPDATE discussions SET deleted_at = NULL WHERE id = ? AND deleted_at IS NOT NULL",
            (discussion_id,),
        )
        self.conn.commit()
    return cur.rowcount > 0

def purge_deleted_discussions(self, max_days: int = MAX_DAYS_KEEP_DELETED) -> int:
    """Hard-delete discussions soft-deleted more than max_days ago.

    Cascades to messages, discussion_members, and storyboard_entries.
    Returns count of discussions purged.
    """
    cutoff = time.time() - (max_days * 86400)
    with self._lock:
        # Find IDs to purge
        ids = [r[0] for r in self.conn.execute(
            "SELECT id FROM discussions WHERE deleted_at IS NOT NULL AND deleted_at < ?",
            (cutoff,),
        ).fetchall()]
        if not ids:
            return 0
        placeholders = ",".join("?" * len(ids))
        self.conn.execute(f"DELETE FROM storyboard_entries WHERE discussion_id IN ({placeholders})", ids)
        self.conn.execute(f"DELETE FROM messages WHERE discussion_id IN ({placeholders})", ids)
        self.conn.execute(f"DELETE FROM discussion_members WHERE discussion_id IN ({placeholders})", ids)
        self.conn.execute(f"DELETE FROM discussions WHERE id IN ({placeholders})", ids)
        self.conn.commit()
    return len(ids)
```

**Step 3: Verify**

Run: `python -c "from consensus.database import Database; db = Database(); print(db.get_discussions()); print('methods ok')"`

**Step 4: Commit**

```bash
git add consensus/database.py
git commit -m "Add soft-delete, restore, and purge methods for discussions"
```

---

### Task 3: Backend — App Methods + Startup Purge

**Files:**
- Modify: `consensus/app.py` (add methods after `load_discussion` ~line 798)
- Modify: `consensus/app.py` (add purge call in `__init__`)

**Step 1: Add purge call in ConsensusApp.__init__**

Find `__init__` in `consensus/app.py` and add after database initialization:

```python
self.db.purge_deleted_discussions()
```

**Step 2: Add `delete_discussions` and `restore_discussion` methods**

In `consensus/app.py`, after `load_discussion()` (~line 798) and before `pause_discussion()`:

```python
def delete_discussions(self, discussion_ids: list[int]) -> dict:
    """Soft-delete discussions by IDs. Returns count and state."""
    count = self.db.soft_delete_discussions(discussion_ids)
    return {"deleted": count, "state": self.get_state()}

def restore_discussion(self, discussion_id: int) -> dict:
    """Restore a soft-deleted discussion."""
    restored = self.db.restore_discussion(discussion_id)
    return {"restored": restored, "state": self.get_state()}
```

**Step 3: Commit**

```bash
git add consensus/app.py
git commit -m "Add delete/restore discussion methods and startup purge"
```

---

### Task 4: Desktop Bridge + Server Routes

**Files:**
- Modify: `consensus/desktop.py:241-248` (add methods in History section)
- Modify: `consensus/server.py:296-299` (add handler entries)

**Step 1: Add to DesktopBridge**

In `consensus/desktop.py`, after `load_discussion()` (line 244) and before `reset()`:

```python
def delete_discussions(self, discussion_ids: list) -> dict:
    """Soft-delete discussions by IDs."""
    return self.app.delete_discussions([int(i) for i in discussion_ids])

def restore_discussion(self, discussion_id: int) -> dict:
    """Restore a soft-deleted discussion."""
    return self.app.restore_discussion(int(discussion_id))
```

**Step 2: Add server handler entries**

In `consensus/server.py`, in the `handlers` dict (~line 298), after the `load_discussion` entry:

```python
"delete_discussions": lambda: app.delete_discussions(
    data["discussion_ids"]),
"restore_discussion": lambda: app.restore_discussion(
    data["discussion_id"]),
```

**Step 3: Commit**

```bash
git add consensus/desktop.py consensus/server.py
git commit -m "Expose delete/restore discussions via desktop bridge and web API"
```

---

### Task 5: Frontend — API Adapters

**Files:**
- Modify: `consensus/static/app.js:39-41` (DesktopAPI, add after loadDiscussion)
- Modify: `consensus/static/app.js:97-98` (WebAPI, add after loadDiscussion)

**Step 1: Add to DesktopAPI**

After `async loadDiscussion(id)` (line 40):

```javascript
async deleteDiscussions(ids) { return await window.pywebview.api.delete_discussions(ids); }
async restoreDiscussion(id) { return await window.pywebview.api.restore_discussion(id); }
```

**Step 2: Add to WebAPI**

After `async loadDiscussion(id)` (line 97):

```javascript
async deleteDiscussions(ids) { return await this._post('delete_discussions', { discussion_ids: ids }); }
async restoreDiscussion(id) { return await this._post('restore_discussion', { discussion_id: id }); }
```

**Step 3: Commit**

```bash
git add consensus/static/app.js
git commit -m "Add deleteDiscussions/restoreDiscussion to frontend API adapters"
```

---

### Task 6: Frontend — History Tab UI with Checkboxes and Bulk Delete

**Files:**
- Modify: `consensus/static/app.js:696-727` (renderHistory function)
- Modify: `consensus/static/app.js` (add new functions after renderHistory)
- Modify: `consensus/static/app.js:1766-1793` (event delegation switch)

**Step 1: Rewrite `renderHistory()` with checkboxes**

Replace the `renderHistory()` function (lines 696-727):

```javascript
function renderHistory() {
    const list = $('#history-list');
    const discussions = state.discussions_history || [];
    if (!discussions.length) {
        list.innerHTML = '<div class="empty-state">No past discussions</div>';
        return;
    }
    const header = `
        <div class="history-toolbar">
            <label class="history-select-all"><input type="checkbox" id="select-all-discussions"> Select all</label>
            <button class="btn btn-danger btn-sm hidden" id="delete-selected-btn" data-action="delete-selected">Delete selected</button>
        </div>`;
    const rows = discussions.map(d => {
        const canResume = d.status === 'active' || d.status === 'paused';
        const btnLabel = canResume ? 'Resume' : 'View';
        const btnClass = canResume ? 'btn btn-primary btn-sm' : 'btn btn-outline btn-sm';
        const showReopen = d.status === 'concluded';
        return `
        <div class="history-item">
            <input type="checkbox" class="history-checkbox" data-id="${d.id}">
            <div style="flex:1;cursor:pointer" data-action="load-discussion" data-id="${d.id}">
                <div class="history-topic">${escHtml(d.topic)}</div>
                <div class="history-meta">${d.started_at ? formatDate(d.started_at) : 'Not started'}</div>
            </div>
            <span class="history-status ${d.status}">${d.status}</span>
            <button class="${btnClass}" data-action="load-discussion" data-id="${d.id}">${btnLabel}</button>
            ${showReopen ? `<button class="btn btn-primary btn-sm" data-action="reopen-discussion" data-id="${d.id}">Resume</button>` : ''}
            <div class="export-dropdown">
                <button class="history-export-btn" data-action="toggle-history-export" data-id="${d.id}" title="Export">Export &#9662;</button>
                <div id="history-export-menu-${d.id}" class="history-export-menu hidden">
                    <button data-action="export-history-json" data-id="${d.id}" class="export-option">JSON</button>
                    <button data-action="export-history-html" data-id="${d.id}" class="export-option">HTML</button>
                    <button data-action="export-history-pdf" data-id="${d.id}" class="export-option">PDF</button>
                </div>
            </div>
        </div>`;
    }).join('');
    list.innerHTML = header + rows;

    // Wire up select-all and checkbox change events
    const selectAll = $('#select-all-discussions');
    if (selectAll) {
        selectAll.addEventListener('change', () => {
            $$('.history-checkbox').forEach(cb => cb.checked = selectAll.checked);
            updateDeleteSelectedBtn();
        });
    }
    list.addEventListener('change', (e) => {
        if (e.target.classList.contains('history-checkbox')) updateDeleteSelectedBtn();
    });
}

function updateDeleteSelectedBtn() {
    const btn = $('#delete-selected-btn');
    if (!btn) return;
    const checked = $$('.history-checkbox:checked');
    const selectAll = $('#select-all-discussions');
    const allBoxes = $$('.history-checkbox');
    if (selectAll) selectAll.checked = allBoxes.length > 0 && checked.length === allBoxes.length;
    if (checked.length > 0) { btn.classList.remove('hidden'); btn.textContent = `Delete selected (${checked.length})`; }
    else btn.classList.add('hidden');
}

async function deleteSelectedDiscussions() {
    const ids = [...$$('.history-checkbox:checked')].map(cb => Number(cb.dataset.id));
    if (!ids.length) return;
    if (!confirm(`Delete ${ids.length} discussion(s)? They will be recoverable for 7 days.`)) return;
    const result = await api.deleteDiscussions(ids);
    if (result?.error) return showToast(result.error);
    showUndoToast(ids.length, ids);
}

function showUndoToast(count, ids) {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();
    const toast = document.createElement('div');
    toast.className = 'toast toast-success';
    toast.innerHTML = `${count} discussion(s) deleted. <button class="toast-undo-btn">Undo</button>`;
    document.body.appendChild(toast);
    const undoBtn = toast.querySelector('.toast-undo-btn');
    undoBtn.addEventListener('click', async () => {
        for (const id of ids) await api.restoreDiscussion(id);
        toast.remove();
        renderHistory();
    });
    setTimeout(() => { toast.classList.add('toast-fade-out'); setTimeout(() => toast.remove(), 300); }, 6000);
}
```

**Step 2: Add `delete-selected` to event delegation**

In the event delegation switch (~line 1793), add before the closing `}`:

```javascript
case 'delete-selected': deleteSelectedDiscussions(); break;
```

**Step 3: Commit**

```bash
git add consensus/static/app.js
git commit -m "Add bulk delete UI with checkboxes, confirmation, and undo toast"
```

---

### Task 7: Frontend — CSS for Delete UI

**Files:**
- Modify: `consensus/static/style.css` (add after history styles ~line 269)

**Step 1: Add CSS**

After the `.history-status.paused` rule (~line 269), add:

```css
.history-toolbar {
    display: flex;
    align-items: center;
    gap: 0.8rem;
    padding: 0.5rem 0.5rem 0.3rem;
    border-bottom: 1px solid var(--border);
}
.history-select-all {
    display: flex;
    align-items: center;
    gap: 0.4rem;
    font-size: 0.8rem;
    color: var(--text-muted);
    cursor: pointer;
}
.history-checkbox {
    flex-shrink: 0;
    cursor: pointer;
}
.btn-danger {
    background: var(--error);
    color: #fff;
    border: none;
}
.btn-danger:hover { opacity: 0.85; }
.toast-undo-btn {
    background: none;
    border: 1px solid currentColor;
    color: inherit;
    padding: 0.15rem 0.5rem;
    border-radius: 4px;
    margin-left: 0.6rem;
    cursor: pointer;
    font-size: 0.8rem;
}
.toast-undo-btn:hover { opacity: 0.8; }
```

**Step 2: Commit**

```bash
git add consensus/static/style.css
git commit -m "Add CSS for history bulk delete toolbar, checkboxes, and undo toast"
```

---

### Task 8: App Icon for Desktop Mode

**Files:**
- Modify: `consensus/desktop.py:260-270` (add icon parameter)
- Modify: `pyproject.toml:31` (include assets in package data)

**Step 1: Add icon to `create_window`**

In `consensus/desktop.py`, modify `launch_desktop()` (lines 260-270):

```python
def launch_desktop(debug: bool = False) -> None:
    """Launch the desktop application using pywebview."""
    import webview
    from .config import load_env
    load_env()

    app = ConsensusApp()
    bridge = DesktopBridge(app)

    pkg_dir = os.path.dirname(__file__)
    static_dir = os.path.join(pkg_dir, "static")
    html_path = os.path.join(static_dir, "index.html")
    icon_path = os.path.join(pkg_dir, "..", "assets", "consensus_icon.png")
    icon_path = os.path.normpath(icon_path)

    window = webview.create_window(
        WINDOW_TITLE,
        html_path,
        js_api=bridge,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT),
    )
    bridge._window = window

    webview.start(debug=debug)
```

Wait — the icon needs to be inside the package for distribution. Let me use a better approach: put the icon in `consensus/static/` or reference it via the assets dir and include it in package data.

Actually, the simplest approach: keep icon in `assets/` at repo root, reference relative to `__file__`, and add `assets/*` to package-data via a wildcard for the parent. But `pyproject.toml` package-data is keyed to the `consensus` package. Better to copy the icon into the consensus package directory.

Revised approach: reference the icon from the `assets/` dir relative to the repo root (works for development installs via `pip install -e .` which is this project's mode). For proper distribution, the icon would need to be inside the package — but that's a future concern.

```python
    pkg_dir = os.path.dirname(__file__)
    static_dir = os.path.join(pkg_dir, "static")
    html_path = os.path.join(static_dir, "index.html")

    # Icon path — relative to package dir, up one level to repo assets/
    icon_path = os.path.normpath(os.path.join(pkg_dir, "..", "assets", "consensus_icon.png"))
    icon_kwarg = {"icon": icon_path} if os.path.exists(icon_path) else {}

    window = webview.create_window(
        WINDOW_TITLE,
        html_path,
        js_api=bridge,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        min_size=(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT),
        **icon_kwarg,
    )
```

**Step 2: Commit**

```bash
git add consensus/desktop.py
git commit -m "Set app icon for desktop mode from assets/consensus_icon.png"
```

---

### Task 9: Manual Smoke Test

**Step 1:** Run `python -m consensus` (desktop mode)
- Verify icon appears in taskbar/dock
- Go to History tab
- Verify checkboxes appear on each discussion row
- Select some discussions, verify "Delete selected (N)" button appears
- Click delete, confirm dialog, verify discussions disappear
- Verify undo toast appears and works
- Restart app, verify deleted items stay hidden
- Wait 7+ days (or temporarily set `MAX_DAYS_KEEP_DELETED = 0` for testing) and verify purge works on startup

**Step 2:** Run `python -m consensus --web` (web mode)
- Verify same delete functionality works via REST API

**Step 3: Final commit**

```bash
git add -A
git commit -m "Delete history discussions (soft-delete with bulk UI) and desktop app icon"
```
