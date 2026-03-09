/**
 * @module profiles
 * Entity profile management UI — CRUD, tool assignments, inactive profiles.
 */

import { $, show, hide, showToast, escHtml, getInitials } from './utils.js';
import { state, onStateUpdate } from './state.js';
import { api } from './api.js';
import { renderSetupTab } from './setup.js';

/** @type {Array<object>} Available tools for assignment */
let _availableTools = [];

/** @type {Object<string, string>} Current entity tool assignments: { tool_name: access_mode } */
let _entityToolAssignments = {};

/**
 * Render the active entity profiles list.
 */
export function renderProfiles() {
    const list = $('#profile-list');
    const entities = state.saved_entities || [];
    if (!entities.length) {
        list.innerHTML = '<div class="empty-state">No profiles created yet</div>';
        return;
    }
    list.innerHTML = entities.map(e => `
        <div class="settings-item">
            <div class="entity-avatar" style="background:${e.avatar_color}">${getInitials(e.name)}</div>
            <div class="entity-info">
                <div class="entity-name">${escHtml(e.name)}</div>
                <div class="entity-type">${e.entity_type === 'ai'
                    ? 'AI - ' + escHtml(e.model || 'LLM') + (e.provider_name ? ' via ' + escHtml(e.provider_name) : '')
                    : 'Human'}</div>
            </div>
            <div class="entity-actions">
                <button class="btn btn-ghost btn-sm" data-action="edit-profile" data-id="${e.id}">Edit</button>
                <button class="btn btn-ghost btn-sm" data-action="delete-profile" data-id="${e.id}">Delete</button>
            </div>
        </div>
    `).join('');
}

/**
 * Load and populate model dropdown for a given provider.
 * @param {number|string} providerId
 * @param {string} currentModel - Currently selected model name
 */
export async function loadModelsForProvider(providerId, currentModel) {
    const modelSelect = $('#ai-model');
    const customInput = $('#ai-model-custom');
    modelSelect.innerHTML = '<option value="">Loading models...</option>';
    customInput.value = '';

    if (!providerId) {
        modelSelect.innerHTML = '<option value="">-- Select a provider first --</option>';
        return;
    }

    let models = [];
    try {
        models = await api.fetchModels(providerId);
    } catch (e) { /* ignore fetch errors */ }

    if (models && models.length > 0) {
        const currentInList = currentModel && models.includes(currentModel);
        modelSelect.innerHTML =
            '<option value="">-- Select Model --</option>' +
            models.map(m =>
                `<option value="${escHtml(m)}" ${m === currentModel ? 'selected' : ''}>${escHtml(m)}</option>`
            ).join('');
        if (currentModel && !currentInList) {
            customInput.value = currentModel;
        }
    } else {
        modelSelect.innerHTML = '<option value="">No models found</option>';
        if (currentModel) customInput.value = currentModel;
    }
}

/**
 * Select a color swatch in the entity dialog.
 * @param {string} color - Hex color value
 */
export function selectColorSwatch(color) {
    const hex = color || '#3b82f6';
    $('#entity-color-hex').value = hex;
    document.querySelectorAll('#color-swatches .color-swatch').forEach(s => {
        s.classList.toggle('selected', s.dataset.color === hex);
    });
}

/**
 * Open the entity add/edit dialog.
 * @param {object|null} entity - Existing entity to edit, or null for new
 */
