/**
 * @module setup
 * New Discussion tab — entity selection, roster management, discussion start.
 */

import { $, show, hide, showToast, escHtml, getInitials } from './utils.js';
import { state, onStateUpdate } from './state.js';
import { api } from './api.js';
import { renderDiscussion } from './discussion.js';

const MOST_RECENT_ENTITIES = 6;

/**
 * Render the full setup tab (available entities, roster, start button).
 */
export function renderSetupTab() {
    renderAvailableEntities();
    renderDiscussionRoster();
    updateStartButton();
}

/**
 * Render the list of available entity profiles to add to the discussion.
 */
export function renderAvailableEntities() {
    const container = $('#available-entities');
    const searchInput = $('#entity-search');
    const saved = state.saved_entities || [];
    const inDiscussion = new Set(state.entities.map(e => e.id));

    const query = (searchInput?.value || '').trim().toLowerCase();

    if (!saved.length) {
        container.innerHTML = '<div class="empty-state">No profiles yet. Create one in the Profiles tab or use the button below.</div>';
        if (searchInput) searchInput.style.display = 'none';
        return;
    }

    let available = saved.filter(e => !inDiscussion.has(e.id));

    if (searchInput) searchInput.style.display = saved.length > MOST_RECENT_ENTITIES ? '' : 'none';

    if (query) {
        available = available.filter(e =>
            e.name.toLowerCase().includes(query) ||
            (e.entity_type === 'ai' && (e.model || '').toLowerCase().includes(query)) ||
            (e.provider_name || '').toLowerCase().includes(query)
        );
    } else {
        available = available
            .slice()
            .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0))
            .slice(0, MOST_RECENT_ENTITIES);
    }

    const totalAvailable = saved.filter(e => !inDiscussion.has(e.id)).length;
    const hiddenCount = !query ? totalAvailable - available.length : 0;

    container.innerHTML = available.map(e => `
            <div class="settings-item">
                <div class="entity-avatar" style="background:${e.avatar_color};width:28px;height:28px;font-size:0.65rem">${getInitials(e.name)}</div>
                <div class="entity-info">
                    <span class="entity-name">${escHtml(e.name)}</span>
                    <span class="entity-type">${e.entity_type === 'ai' ? 'AI' : 'Human'}</span>
                </div>
                <button class="btn btn-outline btn-sm" data-action="add-to-discussion" data-id="${e.id}">Add</button>
            </div>
        `).join('')
        + (hiddenCount > 0 ? `<div class="text-muted" style="padding:0.5rem 0;font-size:0.8rem">Showing ${available.length} most recent — use search to find ${hiddenCount} more</div>` : '')
        + (query && !available.length ? '<div class="text-muted" style="padding:0.5rem 0;font-size:0.85rem">No matching profiles</div>' : '')
        || '<div class="text-muted" style="padding:0.5rem 0;font-size:0.85rem">All profiles added to discussion</div>';
}

/**
 * Render the current discussion roster (participants with role badges).
 */
export function renderDiscussionRoster() {
    const container = $('#discussion-roster');
    if (!state.entities.length) {
        container.innerHTML = '<div class="empty-state">No participants added yet</div>';
        return;
    }
    const roles = state.member_roles || {};
    container.innerHTML = state.entities.map(e => {
        const isMod = e.id === state.moderator_id;
        const isDA = roles[String(e.id)] === 'devils_advocate';
        return `
        <div class="entity-item">
            <div class="entity-avatar" style="background:${e.avatar_color}">${getInitials(e.name)}</div>
            <div class="entity-info">
                <span class="entity-name">${escHtml(e.name)}</span>
                ${isMod ? '<span class="moderator-badge">MOD</span>' : ''}
                ${isDA ? '<span class="da-badge">DA</span>' : ''}
                <div class="entity-type">${e.entity_type === 'ai' ? 'AI - ' + (e.ai_config?.model || 'LLM') : 'Human'}</div>
            </div>
            <div class="entity-actions">
                ${!isMod ? `<button class="btn btn-ghost btn-sm" data-action="set-moderator" data-id="${e.id}">Set Mod</button>` : ''}
                ${!isMod && e.entity_type === 'ai'
                    ? `<button class="btn btn-ghost btn-sm" data-action="set-devils-advocate" data-id="${e.id}">${isDA ? 'Unset DA' : 'Set DA'}</button>`
                    : ''}
                <button class="btn btn-ghost btn-sm" data-action="remove-from-discussion" data-id="${e.id}">Remove</button>
            </div>
        </div>`;
    }).join('');
}

/**
 * Update the Start Discussion button state based on validation.
 */
export function updateStartButton() {
    const btn = $('#start-btn');
    const topic = $('#topic-input').value.trim();
    const hasEntities = state.entities.length >= 2;
    const hasMod = !!state.moderator_id;
    btn.disabled = !(topic && hasEntities && hasMod);
    if (!hasEntities) btn.textContent = 'Need at least 2 participants';
    else if (!hasMod) btn.textContent = 'Designate a moderator';
    else if (!topic) btn.textContent = 'Enter a topic';
    else btn.textContent = 'Start Discussion';
}

/**
 * Add an entity to the current discussion.
 * @param {number} entityId
 */
export async function addToDiscussion(entityId) {
    const result = await api.addToDiscussion(entityId);
    if (result?.error) return showToast(result.error);
    const s = await api.getState();
    onStateUpdate(s);
    if (!$('#discussion-phase').classList.contains('hidden')) {
        renderDiscussion();
    } else {
        renderSetupTab();
    }
}

/**
 * Remove an entity from the current discussion.
 * @param {number} entityId
 */
export async function removeFromDiscussion(entityId) {
    const result = await api.removeFromDiscussion(entityId);
    if (result?.error) return showToast(result.error);
    const s = await api.getState();
    onStateUpdate(s);
    if (!$('#discussion-phase').classList.contains('hidden')) {
        renderDiscussion();
    } else {
        renderSetupTab();
    }
}

/**
 * Set an entity as the discussion moderator.
 * @param {number} entityId
 */
export async function setModerator(entityId) {
    await api.setModerator(entityId);
    const s = await api.getState();
    onStateUpdate(s);
    renderSetupTab();
}

/**
 * Toggle devil's advocate role for an entity.
 * @param {number} entityId
 */
export async function setDevilsAdvocate(entityId) {
    const currentRole = (state.member_roles || {})[String(entityId)];
    const newRole = currentRole === 'devils_advocate' ? 'standard' : 'devils_advocate';
    const result = await api.setParticipantRole(entityId, newRole);
    if (result?.error) return showToast(result.error);
    const s = await api.getState();
    onStateUpdate(s);
    renderSetupTab();
}
