/**
 * @module history
 * Discussion history tab — listing, deletion, undo, and loading.
 */

import { $, $$, show, hide, showToast, escHtml, formatDate } from './utils.js';
import { state, onStateUpdate, resetRenderedMessageCount, resetRenderedStoryboardCount } from './state.js';
import { api } from './api.js';
import { renderDiscussion } from './discussion.js';
import { processCurrentTurn } from './discussion-actions.js';

const UNDO_TOAST_DURATION_MS = 6000;
const TOAST_FADE_DELAY_MS = 300;

/**
 * Render the history list in the History tab.
 */
export function renderHistory() {
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

/**
 * Update visibility of the "Delete selected" button based on checked items.
 */
export function updateDeleteSelectedBtn() {
    const btn = $('#delete-selected-btn');
    if (!btn) return;
    const checked = $$('.history-checkbox:checked');
    const selectAll = $('#select-all-discussions');
    const allBoxes = $$('.history-checkbox');
    if (selectAll) selectAll.checked = allBoxes.length > 0 && checked.length === allBoxes.length;
    if (checked.length > 0) { btn.classList.remove('hidden'); btn.textContent = `Delete selected (${checked.length})`; }
    else btn.classList.add('hidden');
}

/**
 * Delete all selected discussions with confirmation and undo.
 */
export async function deleteSelectedDiscussions() {
    const ids = [...$$('.history-checkbox:checked')].map(cb => Number(cb.dataset.id));
    if (!ids.length) return;
    if (!confirm(`Delete ${ids.length} discussion(s)? They will be recoverable for 7 days.`)) return;
    const result = await api.deleteDiscussions(ids);
    if (result?.error) return showToast(result.error);
    if (result?.state) onStateUpdate(result.state);
    else { const s = await api.getState(); if (s) onStateUpdate(s); }
    renderHistory();
    showUndoToast(ids.length, ids);
}

/**
 * Show a toast with an undo button for deleted discussions.
 * @param {number} count - Number of deleted discussions
 * @param {Array<number>} ids - Discussion IDs
 */
function showUndoToast(count, ids) {
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();
    const toast = document.createElement('div');
    toast.className = 'toast toast-success';
    toast.innerHTML = `${count} discussion(s) deleted. <button class="toast-undo-btn">Undo</button>`;
    document.body.appendChild(toast);
    const undoBtn = toast.querySelector('.toast-undo-btn');
    undoBtn.addEventListener('click', async () => {
        let lastResult;
        for (const id of ids) lastResult = await api.restoreDiscussion(id);
        toast.remove();
        if (lastResult?.state) onStateUpdate(lastResult.state);
        else { const s = await api.getState(); if (s) onStateUpdate(s); }
        renderHistory();
    });
    setTimeout(() => {
        toast.classList.add('toast-fade-out');
        setTimeout(() => toast.remove(), TOAST_FADE_DELAY_MS);
    }, UNDO_TOAST_DURATION_MS);
}

/**
 * Load a discussion from history and display it.
 * @param {number} id - Discussion ID
 */
export async function loadDiscussion(id) {
    const result = await api.loadDiscussion(id);
    if (result?.error) return showToast(result.error);
    onStateUpdate(result);
    hide('#setup-phase');
    show('#discussion-phase');
    resetRenderedMessageCount(0);
    resetRenderedStoryboardCount(0);
    renderDiscussion();
    if (state.is_active) processCurrentTurn();
}