export function openEntityDialog(entity) {
    $('#entity-dialog-title').textContent = entity ? 'Edit Profile' : 'Add Profile';
    $('#entity-name').value = entity?.name || '';
    $('#entity-type').value = entity?.entity_type || 'human';
    const color = entity?.avatar_color || '#3b82f6';
    selectColorSwatch(color);
    $('#entity-edit-id').value = entity?.id || '';

    const provSelect = $('#ai-provider');
    provSelect.innerHTML = '<option value="">-- Select Provider --</option>' +
        (state.providers || []).map(p =>
            `<option value="${p.id}" ${entity?.provider_id === p.id ? 'selected' : ''}>${escHtml(p.name)}</option>`
        ).join('');

    $('#ai-model').innerHTML = '<option value="">-- Select a provider first --</option>';
    $('#ai-model-custom').value = '';

    if ((entity?.entity_type || 'human') === 'ai') {
        show('#ai-config');
        $('#ai-temperature').value = entity?.temperature ?? 0.7;
        $('#ai-max-tokens').value = entity?.max_tokens ?? 1024;
        $('#ai-system-prompt').value = entity?.system_prompt || '';
        if (entity?.provider_id) {
            loadModelsForProvider(entity.provider_id, entity.model || '');
        }
        loadEntityTools(entity?.id);
    } else {
        hide('#ai-config');
    }

    show('#entity-dialog');
    $('#entity-name').focus();
}

/**
 * Confirm and save entity from dialog form.
 */
export async function confirmEntity() {
    const name = $('#entity-name').value.trim();
    if (!name) return showToast('Please enter a name');

    const hexVal = $('#entity-color-hex').value;
    const avatar_color = /^#[0-9a-fA-F]{6}$/.test(hexVal) ? hexVal : '#3b82f6';
    const params = {
        name,
        entity_type: $('#entity-type').value,
        avatar_color,
        entity_id: $('#entity-edit-id').value,
    };

    if (params.entity_type === 'ai') {
        params.provider_id = $('#ai-provider').value;
        params.model = $('#ai-model-custom').value.trim() || $('#ai-model').value;
        params.temperature = parseFloat($('#ai-temperature').value);
        params.max_tokens = parseInt($('#ai-max-tokens').value);
        params.system_prompt = $('#ai-system-prompt').value;
    }

    const result = await api.saveEntity(params);
    const savedId = params.entity_id || (result && result.id);
    if (savedId && params.entity_type === 'ai') {
        await saveEntityTools(savedId);
    }
    const s = await api.getState();
    onStateUpdate(s);
    hide('#entity-dialog');
    renderProfiles();
    renderSetupTab();
}

/**
 * Load available tools and current assignments for an entity.
 * @param {number|string|undefined} entityId
 */
async function loadEntityTools(entityId) {
    const container = $('#entity-tools-list');
    try {
        _availableTools = (await api.listTools()) || [];
        const assigned = entityId ? ((await api.getEntityTools(entityId)) || []) : [];
        _entityToolAssignments = {};
        for (const a of assigned) {
            _entityToolAssignments[a.tool_name] = a.access_mode;
        }
    } catch {
        _availableTools = [];
        _entityToolAssignments = {};
    }

    if (!_availableTools.length) {
        container.innerHTML = '<span class="text-muted">No tools available</span>';
        return;
    }

    container.innerHTML = _availableTools.map(t => {
        const checked = t.name in _entityToolAssignments;
        const mode = _entityToolAssignments[t.name] || 'private';
        return `<div class="tool-assignment">
            <label class="tool-checkbox">
                <input type="checkbox" data-tool="${escHtml(t.name)}" ${checked ? 'checked' : ''}>
                <strong>${escHtml(t.name)}</strong>
                <span class="text-muted" style="font-size:0.75rem"> — ${escHtml(t.description)}</span>
            </label>
            <select class="tool-access-mode" data-tool="${escHtml(t.name)}" ${!checked ? 'disabled' : ''}>
                <option value="private" ${mode === 'private' ? 'selected' : ''}>Private</option>
                <option value="shared" ${mode === 'shared' ? 'selected' : ''}>Shared</option>
                <option value="moderator_only" ${mode === 'moderator_only' ? 'selected' : ''}>Moderator Only</option>
            </select>
        </div>`;
    }).join('');

    container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.addEventListener('change', () => {
            const sel = container.querySelector(`select[data-tool="${cb.dataset.tool}"]`);
            if (sel) sel.disabled = !cb.checked;
        });
    });
}

