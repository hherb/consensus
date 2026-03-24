/**
 * @module setup
 * New Discussion tab — entity selection, roster management, discussion start.
 */

import { $, show, hide, showToast, escHtml, getInitials } from './utils.js';
import { state, onStateUpdate } from './state.js';
import { api } from './api.js';
import { renderDiscussion } from './discussion.js';

const MOST_RECENT_ENTITIES = 6;

/** Cached list of discussion method metadata from the backend. */
let _methodsCache = null;

const STRATEGY_LABELS = {
    full: 'Full history',
    sliding_window: 'Sliding window',
    summary: 'Summary + recent',
    semantic: 'Semantic (RAG)',
    token_window: 'Token-aware (auto)',
};

/**
 * Render the full setup tab (available entities, roster, start button).
 */
export function renderSetupTab() {
    renderAvailableEntities();
    renderDiscussionRoster();
    updateStartButton();
    loadDiscussionMethods();
    syncDefaultContextControls();
    initMethodRecommendation();
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
    const configs = state.member_context_configs || {};
    const embeddingAvailable = !!state.embedding_available;
    const isCourtMethod = state.discussion_method === 'court_of_law';
    container.innerHTML = state.entities.map(e => {
        const isMod = e.id === state.moderator_id;
        const isDA = roles[String(e.id)] === 'devils_advocate';
        const courtRole = roles[String(e.id)] || 'standard';
        const cfg = configs[String(e.id)] || {};
        const strategy = cfg.strategy || state.default_context_strategy || 'sliding_window';
        const windowSize = cfg.window_size ?? state.default_context_window_size ?? 20;
        const showWindow = strategy !== 'full' && strategy !== 'token_window';

        // Court role badge
        const courtBadgeMap = {
            prosecutor: '<span class="da-badge" style="background:var(--error-color,#d32f2f)">PROS</span>',
            plaintiff: '<span class="da-badge" style="background:var(--warning-color,#f57c00)">PLNT</span>',
            defense: '<span class="da-badge" style="background:var(--info-color,#1976d2)">DEF</span>',
        };
        const courtBadge = isCourtMethod && !isMod ? (courtBadgeMap[courtRole] || '') : '';

        // Court role selector (replaces DA button when court_of_law is active)
        const courtRoleSelect = isCourtMethod && !isMod ? `
            <select class="court-role-select" data-entity-id="${e.id}"
                    style="font-size:0.75rem;padding:0.15rem 0.25rem;" title="Court role">
                <option value="standard"${courtRole === 'standard' ? ' selected' : ''}>No role</option>
                <option value="prosecutor"${courtRole === 'prosecutor' ? ' selected' : ''}>Prosecutor</option>
                <option value="plaintiff"${courtRole === 'plaintiff' ? ' selected' : ''}>Plaintiff</option>
                <option value="defense"${courtRole === 'defense' ? ' selected' : ''}>Defense</option>
            </select>` : '';

        return `
        <div class="entity-item">
            <div class="entity-avatar" style="background:${e.avatar_color}">${getInitials(e.name)}</div>
            <div class="entity-info">
                <span class="entity-name">${escHtml(e.name)}</span>
                ${isMod ? '<span class="moderator-badge">MOD</span>' : ''}
                ${!isCourtMethod && isDA ? '<span class="da-badge">DA</span>' : ''}
                ${courtBadge}
                <div class="entity-type">${e.entity_type === 'ai' ? 'AI - ' + (e.ai_config?.model || 'LLM') : 'Human'}</div>
            </div>
            <div class="entity-actions" style="display:flex;align-items:center;gap:0.25rem;flex-wrap:wrap;">
                <select class="context-strategy-select" data-entity-id="${e.id}"
                        style="font-size:0.75rem;padding:0.15rem 0.25rem;" title="Context loading strategy">
                    ${Object.entries(STRATEGY_LABELS).map(([val, label]) => {
                        const disabled = val === 'semantic' && !embeddingAvailable;
                        return `<option value="${val}"${val === strategy ? ' selected' : ''}${disabled ? ' disabled' : ''}>${label}${disabled ? ' (no embeddings)' : ''}</option>`;
                    }).join('')}
                </select>
                ${showWindow ? `<input type="number" class="context-window-input" data-entity-id="${e.id}"
                    value="${windowSize}" min="3" max="200"
                    style="width:3rem;font-size:0.75rem;padding:0.15rem 0.25rem;" title="Context window size">` : ''}
                ${!isMod ? `<button class="btn btn-ghost btn-sm" data-action="set-moderator" data-id="${e.id}">Set Mod</button>` : ''}
                ${courtRoleSelect}
                ${!isCourtMethod && !isMod && e.entity_type === 'ai'
                    ? `<button class="btn btn-ghost btn-sm" data-action="set-devils-advocate" data-id="${e.id}">${isDA ? 'Unset DA' : 'Set DA'}</button>`
                    : ''}
                <button class="btn btn-ghost btn-sm" data-action="remove-from-discussion" data-id="${e.id}">Remove</button>
            </div>
        </div>`;
    }).join('');

    // Attach change listeners for per-entity context strategy
    container.querySelectorAll('.context-strategy-select').forEach(sel => {
        sel.addEventListener('change', () => onMemberContextChange(Number(sel.dataset.entityId)));
    });
    container.querySelectorAll('.context-window-input').forEach(inp => {
        inp.addEventListener('change', () => onMemberContextChange(Number(inp.dataset.entityId)));
    });
    // Court role dropdowns
    container.querySelectorAll('.court-role-select').forEach(sel => {
        sel.addEventListener('change', () => setCourtRole(Number(sel.dataset.entityId), sel.value));
    });
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

/**
 * Set a court role for an entity (prosecutor, plaintiff, defense, standard).
 * @param {number} entityId
 * @param {string} role
 */
async function setCourtRole(entityId, role) {
    const result = await api.setParticipantRole(entityId, role);
    if (result?.error) return showToast(result.error);
    const s = await api.getState();
    onStateUpdate(s);
    renderSetupTab();
}

/**
 * Fetch available discussion methods and populate the selector dropdown.
 */
export async function loadDiscussionMethods() {
    const select = $('#discussion-method');
    if (!select) return;
    if (!_methodsCache) {
        _methodsCache = await api.listDiscussionMethods() || [];
    }
    select.innerHTML = _methodsCache.map(m =>
        `<option value="${m.name}" title="${escHtml(m.description)}">${escHtml(m.display_name)}</option>`
    ).join('');
    select.value = state.discussion_method || 'open_discussion';
    updateMethodDescription();
}

/**
 * Handle discussion method selection change.
 */
export async function onMethodChange() {
    const select = $('#discussion-method');
    if (!select) return;
    updateMethodDescription();
    const result = await api.setDiscussionMethod(select.value);
    if (result?.error) showToast(result.error);
    // Re-render roster — method change may show/hide role controls
    const s = await api.getState();
    onStateUpdate(s);
    renderDiscussionRoster();
}

// --- Method Recommendation ---

const ANSWER_TYPE_MAP = {
    explore: "Explore a topic from multiple perspectives",
    decide: "Make a decision between options",
    forecast: "Forecast or estimate something",
    risks: "Identify risks or failure modes",
    hypothesis: "Test a hypothesis or claim",
    disagreement: "Resolve a disagreement",
    other: "Something else / not sure",
};

export function initMethodRecommendation() {
    const btn = $('#suggest-method-btn');
    const radios = document.querySelectorAll('input[name="answer_type"]');
    if (!btn) return;

    radios.forEach(r => r.addEventListener('change', () => {
        const topic = $('#topic')?.value?.trim();
        btn.disabled = !topic;
    }));

    btn.addEventListener('click', requestRecommendation);
}

async function requestRecommendation() {
    const topic = $('#topic')?.value?.trim();
    const selected = document.querySelector('input[name="answer_type"]:checked');
    if (!topic || !selected) return;

    const btn = $('#suggest-method-btn');
    const panel = $('#method-recommendations');
    btn.disabled = true;
    btn.textContent = 'Analyzing...';

    const answerType = ANSWER_TYPE_MAP[selected.value] || selected.value;

    try {
        const result = await api.recommendMethod(topic, answerType);
        if (result?.error) {
            panel.innerHTML = `<p class="error">${escHtml(result.error)}</p>`;
        } else if (result?.recommendations) {
            renderRecommendations(result.recommendations);
        }
    } catch (e) {
        panel.innerHTML = '<p class="error">Recommendation failed.</p>';
    } finally {
        btn.disabled = false;
        btn.textContent = 'Suggest Method';
        panel.style.display = '';
    }
}

function renderRecommendations(recs) {
    const panel = $('#method-recommendations');
    if (!panel || !recs.length) return;

    panel.innerHTML = recs.map((r, i) => `
        <div class="recommendation-item ${i === 0 ? 'top-pick' : ''}"
             data-method="${escHtml(r.method_name)}">
            <strong>${escHtml(r.display_name)}</strong>
            <span class="confidence">${Math.round(r.confidence * 100)}% match</span>
            <div class="reasoning">${escHtml(r.reasoning)}</div>
        </div>
    `).join('');

    panel.querySelectorAll('.recommendation-item').forEach(el => {
        el.addEventListener('click', () => {
            const select = $('#discussion-method');
            if (select) {
                select.value = el.dataset.method;
                select.dispatchEvent(new Event('change'));
            }
        });
    });
}

function updateMethodDescription() {
    const select = $('#discussion-method');
    const desc = $('#method-description');
    if (!select || !desc || !_methodsCache) return;
    const method = _methodsCache.find(m => m.name === select.value);
    desc.textContent = method?.description || '';
}

// --- Context Strategy Controls ---

/**
 * Sync the discussion-level default context strategy controls with state.
 */
function syncDefaultContextControls() {
    const strategySelect = $('#default-context-strategy');
    const windowInput = $('#default-context-window');
    const windowLabel = $('#default-window-size-label');
    if (!strategySelect) return;

    strategySelect.value = state.default_context_strategy || 'sliding_window';
    if (windowInput) windowInput.value = state.default_context_window_size ?? 20;

    // Hide window input when strategy doesn't use a fixed window
    if (windowLabel) windowLabel.style.display =
        (strategySelect.value === 'full' || strategySelect.value === 'token_window') ? 'none' : '';

    // Disable semantic if embedding not available
    const semanticOpt = strategySelect.querySelector('option[value="semantic"]');
    if (semanticOpt) {
        const unavailable = !state.embedding_available;
        semanticOpt.disabled = unavailable;
        semanticOpt.textContent = unavailable ? 'Semantic (RAG) — no embeddings' : 'Semantic (RAG)';
    }
}

/**
 * Handle change of the discussion-level default context strategy or window size.
 */
export async function onDefaultContextChange() {
    const strategySelect = $('#default-context-strategy');
    const windowInput = $('#default-context-window');
    const windowLabel = $('#default-window-size-label');
    if (!strategySelect) return;

    const strategy = strategySelect.value;
    const windowSize = windowInput ? parseInt(windowInput.value, 10) || 20 : 20;

    // Show/hide window input
    if (windowLabel) windowLabel.style.display =
        (strategy === 'full' || strategy === 'token_window') ? 'none' : '';

    const result = await api.setDefaultContextStrategy(strategy, windowSize);
    if (result?.error) showToast(result.error);
}

/**
 * Handle per-entity context strategy or window size change.
 */
async function onMemberContextChange(entityId) {
    const sel = $(`.context-strategy-select[data-entity-id="${entityId}"]`);
    const inp = $(`.context-window-input[data-entity-id="${entityId}"]`);
    if (!sel) return;

    const strategy = sel.value;
    const windowSize = inp ? parseInt(inp.value, 10) || 20 : 20;

    const result = await api.setMemberContextStrategy(entityId, strategy, windowSize);
    if (result?.error) showToast(result.error);
    else renderDiscussionRoster(); // re-render to show/hide window input
}
