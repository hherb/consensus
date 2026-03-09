/**
 * @module providers
 * Provider management UI — CRUD operations and BYOK key dialogs.
 */

import { $, show, hide, showToast, escHtml } from './utils.js';
import { state, onStateUpdate } from './state.js';
import { api } from './api.js';
import { hasByokKey, setByokKey } from './byok.js';
import { renderMcpServers } from './mcp.js';

/**
 * Render the providers list in the Providers tab.
 */
export function renderProviders() {
    const list = $('#provider-list');
    const providers = state.providers || [];
    if (!providers.length) {
        list.innerHTML = '<div class="empty-state">No providers configured yet</div>';
        return;
    }
    renderMcpServers();
    const isWeb = !window.pywebview;
    list.innerHTML = providers.map(p => {
        const byok = hasByokKey(p.id);
        let keyStatus;
        if (byok) {
            keyStatus = '<span style="color:var(--color-success)">Session key set</span>';
        } else if (p.has_key) {
            keyStatus = '<span style="color:var(--color-success)">Server key configured</span>';
        } else {
            keyStatus = '<em>Not set</em>';
        }
        return `
        <div class="settings-item">
            <div class="entity-info">
                <div class="entity-name">${escHtml(p.name)}</div>
                <div class="settings-detail">${escHtml(p.base_url)}</div>
                <div class="settings-detail">API Key: ${keyStatus}</div>
            </div>
            <div class="entity-actions">
                ${isWeb ? `<button class="btn btn-outline btn-sm" data-action="set-byok" data-id="${p.id}">Set Key</button>` : ''}
                <button class="btn btn-ghost btn-sm" data-action="edit-provider" data-id="${p.id}">Edit</button>
                <button class="btn btn-ghost btn-sm" data-action="delete-provider" data-id="${p.id}">Delete</button>
            </div>
        </div>
    `;}).join('');
}

/**
 * Open the provider add/edit dialog.
 * @param {object|null} provider - Existing provider to edit, or null for new
 */
export function openProviderDialog(provider) {
    $('#provider-dialog-title').textContent = provider ? 'Edit Provider' : 'Add Provider';
    $('#prov-name').value = provider?.name || '';
    $('#prov-url').value = provider?.base_url || '';
    $('#prov-key-env').value = provider?.api_key_env || '';
    $('#prov-api-key').value = '';
    $('#prov-edit-id').value = provider?.id || '';
    const hint = $('#prov-key-hint');
    if (provider?.has_key) {
        hint.textContent = 'Leave blank to keep current key, or enter new key to replace';
    } else {
        hint.textContent = 'Enter the API key for this provider';
    }
    show('#provider-dialog');
    $('#prov-name').focus();
}

/**
 * Confirm and save provider from dialog form.
 */
export async function confirmProvider() {
    const name = $('#prov-name').value.trim();
    const url = $('#prov-url').value.trim();
    if (!name || !url) return showToast('Name and URL are required');
    const keyEnv = $('#prov-key-env').value.trim();
    const apiKey = $('#prov-api-key').value.trim();
    const editId = $('#prov-edit-id').value;

    if (editId) {
        await api.updateProvider(editId, name, url, keyEnv, apiKey);
    } else {
        await api.addProvider(name, url, keyEnv, apiKey);
    }
    const s = await api.getState();
    onStateUpdate(s);
    hide('#provider-dialog');
    renderProviders();
}

/**
 * Open edit dialog for an existing provider.
 * @param {number} id - Provider ID
 */
export async function editProvider(id) {
    const p = (state.providers || []).find(x => x.id === id);
    if (p) openProviderDialog(p);
}

/**
 * Delete a provider.
 * @param {number} id - Provider ID
 */
export async function removeProvider(id) {
    await api.deleteProvider(id);
    const s = await api.getState();
    onStateUpdate(s);
    renderProviders();
}

/**
 * Show the BYOK key input dialog for a provider.
 * @param {number} providerId
 */
export function promptByokKey(providerId) {
    const provider = (state.providers || []).find(p => p.id === providerId);
    const name = provider ? provider.name : 'this provider';
    const existing = hasByokKey(providerId);
    $('#byok-dialog-title').textContent = existing ? `Update API Key — ${name}` : `Set API Key — ${name}`;
    $('#byok-dialog-desc').textContent = existing
        ? 'Update or remove the session key for this provider.'
        : 'Enter your API key for this provider.';
    $('#byok-key-input').value = '';
    $('#byok-provider-id').value = providerId;
    if (existing) { show('#byok-remove-btn'); } else { hide('#byok-remove-btn'); }
    show('#byok-dialog');
    $('#byok-key-input').focus();
}

/**
 * Confirm and save the BYOK key from the dialog.
 */
export function confirmByokKey() {
    const providerId = $('#byok-provider-id').value;
    const key = $('#byok-key-input').value.trim();
    const provider = (state.providers || []).find(p => String(p.id) === String(providerId));
    const name = provider ? provider.name : 'this provider';
    if (key) {
        setByokKey(providerId, key);
        showToast(`API key set for ${name} (this session only)`, 3000, 'info');
    }
    hide('#byok-dialog');
    renderProviders();
}

/**
 * Remove the BYOK key for the provider in the current dialog.
 */
export function removeByokKey() {
    const providerId = $('#byok-provider-id').value;
    const provider = (state.providers || []).find(p => String(p.id) === String(providerId));
    const name = provider ? provider.name : 'this provider';
    setByokKey(providerId, '');
    showToast(`API key removed for ${name}`, 3000, 'info');
    hide('#byok-dialog');
    renderProviders();
}