/**
 * Save tool assignments from the entity dialog checkboxes.
 * @param {number|string} entityId
 */
async function saveEntityTools(entityId) {
    if (!entityId || !_availableTools.length) return;
    const container = $('#entity-tools-list');
    const checkboxes = container.querySelectorAll('input[type="checkbox"]');

    for (const cb of checkboxes) {
        const toolName = cb.dataset.tool;
        const wasAssigned = toolName in _entityToolAssignments;
        const isAssigned = cb.checked;
        const modeSelect = container.querySelector(`select[data-tool="${toolName}"]`);
        const mode = modeSelect ? modeSelect.value : 'private';

        if (isAssigned && !wasAssigned) {
            await api.assignTool(entityId, toolName, mode);
        } else if (!isAssigned && wasAssigned) {
            await api.removeTool(entityId, toolName);
        } else if (isAssigned && wasAssigned && mode !== _entityToolAssignments[toolName]) {
            await api.removeTool(entityId, toolName);
            await api.assignTool(entityId, toolName, mode);
        }
    }
}

/**
 * Open edit dialog for an existing profile.
 * @param {number} id - Entity ID
 */
export async function editProfile(id) {
    const e = (state.saved_entities || []).find(x => x.id === id);
    if (e) openEntityDialog(e);
}

/**
 * Delete (deactivate) a profile.
 * @param {number} id - Entity ID
 */
export async function removeProfile(id) {
    const entity = (state.saved_entities || []).find(x => x.id === id);
    const name = entity ? entity.name : 'this profile';
    if (!confirm(`Delete "${name}"?`)) return;
    const result = await api.deleteEntity(id);
    const s = await api.getState();
    onStateUpdate(s);
    renderProfiles();
    renderInactiveProfiles();
    if (result && result.deactivated) {
        showToast(`"${name}" deactivated (used in past discussions). Reactivate from the Profiles tab.`, 5000, 'info');
    }
}

/**
 * Reactivate a previously deactivated profile.
 * @param {number} id - Entity ID
 */
export async function reactivateProfile(id) {
    await api.reactivateEntity(id);
    const s = await api.getState();
    onStateUpdate(s);
    renderProfiles();
    renderInactiveProfiles();
    renderSetupTab();
    showToast('Profile reactivated', 3000, 'info');
}

/**
 * Render the inactive profiles section below the active profiles list.
 */
export async function renderInactiveProfiles() {
    const container = $('#inactive-profiles');
    if (!container) return;
    let inactive = [];
    try {
        inactive = await api.getInactiveEntities();
    } catch (e) { /* ignore */ }
    if (!inactive || !inactive.length) {
        container.innerHTML = '';
        return;
    }
    container.innerHTML = `
        <h3 style="margin-top:1.5rem;margin-bottom:0.5rem;font-size:0.95rem;color:var(--text-secondary)">Inactive Profiles</h3>
        <p class="text-muted" style="font-size:0.8rem;margin-bottom:0.5rem">These profiles were deactivated because they participated in past discussions.</p>
        ${inactive.map(e => `
            <div class="settings-item inactive-entity">
                <div class="entity-avatar" style="background:${e.avatar_color};opacity:0.5">${getInitials(e.name)}</div>
                <div class="entity-info">
                    <div class="entity-name" style="opacity:0.6">${escHtml(e.name)}</div>
                    <div class="entity-type" style="opacity:0.6">${e.entity_type === 'ai'
                        ? 'AI - ' + escHtml(e.model || 'LLM') + (e.provider_name ? ' via ' + escHtml(e.provider_name) : '')
                        : 'Human'}</div>
                </div>
                <div class="entity-actions">
                    <button class="btn btn-outline btn-sm" data-action="reactivate-profile" data-id="${e.id}">Reactivate</button>
                </div>
            </div>
        `).join('')}
    `;
}
