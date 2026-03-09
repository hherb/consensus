/**
 * @module prompts
 * Prompt template management UI — CRUD operations.
 */

import { $, show, hide, showToast, escHtml } from './utils.js';
import { state, onStateUpdate } from './state.js';
import { api } from './api.js';

/**
 * Render the prompts list in the Prompts tab.
 */
export function renderPrompts() {
    const list = $('#prompt-list');
    const prompts = state.prompts || [];
    if (!prompts.length) {
        list.innerHTML = '<div class="empty-state">No prompts defined</div>';
        return;
    }
    list.innerHTML = prompts.map(p => `
        <div class="settings-item">
            <div class="entity-info">
                <div class="entity-name">${escHtml(p.name)} ${p.is_default ? '<span class="moderator-badge">DEFAULT</span>' : ''}</div>
                <div class="settings-detail">${p.role} / ${p.target} / ${p.task}</div>
            </div>
            <div class="entity-actions">
                <button class="btn btn-ghost btn-sm" data-action="edit-prompt" data-id="${p.id}">Edit</button>
                <button class="btn btn-ghost btn-sm" data-action="delete-prompt" data-id="${p.id}">Delete</button>
            </div>
        </div>
    `).join('');
}

/**
 * Open the prompt add/edit dialog.
 * @param {object|null} prompt - Existing prompt to edit, or null for new
 */
export function openPromptDialog(prompt) {
    $('#prompt-dialog-title').textContent = prompt ? 'Edit Prompt' : 'Add Prompt';
    $('#prompt-name').value = prompt?.name || '';
    $('#prompt-role').value = prompt?.role || 'moderator';
    $('#prompt-target').value = prompt?.target || 'ai';
    $('#prompt-task').value = prompt?.task || 'system';
    $('#prompt-content').value = prompt?.content || '';
    $('#prompt-edit-id').value = prompt?.id || '';
    show('#prompt-dialog');
    $('#prompt-name').focus();
}

/**
 * Confirm and save prompt from dialog form.
 */
export async function confirmPrompt() {
    const name = $('#prompt-name').value.trim();
    const content = $('#prompt-content').value.trim();
    if (!name || !content) return showToast('Name and content are required');

    await api.savePrompt({
        prompt_id: $('#prompt-edit-id').value,
        name,
        role: $('#prompt-role').value,
        target: $('#prompt-target').value,
        task: $('#prompt-task').value,
        content,
    });
    const s = await api.getState();
    onStateUpdate(s);
    hide('#prompt-dialog');
    renderPrompts();
}

/**
 * Open edit dialog for an existing prompt.
 * @param {number} id - Prompt ID
 */
export async function editPrompt(id) {
    const p = (state.prompts || []).find(x => x.id === id);
    if (p) openPromptDialog(p);
}

/**
 * Delete a prompt.
 * @param {number} id - Prompt ID
 */
export async function removePrompt(id) {
    await api.deletePrompt(id);
    const s = await api.getState();
    onStateUpdate(s);
    renderPrompts();
}
