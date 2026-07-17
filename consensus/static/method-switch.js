/**
 * @module method-switch
 * Recovery dialog for a Triage method handoff blocked by the
 * tool-capability gate (spec 2026-07-17): the user assigns a
 * tool-capable model to each offending participant and retries the
 * switch, or concludes the discussion. The backend keeps the
 * discussion paused while this dialog waits.
 */

import { $, show, hide, showToast, escHtml, TOAST_WARNING_DURATION_MS } from './utils.js';
import { state, onStateUpdate } from './state.js';
import { api } from './api.js';
import { renderDiscussion } from './discussion.js';

// Injected by initMethodSwitchDialog — avoids a circular import with
// discussion-actions.js (mirrors initApi's callback pattern).
let deps = { onConclude: null, processCurrentTurn: null };

// The blocked entities currently shown, as sent by the backend.
let currentBlocked = [];

/**
 * Wire the dialog's static buttons. Call once at app init.
 * @param {object} injected - {onConclude, processCurrentTurn}
 */
export function initMethodSwitchDialog(injected) {
    deps = injected;
    $('#switch-blocked-retry-btn').addEventListener('click', onRetrySwitch);
    $('#switch-blocked-conclude-btn').addEventListener('click', async () => {
        hide('#switch-blocked-dialog');
        await deps.onConclude();
    });
}

/**
 * Populate one participant row's model dropdown from its provider.
 * Mirrors profiles.js loadModelsForProvider, with per-row elements.
 * @param {object} row - saved_entities row for the blocked participant
 * @param {HTMLSelectElement} select
 * @param {HTMLInputElement} custom
 */
async function loadModels(row, select, custom) {
    select.innerHTML = '<option value="">Loading models...</option>';
    let models = [];
    try {
        models = await api.fetchModels(row.provider_id);
    } catch (e) { /* provider offline — the custom input still works */ }
    if (models && models.length > 0) {
        select.innerHTML =
            '<option value="">-- Select Model --</option>' +
            models.map(m =>
                `<option value="${escHtml(m)}" ${m === row.model ? 'selected' : ''}>${escHtml(m)}</option>`
            ).join('');
    } else {
        select.innerHTML = '<option value="">No models found</option>';
        custom.value = row.model || '';
    }
}

/**
 * Show (or refresh) the recovery dialog.
 * @param {object} data - {target_method, switch_error, blocked_entities}
 */
export async function showSwitchBlockedDialog(data) {
    currentBlocked = data.blocked_entities || [];
    $('#switch-blocked-error').textContent = data.switch_error || '';
    const list = $('#switch-blocked-list');
    list.innerHTML = currentBlocked.map(b => `
        <div class="form-group" data-entity-id="${b.entity_id}">
            <label for="switch-model-select-${b.entity_id}">
                ${escHtml(b.name)} — current model: ${escHtml(b.model)}
            </label>
            <select id="switch-model-select-${b.entity_id}"></select>
            <input id="switch-model-custom-${b.entity_id}" type="text"
                   placeholder="Or type a model name"
                   style="margin-top:0.25rem">
        </div>
    `).join('');
    show('#switch-blocked-dialog');
    for (const b of currentBlocked) {
        const row = (state.saved_entities || []).find(e => e.id === b.entity_id);
        if (!row) continue;
        await loadModels(
            row,
            $(`#switch-model-select-${b.entity_id}`),
            $(`#switch-model-custom-${b.entity_id}`),
        );
    }
}

/**
 * Save changed models to the entity profiles, then retry the switch.
 */
async function onRetrySwitch() {
    const retryBtn = $('#switch-blocked-retry-btn');
    retryBtn.disabled = true;
    try {
        for (const b of currentBlocked) {
            const row = (state.saved_entities || []).find(e => e.id === b.entity_id);
            if (!row) continue;
            const custom = $(`#switch-model-custom-${b.entity_id}`);
            const select = $(`#switch-model-select-${b.entity_id}`);
            const newModel = (custom.value.trim() || select.value);
            if (!newModel || newModel === row.model) continue;
            await api.saveEntity({
                name: row.name,
                entity_type: row.entity_type,
                avatar_color: row.avatar_color,
                provider_id: row.provider_id,
                model: newModel,
                temperature: row.temperature,
                max_tokens: row.max_tokens,
                system_prompt: row.system_prompt || '',
                entity_id: row.id,
            });
        }
        const result = await api.retryMethodSwitch();
        if (result?.method_switched) {
            hide('#switch-blocked-dialog');
            if (result.state) onStateUpdate(result.state);
            else onStateUpdate(await api.getState());
            showToast('Method switched to '
                + (result.new_method?.display_name || 'the chosen method'));
            renderDiscussion();
            deps.processCurrentTurn();
        } else if (result?.method_switch_blocked) {
            if (result.state) onStateUpdate(result.state);
            showToast('Switch still blocked: ' + result.switch_error,
                TOAST_WARNING_DURATION_MS, 'warning');
            await showSwitchBlockedDialog(result);
        } else if (result?.error) {
            showToast(result.error);
        }
    } catch (e) {
        showToast('Retry failed: ' + e.message);
    } finally {
        retryBtn.disabled = false;
    }
}
