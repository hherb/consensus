/* Consensus - Discussion Moderator Frontend */

// ============================================================
// API Adapters
// ============================================================

class DesktopAPI {
    async getState() { return await window.pywebview.api.get_state(); }
    // Providers
    async addProvider(n, u, ke, k) { return await window.pywebview.api.add_provider(n, u, ke || '', k || ''); }
    async updateProvider(id, n, u, ke, k) { return await window.pywebview.api.update_provider(id, n, u, ke, k || ''); }
    async deleteProvider(id) { return await window.pywebview.api.delete_provider(id); }
    async fetchModels(providerId) { return await window.pywebview.api.fetch_models(providerId); }
    // Entity profiles
    async saveEntity(p) { return await window.pywebview.api.save_entity(p.name, p.entity_type, p.avatar_color||'#3b82f6', p.provider_id||'', p.model||'', p.temperature ?? 0.7, p.max_tokens ?? 1024, p.system_prompt||'', p.entity_id||''); }
    async deleteEntity(id) { return await window.pywebview.api.delete_entity(id); }
    async reactivateEntity(id) { return await window.pywebview.api.reactivate_entity(id); }
    async getInactiveEntities() { return await window.pywebview.api.get_inactive_entities(); }
    // Prompts
    async savePrompt(p) { return await window.pywebview.api.save_prompt(p.prompt_id||'', p.name, p.role, p.target, p.task, p.content); }
    async deletePrompt(id) { return await window.pywebview.api.delete_prompt(id); }
    // Discussion setup
    async addToDiscussion(eid, isMod, alsoPart) { return await window.pywebview.api.add_to_discussion(eid, !!isMod, !!alsoPart); }
    async removeFromDiscussion(eid) { return await window.pywebview.api.remove_from_discussion(eid); }
    async setModerator(id, alsoPart) { return await window.pywebview.api.set_moderator(id, !!alsoPart); }
    async setTopic(t) { return await window.pywebview.api.set_topic(t); }
    async startDiscussion(modPart) { return await window.pywebview.api.start_discussion(!!modPart); }
    // Discussion lifecycle
    async submitMessage(eid, content) { return await window.pywebview.api.submit_human_message(eid, content); }
    async submitModeratorMessage(content) { return await window.pywebview.api.submit_moderator_message(content); }
    async generateAiTurn() { return await window.pywebview.api.generate_ai_turn(); }
    async completeTurn(summary) { return await window.pywebview.api.complete_turn(summary || ''); }
    async reassignTurn(eid) { return await window.pywebview.api.reassign_turn(eid); }
    async mediate(ctx) { return await window.pywebview.api.mediate(ctx || ''); }
    async conclude() { return await window.pywebview.api.conclude(); }
    // History
    async loadDiscussion(id) { return await window.pywebview.api.load_discussion(id); }
    async reset() { return await window.pywebview.api.reset(); }
}

class WebAPI {
    async _post(method, data = {}) {
        const resp = await fetch(`/api/${method}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const json = await resp.json();
        if (!resp.ok) {
            const errMsg = json.error || `Server error (${resp.status})`;
            showToast(errMsg);
            return { error: errMsg };
        }
        if (json.state) onStateUpdate(json.state);
        return json.result;
    }
    async getState() { return await this._post('get_state'); }
    async addProvider(n, u, ke, k) { return await this._post('add_provider', { name: n, base_url: u, api_key_env: ke || '', api_key: k || '' }); }
    async updateProvider(id, n, u, ke, k) { return await this._post('update_provider', { provider_id: id, name: n, base_url: u, api_key_env: ke, api_key: k || '' }); }
    async deleteProvider(id) { return await this._post('delete_provider', { provider_id: id }); }
    async fetchModels(providerId) { return await this._post('fetch_models', { provider_id: providerId }); }
    async saveEntity(p) { return await this._post('save_entity', p); }
    async deleteEntity(id) { return await this._post('delete_entity', { entity_id: id }); }
    async reactivateEntity(id) { return await this._post('reactivate_entity', { entity_id: id }); }
    async getInactiveEntities() { return await this._post('get_inactive_entities'); }
    async savePrompt(p) { return await this._post('save_prompt', p); }
    async deletePrompt(id) { return await this._post('delete_prompt', { prompt_id: id }); }
    async addToDiscussion(eid, isMod, alsoPart) { return await this._post('add_to_discussion', { entity_id: eid, is_moderator: !!isMod, also_participant: !!alsoPart }); }
    async removeFromDiscussion(eid) { return await this._post('remove_from_discussion', { entity_id: eid }); }
    async setModerator(id, alsoPart) { return await this._post('set_moderator', { entity_id: id, also_participant: !!alsoPart }); }
    async setTopic(t) { return await this._post('set_topic', { topic: t }); }
    async startDiscussion(modPart) { return await this._post('start_discussion', { moderator_participates: !!modPart }); }
    async submitMessage(eid, content) { return await this._post('submit_human_message', { entity_id: eid, content }); }
    async submitModeratorMessage(content) { return await this._post('submit_moderator_message', { content }); }
    async generateAiTurn() { return await this._post('generate_ai_turn'); }
    async completeTurn(summary) { return await this._post('complete_turn', { moderator_summary: summary || '' }); }
    async reassignTurn(eid) { return await this._post('reassign_turn', { entity_id: eid }); }
    async mediate(ctx) { return await this._post('mediate', { context: ctx || '' }); }
    async conclude() { return await this._post('conclude'); }
    async loadDiscussion(id) { return await this._post('load_discussion', { discussion_id: id }); }
    async reset() { return await this._post('reset'); }
}

// ============================================================
// State
// ============================================================

let api = null;
let state = {
    topic: '', entities: [], moderator_id: null, messages: [],
    storyboard: [], turn_order: [], current_turn_index: 0,
    turn_number: 0, is_active: false, current_speaker_id: null,
    providers: [], saved_entities: [], prompts: [], discussions_history: [],
};
let processing = false;
let renderedMessageCount = 0;
let renderedStoryboardCount = 0;

// ============================================================
// Helpers
// ============================================================

function renderMarkdown(text) {
    if (!text) return '';
    // Escape HTML first to prevent XSS, then apply markdown formatting
    let html = escHtml(text);
    return html
        .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/^[-*] (.+)$/gm, '<uli>$1</uli>')
        .replace(/^\d+\. (.+)$/gm, '<oli>$1</oli>')
        .replace(/((?:<uli>.*<\/uli>\n?)+)/g, (m) =>
            '<ul>' + m.replace(/<uli>/g, '<li>').replace(/<\/uli>/g, '</li>') + '</ul>')
        .replace(/((?:<oli>.*<\/oli>\n?)+)/g, (m) =>
            '<ol>' + m.replace(/<oli>/g, '<li>').replace(/<\/oli>/g, '</li>') + '</ol>')
        .replace(/^(?!<(?:h[1-6]|ul|ol|li|p|pre))(.*\S.*)$/gm, '<p>$1</p>');
}

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
function show(el) { if (typeof el === 'string') el = $(el); el?.classList.remove('hidden'); }
function hide(el) { if (typeof el === 'string') el = $(el); el?.classList.add('hidden'); }

function showToast(msg, duration = 4000, type = 'error') {
    const existing = $('.toast');
    if (existing) existing.remove();
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => { toast.classList.add('toast-fade-out'); setTimeout(() => toast.remove(), 300); }, duration);
}

function getInitials(name) {
    return (name || '?').split(/\s+/).map(w => w[0]).join('').toUpperCase().slice(0, 2);
}

function formatTime(ts) {
    return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatDate(ts) {
    return new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function getEntity(id) { return state.entities.find(e => e.id === id); }

function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

// ============================================================
// Tab Navigation
// ============================================================

function switchTab(tabName) {
    $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    $$('.tab-content').forEach(tc => tc.classList.add('hidden'));
    const target = $(`#tab-${tabName}`);
    if (target) target.classList.remove('hidden');
    // Render tab content
    if (tabName === 'settings-providers') renderProviders();
    else if (tabName === 'settings-entities') { renderProfiles(); renderInactiveProfiles(); }
    else if (tabName === 'settings-prompts') renderPrompts();
    else if (tabName === 'history') renderHistory();
    else if (tabName === 'new-discussion') renderSetupTab();
}

// ============================================================
// Providers Tab
// ============================================================

function renderProviders() {
    const list = $('#provider-list');
    const providers = state.providers || [];
    if (!providers.length) {
        list.innerHTML = '<div class="empty-state">No providers configured yet</div>';
        return;
    }
    list.innerHTML = providers.map(p => `
        <div class="settings-item">
            <div class="entity-info">
                <div class="entity-name">${escHtml(p.name)}</div>
                <div class="settings-detail">${escHtml(p.base_url)}</div>
                <div class="settings-detail">API Key: ${p.has_key ? '<span style="color:var(--color-success)">Configured</span>' : '<em>Not set</em>'}</div>
            </div>
            <div class="entity-actions">
                <button class="btn btn-ghost btn-sm" data-action="edit-provider" data-id="${p.id}">Edit</button>
                <button class="btn btn-ghost btn-sm" data-action="delete-provider" data-id="${p.id}">Delete</button>
            </div>
        </div>
    `).join('');
}

function openProviderDialog(provider) {
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

async function confirmProvider() {
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

async function editProvider(id) {
    const p = (state.providers || []).find(x => x.id === id);
    if (p) openProviderDialog(p);
}

async function removeProvider(id) {
    await api.deleteProvider(id);
    const s = await api.getState();
    onStateUpdate(s);
    renderProviders();
}

// ============================================================
// Entity Profiles Tab
// ============================================================

function renderProfiles() {
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

async function loadModelsForProvider(providerId, currentModel) {
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

function selectColorSwatch(color) {
    const hex = color || '#3b82f6';
    $('#entity-color-hex').value = hex;
    document.querySelectorAll('#color-swatches .color-swatch').forEach(s => {
        s.classList.toggle('selected', s.dataset.color === hex);
    });
}

function openEntityDialog(entity) {
    $('#entity-dialog-title').textContent = entity ? 'Edit Profile' : 'Add Profile';
    $('#entity-name').value = entity?.name || '';
    $('#entity-type').value = entity?.entity_type || 'human';
    const color = entity?.avatar_color || '#3b82f6';
    selectColorSwatch(color);
    $('#entity-edit-id').value = entity?.id || '';

    // Populate provider dropdown
    const provSelect = $('#ai-provider');
    provSelect.innerHTML = '<option value="">-- Select Provider --</option>' +
        (state.providers || []).map(p =>
            `<option value="${p.id}" ${entity?.provider_id === p.id ? 'selected' : ''}>${escHtml(p.name)}</option>`
        ).join('');

    // Reset model fields
    $('#ai-model').innerHTML = '<option value="">-- Select a provider first --</option>';
    $('#ai-model-custom').value = '';

    if ((entity?.entity_type || 'human') === 'ai') {
        show('#ai-config');
        $('#ai-temperature').value = entity?.temperature ?? 0.7;
        $('#ai-max-tokens').value = entity?.max_tokens ?? 1024;
        $('#ai-system-prompt').value = entity?.system_prompt || '';
        // Load models if provider is set
        if (entity?.provider_id) {
            loadModelsForProvider(entity.provider_id, entity.model || '');
        }
    } else {
        hide('#ai-config');
    }

    show('#entity-dialog');
    $('#entity-name').focus();
}

async function confirmEntity() {
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

    await api.saveEntity(params);
    const s = await api.getState();
    onStateUpdate(s);
    hide('#entity-dialog');
    renderProfiles();
    renderSetupTab();
}

async function editProfile(id) {
    const e = (state.saved_entities || []).find(x => x.id === id);
    if (e) openEntityDialog(e);
}

async function removeProfile(id) {
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

async function reactivateProfile(id) {
    await api.reactivateEntity(id);
    const s = await api.getState();
    onStateUpdate(s);
    renderProfiles();
    renderInactiveProfiles();
    renderSetupTab();
    showToast('Profile reactivated', 3000, 'info');
}

async function renderInactiveProfiles() {
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

// ============================================================
// Prompts Tab
// ============================================================

function renderPrompts() {
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

function openPromptDialog(prompt) {
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

async function confirmPrompt() {
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

async function editPrompt(id) {
    const p = (state.prompts || []).find(x => x.id === id);
    if (p) openPromptDialog(p);
}

async function removePrompt(id) {
    await api.deletePrompt(id);
    const s = await api.getState();
    onStateUpdate(s);
    renderPrompts();
}

// ============================================================
// History Tab
// ============================================================

function renderHistory() {
    const list = $('#history-list');
    const discussions = state.discussions_history || [];
    if (!discussions.length) {
        list.innerHTML = '<div class="empty-state">No past discussions</div>';
        return;
    }
    list.innerHTML = discussions.map(d => `
        <div class="history-item">
            <div style="flex:1;cursor:pointer" data-action="load-discussion" data-id="${d.id}">
                <div class="history-topic">${escHtml(d.topic)}</div>
                <div class="history-meta">${d.started_at ? formatDate(d.started_at) : 'Not started'}</div>
            </div>
            <span class="history-status ${d.status}">${d.status}</span>
            <div class="export-dropdown">
                <button class="history-export-btn" data-action="toggle-history-export" data-id="${d.id}" title="Export">Export &#9662;</button>
                <div id="history-export-menu-${d.id}" class="history-export-menu hidden">
                    <button data-action="export-history-json" data-id="${d.id}" class="export-option">JSON</button>
                    <button data-action="export-history-html" data-id="${d.id}" class="export-option">HTML</button>
                    <button data-action="export-history-pdf" data-id="${d.id}" class="export-option">PDF</button>
                </div>
            </div>
        </div>
    `).join('');
}

async function loadDiscussion(id) {
    const result = await api.loadDiscussion(id);
    if (result?.error) return showToast(result.error);
    onStateUpdate(result);
    hide('#setup-phase');
    show('#discussion-phase');
    renderedMessageCount = 0;
    renderedStoryboardCount = 0;
    renderDiscussion();
}

// ============================================================
// New Discussion Tab (Setup)
// ============================================================

function renderSetupTab() {
    renderAvailableEntities();
    renderDiscussionRoster();
    updateStartButton();
}

const MOST_RECENT_ENTITIES = 6;

function renderAvailableEntities() {
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

    // Hide search box when few entities
    if (searchInput) searchInput.style.display = saved.length > MOST_RECENT_ENTITIES ? '' : 'none';

    if (query) {
        // When searching, show all matches
        available = available.filter(e =>
            e.name.toLowerCase().includes(query) ||
            (e.entity_type === 'ai' && (e.model || '').toLowerCase().includes(query)) ||
            (e.provider_name || '').toLowerCase().includes(query)
        );
    } else {
        // No search: sort by most recently updated, limit to MOST_RECENT_ENTITIES
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

function renderDiscussionRoster() {
    const container = $('#discussion-roster');
    if (!state.entities.length) {
        container.innerHTML = '<div class="empty-state">No participants added yet</div>';
        return;
    }
    container.innerHTML = state.entities.map(e => `
        <div class="entity-item">
            <div class="entity-avatar" style="background:${e.avatar_color}">${getInitials(e.name)}</div>
            <div class="entity-info">
                <span class="entity-name">${escHtml(e.name)}</span>
                ${e.id === state.moderator_id ? '<span class="moderator-badge">MOD</span>' : ''}
                <div class="entity-type">${e.entity_type === 'ai' ? 'AI - ' + (e.ai_config?.model || 'LLM') : 'Human'}</div>
            </div>
            <div class="entity-actions">
                ${e.id !== state.moderator_id
                    ? `<button class="btn btn-ghost btn-sm" data-action="set-moderator" data-id="${e.id}">Set Mod</button>`
                    : ''}
                <button class="btn btn-ghost btn-sm" data-action="remove-from-discussion" data-id="${e.id}">Remove</button>
            </div>
        </div>
    `).join('');
}

function updateStartButton() {
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

async function addToDiscussion(entityId) {
    const result = await api.addToDiscussion(entityId);
    if (result?.error) return showToast(result.error);
    const s = await api.getState();
    onStateUpdate(s);
    renderSetupTab();
}

async function removeFromDiscussion(entityId) {
    await api.removeFromDiscussion(entityId);
    const s = await api.getState();
    onStateUpdate(s);
    renderSetupTab();
}

async function setModerator(entityId) {
    await api.setModerator(entityId);
    const s = await api.getState();
    onStateUpdate(s);
    renderSetupTab();
}

// ============================================================
// Discussion Phase Rendering
// ============================================================

function renderDiscussion() {
    $('#discussion-topic').textContent = state.topic;
    const speaker = getEntity(state.current_speaker_id);
    const badge = $('#turn-badge');
    if (speaker && state.is_active) {
        badge.textContent = `Turn ${state.turn_number}: ${speaker.name}`;
        badge.className = 'badge active';
    } else if (!state.is_active && state.messages.length > 0) {
        badge.textContent = 'Concluded';
        badge.className = 'badge';
    } else {
        badge.textContent = '';
    }
    renderSidebarEntities();
    renderNewMessages();
    renderNewStoryboard();
    updateInputArea();
}

function renderSidebarEntities() {
    $('#discussion-entities').innerHTML = state.entities.map(e => {
        const isSpeaking = e.id === state.current_speaker_id && state.is_active;
        const isMod = e.id === state.moderator_id;
        return `
            <div class="entity-sidebar-item ${isSpeaking ? 'speaking' : ''}">
                <div class="entity-avatar" style="background:${e.avatar_color}">${getInitials(e.name)}</div>
                <div>
                    <div class="entity-name">${escHtml(e.name)}${isMod ? ' <span class="moderator-badge">MOD</span>' : ''}</div>
                    <div class="entity-type">${e.entity_type === 'ai' ? e.ai_config?.model || 'AI' : 'Human'}</div>
                </div>
                ${isSpeaking ? '<div class="speaking-indicator"></div>' : ''}
            </div>`;
    }).join('');
}

function renderNewMessages() {
    const container = $('#messages');
    const newMessages = state.messages.slice(renderedMessageCount);
    const typing = container.querySelector('.typing-indicator');
    if (typing) typing.remove();

    for (const msg of newMessages) {
        const entity = getEntity(msg.entity_id);
        const color = entity?.avatar_color || '#666';
        const isMod = msg.role === 'moderator';
        const div = document.createElement('div');
        div.className = `message ${isMod ? 'moderator' : ''} ${msg.role === 'system' ? 'system' : ''}`;
        if (!isMod) div.style.borderLeftColor = color;

        let metaHtml = '';
        if (msg.model_used) {
            metaHtml = `<span class="text-muted" style="font-size:0.7rem;margin-left:0.5rem">${msg.model_used} | ${msg.total_tokens}tok | ${msg.latency_ms}ms</span>`;
        }

        div.innerHTML = `
            <div class="message-header">
                <div class="entity-avatar" style="background:${color};width:24px;height:24px;font-size:0.65rem">${getInitials(msg.entity_name)}</div>
                <span class="message-sender" ${isMod ? '' : `style="color:${color}"`}>${escHtml(msg.entity_name)}</span>
                ${metaHtml}
                <span class="message-time">${formatTime(msg.timestamp)}</span>
            </div>
            <div class="message-content">${renderMarkdown(msg.content)}</div>`;
        container.appendChild(div);
    }
    renderedMessageCount = state.messages.length;
    container.scrollTop = container.scrollHeight;
}

function renderNewStoryboard() {
    const container = $('#storyboard');
    const newEntries = state.storyboard.slice(renderedStoryboardCount);
    if (!state.storyboard.length && !newEntries.length) {
        if (!container.querySelector('.empty-state'))
            container.innerHTML = '<div class="empty-state">Summaries will appear here after each turn</div>';
        return;
    }
    const empty = container.querySelector('.empty-state');
    if (empty) empty.remove();

    for (const entry of newEntries) {
        const isConclusion = entry.summary.startsWith('CONCLUSION:');
        const div = document.createElement('div');
        div.className = `storyboard-entry ${isConclusion ? 'conclusion' : ''}`;
        div.innerHTML = `
            <div class="storyboard-turn">${isConclusion ? 'Conclusion' : `Turn ${entry.turn_number}`}</div>
            <div class="storyboard-speaker">${escHtml(entry.speaker_name)}</div>
            <div class="storyboard-text">${renderMarkdown(isConclusion ? entry.summary.replace('CONCLUSION: ', '') : entry.summary)}</div>`;
        container.appendChild(div);
    }
    renderedStoryboardCount = state.storyboard.length;
    container.scrollTop = container.scrollHeight;
}

function updateInputArea() {
    const input = $('#message-input');
    const sendBtn = $('#send-btn');
    const turnInfo = $('#turn-info');
    const speaker = getEntity(state.current_speaker_id);

    if (!state.is_active) {
        turnInfo.textContent = 'Discussion has concluded.';
        input.disabled = true; sendBtn.disabled = true;
        return;
    }
    if (!speaker) {
        turnInfo.textContent = 'Waiting...';
        input.disabled = true; sendBtn.disabled = true;
        return;
    }
    if (speaker.entity_type === 'ai') {
        turnInfo.textContent = `${speaker.name} (AI) is thinking...`;
        input.disabled = true; sendBtn.disabled = true;
    } else {
        turnInfo.textContent = `${speaker.name}'s turn to speak`;
        input.disabled = false; sendBtn.disabled = false;
        input.placeholder = `Type ${speaker.name}'s message...`;
        input.focus();
    }
}

function showTypingIndicator(name) {
    const container = $('#messages');
    const existing = container.querySelector('.typing-indicator');
    if (existing) existing.remove();
    const div = document.createElement('div');
    div.className = 'typing-indicator';
    div.innerHTML = `<span>${escHtml(name)} is thinking</span><div class="typing-dots"><span></span><span></span><span></span></div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// ============================================================
// Export Functions
// ============================================================

function slugify(text) {
    return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);
}

function exportFilename(ext, exportState) {
    const s = exportState || state;
    const topic = slugify(s.topic || 'discussion');
    const date = new Date().toISOString().slice(0, 10);
    return `consensus-${topic}-${date}.${ext}`;
}

function buildExportData(exportState) {
    const s = exportState || state;
    const mod = s.entities.find(e => e.id === s.moderator_id);
    return {
        exported_at: new Date().toISOString(),
        app: 'Consensus',
        discussion: {
            id: s.id,
            topic: s.topic,
            status: s.is_active ? 'active' : 'concluded',
            turn_number: s.turn_number,
        },
        participants: s.entities.map(e => {
            const p = { name: e.name, type: e.entity_type, avatar_color: e.avatar_color };
            if (e.entity_type === 'ai' && e.ai_config) p.model = e.ai_config.model;
            return p;
        }),
        moderator: mod ? mod.name : null,
        messages: s.messages.map(m => {
            const msg = {
                speaker: m.entity_name,
                role: m.role,
                content: m.content,
                timestamp: m.timestamp,
            };
            if (m.model_used) {
                msg.ai_metadata = {
                    model: m.model_used,
                    tokens: m.total_tokens,
                    prompt_tokens: m.prompt_tokens,
                    completion_tokens: m.completion_tokens,
                    latency_ms: m.latency_ms,
                };
            } else {
                msg.ai_metadata = null;
            }
            return msg;
        }),
        storyboard: s.storyboard.map(e => ({
            turn: e.turn_number,
            speaker: e.speaker_name,
            summary: e.summary,
        })),
    };
}

function downloadFile(content, filename, mimeType) {
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function exportAsJson(exportState) {
    const data = buildExportData(exportState);
    const json = JSON.stringify(data, null, 2);
    downloadFile(json, exportFilename('json', exportState), 'application/json');
    showToast('Exported as JSON', 2000, 'success');
}

function safeColor(c) {
    return /^#[0-9a-fA-F]{3,8}$/.test(c) ? c : '#666';
}

function buildExportHtml(exportState) {
    const s = exportState || state;
    const mod = s.entities.find(e => e.id === s.moderator_id);
    const statusText = s.is_active ? 'Active' : 'Concluded';

    const participantsHtml = s.entities.map(e => {
        const initials = getInitials(e.name);
        const typeLabel = e.entity_type === 'ai' ? (e.ai_config?.model || 'AI') : 'Human';
        const modBadge = e.id === s.moderator_id ? ' <span class="mod-badge">MOD</span>' : '';
        return `<div class="participant">
            <div class="avatar" style="background:${safeColor(e.avatar_color)}">${escHtml(initials)}</div>
            <div><span class="name">${escHtml(e.name)}${modBadge}</span><br><span class="type">${escHtml(typeLabel)}</span></div>
        </div>`;
    }).join('\n');

    const messagesHtml = s.messages.map(m => {
        const entity = s.entities.find(e => e.id === m.entity_id);
        const color = safeColor(entity?.avatar_color || '#666');
        const isMod = m.role === 'moderator';
        const isSystem = m.role === 'system';
        const initials = getInitials(m.entity_name);
        let metaHtml = '';
        if (m.model_used) {
            metaHtml = `<span class="meta">${escHtml(m.model_used)} | ${m.total_tokens}tok | ${m.latency_ms}ms</span>`;
        }
        const cls = isMod ? 'message moderator' : isSystem ? 'message system' : 'message';
        const borderStyle = !isMod && !isSystem ? `border-left-color:${color};` : '';
        return `<div class="${cls}" style="${borderStyle}">
            <div class="msg-header">
                <div class="avatar" style="background:${color};width:24px;height:24px;font-size:0.65rem">${escHtml(initials)}</div>
                <span class="sender" ${isMod ? '' : `style="color:${color}"`}>${escHtml(m.entity_name)}</span>
                ${metaHtml}
                <span class="time">${formatTime(m.timestamp)}</span>
            </div>
            <div class="content">${renderMarkdown(m.content)}</div>
        </div>`;
    }).join('\n');

    const storyboardHtml = s.storyboard.map(e => {
        const isConclusion = e.summary.startsWith('CONCLUSION:');
        const cls = isConclusion ? 'sb-entry conclusion' : 'sb-entry';
        const label = isConclusion ? 'Conclusion' : `Turn ${e.turn_number}`;
        const text = isConclusion ? e.summary.replace('CONCLUSION: ', '') : e.summary;
        return `<div class="${cls}">
            <div class="sb-turn">${label}</div>
            <div class="sb-speaker">${escHtml(e.speaker_name)}</div>
            <div class="sb-text">${renderMarkdown(text)}</div>
        </div>`;
    }).join('\n');

    const exportDate = new Date().toLocaleString();

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Consensus: ${escHtml(s.topic)}</title>
<style>
:root {
    --bg: #0f172a; --surface: #1e293b; --surface-elevated: #334155;
    --border: #475569; --text: #f1f5f9; --text-secondary: #94a3b8;
    --text-muted: #64748b; --primary: #3b82f6; --accent: #a855f7;
    --success: #22c55e; --moderator-bg: rgba(168,85,247,0.08);
    --moderator-border: rgba(168,85,247,0.3);
    --font: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
    --font-mono: 'SF Mono', 'Fira Code', Consolas, monospace;
    --radius: 8px; --radius-lg: 12px;
}
@media (prefers-color-scheme: light) {
    :root {
        --bg: #f1f5f9; --surface: #ffffff; --surface-elevated: #f8fafc;
        --border: #e2e8f0; --text: #0f172a; --text-secondary: #475569;
        --text-muted: #94a3b8; --moderator-bg: rgba(168,85,247,0.05);
        --moderator-border: rgba(168,85,247,0.2);
    }
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: var(--font); font-size: 14px; line-height: 1.5; color: var(--text); background: var(--bg); padding: 2rem; max-width: 900px; margin: 0 auto; }
h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 0.25rem; }
.export-header { margin-bottom: 2rem; padding-bottom: 1rem; border-bottom: 1px solid var(--border); }
.export-header .subtitle { color: var(--text-secondary); font-size: 0.85rem; }
.export-header .status { display: inline-block; font-size: 0.7rem; padding: 0.1rem 0.5rem; border-radius: 999px; color: #fff; margin-left: 0.5rem; }
.export-header .status.concluded { background: var(--success); }
.export-header .status.active { background: #f59e0b; color: #000; }
.participants { display: flex; flex-wrap: wrap; gap: 0.75rem; margin: 1rem 0; }
.participant { display: flex; align-items: center; gap: 0.5rem; padding: 0.4rem 0.6rem; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); }
.avatar { width: 32px; height: 32px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 0.75rem; font-weight: 700; color: #fff; flex-shrink: 0; }
.name { font-weight: 500; font-size: 0.85rem; }
.type { font-size: 0.7rem; color: var(--text-muted); }
.mod-badge { font-size: 0.6rem; background: var(--accent); color: #fff; padding: 0.05rem 0.35rem; border-radius: 999px; }
section { margin-bottom: 2rem; }
section > h2 { font-size: 1.1rem; font-weight: 600; margin-bottom: 1rem; padding-bottom: 0.5rem; border-bottom: 1px solid var(--border); }
.message { padding: 0.75rem 1rem; border-radius: var(--radius-lg); border-left: 3px solid transparent; background: var(--surface); margin-bottom: 0.5rem; max-width: 85%; }
.message.moderator { background: var(--moderator-bg); border-left-color: var(--moderator-border); border: 1px solid var(--moderator-border); }
.message.moderator .sender { color: var(--accent); }
.message.system { text-align: center; color: var(--text-muted); font-size: 0.8rem; background: transparent; border: none; max-width: 100%; }
.msg-header { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 0.3rem; }
.sender { font-weight: 600; font-size: 0.85rem; }
.meta { font-size: 0.7rem; color: var(--text-muted); margin-left: 0.5rem; }
.time { font-size: 0.7rem; color: var(--text-muted); margin-left: auto; }
.content { font-size: 0.9rem; line-height: 1.6; }
.content p { margin-bottom: 0.4em; } .content p:last-child { margin-bottom: 0; }
.content strong { font-weight: 600; } .content em { font-style: italic; }
.content code { background: var(--surface-elevated); padding: 0.1em 0.3em; border-radius: 3px; font-family: var(--font-mono); font-size: 0.85em; }
.content pre { background: var(--surface-elevated); padding: 0.75rem; border-radius: var(--radius); overflow-x: auto; margin: 0.5em 0; }
.content pre code { background: none; padding: 0; }
.content ul, .content ol { padding-left: 1.5em; margin: 0.4em 0; }
.content h2 { font-size: 1.1rem; margin: 0.5em 0 0.3em; } .content h3 { font-size: 1rem; margin: 0.4em 0 0.2em; }
.sb-entry { position: relative; padding: 0.6rem 0.6rem 0.6rem 1.5rem; margin-bottom: 0.75rem; }
.sb-entry::before { content: ''; position: absolute; left: 0; top: 0; bottom: -0.75rem; width: 2px; background: var(--border); }
.sb-entry:last-child::before { bottom: 0; }
.sb-entry::after { content: ''; position: absolute; left: -3px; top: 0.8rem; width: 8px; height: 8px; border-radius: 50%; background: var(--accent); }
.sb-turn { font-size: 0.7rem; font-weight: 600; color: var(--accent); text-transform: uppercase; letter-spacing: 0.05em; }
.sb-speaker { font-size: 0.75rem; color: var(--text-muted); }
.sb-text { font-size: 0.8rem; line-height: 1.5; margin-top: 0.2rem; color: var(--text-secondary); }
.sb-entry.conclusion .sb-text { color: var(--text); font-weight: 500; }
.sb-entry.conclusion::after { background: var(--success); width: 10px; height: 10px; left: -4px; }
.export-footer { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--border); font-size: 0.75rem; color: var(--text-muted); text-align: center; }
@media print {
    body { background: #fff; color: #000; padding: 1rem; }
    :root { --bg: #fff; --surface: #fff; --surface-elevated: #f5f5f5; --border: #ddd; --text: #000; --text-secondary: #555; --text-muted: #888; --moderator-bg: #f5f0ff; --moderator-border: #c4a7e7; }
    .message { max-width: 100%; }
    .avatar { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
    .mod-badge, .sb-entry::after, .sb-entry.conclusion::after { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
}
</style>
</head>
<body>
<div class="export-header">
    <h1>${escHtml(s.topic)}<span class="status ${statusText.toLowerCase()}">${statusText}</span></h1>
    <div class="subtitle">Exported from Consensus on ${exportDate}</div>
</div>

<section>
    <h2>Participants</h2>
    <div class="participants">${participantsHtml}</div>
</section>

<section>
    <h2>Discussion</h2>
    ${messagesHtml}
</section>

${s.storyboard.length ? `<section>
    <h2>Storyboard</h2>
    ${storyboardHtml}
</section>` : ''}

<div class="export-footer">Exported from Consensus &mdash; ${exportDate}</div>
</body>
</html>`;
}

function exportAsHtml(exportState) {
    const html = buildExportHtml(exportState);
    downloadFile(html, exportFilename('html', exportState), 'text/html');
    showToast('Exported as HTML', 2000, 'success');
}

function exportAsPdf(exportState) {
    const html = buildExportHtml(exportState);
    // In pywebview desktop mode, replace page with export preview + print toolbar
    if (window.pywebview) {
        document.open();
        document.write(html);
        document.close();
        // Inject a print toolbar at the top (hidden from print output)
        const toolbar = document.createElement('div');
        toolbar.id = 'pdf-toolbar';
        toolbar.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:9999;display:flex;align-items:center;gap:1rem;padding:0.75rem 1.5rem;background:#1e293b;border-bottom:2px solid #3b82f6;font-family:-apple-system,BlinkMacSystemFont,sans-serif;';
        toolbar.innerHTML = `
            <button onclick="window.print()" style="padding:0.5rem 1.25rem;background:#3b82f6;color:#fff;border:none;border-radius:6px;font-size:0.9rem;font-weight:600;cursor:pointer;">Print / Save as PDF</button>
            <button id="pdf-back-btn" style="padding:0.5rem 1.25rem;background:#475569;color:#fff;border:none;border-radius:6px;font-size:0.9rem;cursor:pointer;">Back to Discussion</button>
            <span style="color:#94a3b8;font-size:0.8rem;margin-left:auto;">Use Print dialog to save as PDF</span>
        `;
        document.body.style.paddingTop = '60px';
        document.body.insertBefore(toolbar, document.body.firstChild);
        // Hide toolbar when printing
        const style = document.createElement('style');
        style.textContent = '#pdf-toolbar { display: none !important; } body { padding-top: 0 !important; }';
        style.media = 'print';
        document.head.appendChild(style);
        // Back button reloads the app
        document.getElementById('pdf-back-btn').addEventListener('click', () => {
            location.reload();
        });
        return;
    }
    const w = window.open('', '_blank');
    if (!w) {
        showToast('Pop-up blocked — please allow pop-ups for PDF export');
        return;
    }
    w.onload = () => { w.print(); };
    w.document.write(html);
    w.document.close();
}

function toggleExportMenu() {
    const menu = $('#export-menu');
    menu.classList.toggle('hidden');
}

function closeExportMenu() {
    const menu = $('#export-menu');
    if (menu) menu.classList.add('hidden');
}

function toggleHistoryExportMenu(discussionId) {
    const menu = $(`#history-export-menu-${discussionId}`);
    if (!menu) return;
    const wasHidden = menu.classList.contains('hidden');
    closeAllHistoryMenus();
    if (wasHidden) menu.classList.remove('hidden');
}

function closeAllHistoryMenus() {
    $$('.history-export-menu').forEach(m => m.classList.add('hidden'));
}

async function exportHistoryDiscussion(discussionId, format) {
    try {
        // Fetch discussion data via get_export_data to avoid mutating server state
        let exportState;
        if (window.pywebview) {
            exportState = await window.pywebview.api.get_export_data(discussionId);
        } else {
            const resp = await fetch('/api/get_export_data', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ discussion_id: discussionId }),
            });
            const json = await resp.json();
            exportState = json.result;
        }
        if (!exportState || exportState.error) {
            showToast(exportState?.error || 'Failed to load discussion');
            return;
        }
        if (format === 'json') exportAsJson(exportState);
        else if (format === 'html') exportAsHtml(exportState);
        else if (format === 'pdf') exportAsPdf(exportState);
    } catch (e) {
        showToast('Export failed: ' + e.message);
    }
}

// ============================================================
// Discussion Event Handlers
// ============================================================

async function onStartDiscussion() {
    const topic = $('#topic-input').value.trim();
    if (!topic) return;
    await api.setTopic(topic);
    const modParticipates = $('#mod-participates').checked;
    const result = await api.startDiscussion(modParticipates);
    if (result?.error) return showToast(result.error);
    onStateUpdate(result);
    hide('#setup-phase');
    show('#discussion-phase');
    renderedMessageCount = 0;
    renderedStoryboardCount = 0;
    renderDiscussion();
    processCurrentTurn();
}

async function onSendMessage() {
    const input = $('#message-input');
    const content = input.value.trim();
    if (!content || !state.current_speaker_id) return;
    input.value = '';
    input.disabled = true;
    $('#send-btn').disabled = true;

    try {
        const result = await api.submitMessage(state.current_speaker_id, content);
        if (result?.error) return showToast(result.error);
        const s = await api.getState();
        onStateUpdate(s);
        const completed = await completeTurnFlow();
        if (completed) processCurrentTurn();
    } catch (e) {
        showToast('Failed to send: ' + e.message);
        input.disabled = false;
        $('#send-btn').disabled = false;
    }
}

async function completeTurnFlow() {
    const mod = getEntity(state.moderator_id);
    if (!mod) return true;
    if (mod.entity_type === 'ai') {
        showTypingIndicator(mod.name + ' (summarizing)');
        try {
            const result = await api.completeTurn();
            if (result?.state) onStateUpdate(result.state);
            else onStateUpdate(await api.getState());
        } catch (e) {
            showToast('Summary failed: ' + e.message);
            onStateUpdate(await api.getState());
        }
        renderDiscussion();
        return true;  // Turn fully completed, caller can continue
    } else {
        promptModeratorInput('summary');
        return false;  // Waiting for human moderator input
    }
}

function promptModeratorInput(mode) {
    const title = $('#moderator-dialog-title');
    const input = $('#moderator-input');
    title.textContent = mode === 'summary' ? 'Moderator Summary' : 'Moderator Mediation';
    input.placeholder = mode === 'summary'
        ? 'Summarize the key points from this turn...'
        : 'Enter your mediation or commentary...';
    input.value = '';
    input.dataset.mode = mode;
    show('#moderator-dialog');
    input.focus();
}

async function onConfirmModeratorInput() {
    const input = $('#moderator-input');
    const content = input.value.trim();
    if (!content) return showToast('Please enter text');
    const mode = input.dataset.mode;
    hide('#moderator-dialog');

    if (mode === 'summary') {
        const result = await api.completeTurn(content);
        if (result?.state) onStateUpdate(result.state);
        else onStateUpdate(await api.getState());
        renderDiscussion();
        processCurrentTurn();  // Check if next speaker is AI
    } else {
        await api.submitModeratorMessage(content);
        onStateUpdate(await api.getState());
        renderDiscussion();
    }
}

async function processCurrentTurn() {
    if (!state.is_active || processing) return;
    processing = true;
    try {
        // Loop to handle sequential AI speakers without recursion
        while (state.is_active) {
            const speaker = getEntity(state.current_speaker_id);
            if (!speaker || speaker.entity_type !== 'ai') {
                renderDiscussion();
                break;
            }
            showTypingIndicator(speaker.name);
            renderDiscussion();
            const result = await api.generateAiTurn();
            if (result?.error) { showToast(result.error); break; }
            onStateUpdate(await api.getState());
            renderDiscussion();
            const turnCompleted = await completeTurnFlow();
            if (!turnCompleted) break;  // Human moderator needs input
        }
    } catch (e) {
        showToast('AI turn failed: ' + e.message);
    } finally {
        processing = false;
    }
}

async function onReassign() {
    const list = $('#reassign-list');
    list.innerHTML = state.entities
        .filter(e => state.turn_order.includes(e.id))
        .map(e => `
            <div class="reassign-item" data-action="do-reassign" data-id="${e.id}">
                <div class="entity-avatar" style="background:${e.avatar_color};width:28px;height:28px;font-size:0.7rem">${getInitials(e.name)}</div>
                <span>${escHtml(e.name)}</span>
                <span class="text-muted">${e.entity_type}</span>
            </div>`).join('');
    show('#reassign-dialog');
}

async function doReassign(entityId) {
    hide('#reassign-dialog');
    const result = await api.reassignTurn(entityId);
    if (result?.error) return showToast(result.error);
    if (result?.state) onStateUpdate(result.state);
    else onStateUpdate(await api.getState());
    renderDiscussion();
    processCurrentTurn();
}

async function onMediate() {
    const mod = getEntity(state.moderator_id);
    if (!mod) return;
    if (mod.entity_type === 'ai') {
        showTypingIndicator(mod.name + ' (mediating)');
        try {
            await api.mediate();
            onStateUpdate(await api.getState());
            renderDiscussion();
        } catch (e) { showToast('Mediation failed: ' + e.message); }
    } else {
        promptModeratorInput('mediation');
    }
}

async function onConclude() {
    const mod = getEntity(state.moderator_id);
    if (mod?.entity_type === 'ai') showTypingIndicator(mod.name + ' (concluding)');
    try {
        const result = await api.conclude();
        onStateUpdate(result);
        renderDiscussion();
    } catch (e) { showToast('Conclusion failed: ' + e.message); }
}

async function onBack() {
    await api.reset();
    onStateUpdate(await api.getState());
    renderedMessageCount = 0;
    renderedStoryboardCount = 0;
    $('#messages').innerHTML = '';
    $('#storyboard').innerHTML = '';
    hide('#discussion-phase');
    show('#setup-phase');
    renderSetupTab();
}

// ============================================================
// State Updates
// ============================================================

function onStateUpdate(newState) {
    if (!newState) return;
    state = newState;
    if ($('#setup-phase') && !$('#setup-phase').classList.contains('hidden')) {
        renderSetupTab();
    }
}

// ============================================================
// Initialization
// ============================================================

function init() {
    // Tab navigation
    $$('.tab').forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });

    // Provider dialog
    $('#add-provider-btn').addEventListener('click', () => openProviderDialog(null));
    $('#confirm-provider-btn').addEventListener('click', confirmProvider);
    $('#cancel-provider-btn').addEventListener('click', () => hide('#provider-dialog'));

    // Entity profile dialog
    $('#add-profile-btn').addEventListener('click', () => openEntityDialog(null));
    $('#quick-add-btn').addEventListener('click', () => openEntityDialog(null));
    $('#entity-type').addEventListener('change', (e) => {
        if (e.target.value === 'ai') show('#ai-config');
        else hide('#ai-config');
    });
    $('#ai-provider').addEventListener('change', (e) => {
        const pid = e.target.value;
        if (pid) loadModelsForProvider(parseInt(pid), '');
        else {
            $('#ai-model').innerHTML = '<option value="">-- Select a provider first --</option>';
            $('#ai-model-custom').value = '';
        }
    });
    // Clear custom input when a model is selected from dropdown
    $('#ai-model').addEventListener('change', () => {
        if ($('#ai-model').value) $('#ai-model-custom').value = '';
    });
    // Color swatch selection
    $('#color-swatches').addEventListener('click', (e) => {
        const swatch = e.target.closest('.color-swatch');
        if (!swatch) return;
        selectColorSwatch(swatch.dataset.color);
    });
    // Hex input updates swatch selection
    $('#entity-color-hex').addEventListener('input', (e) => {
        const v = e.target.value;
        if (/^#[0-9a-fA-F]{6}$/.test(v)) selectColorSwatch(v);
    });
    $('#confirm-entity-btn').addEventListener('click', confirmEntity);
    $('#cancel-entity-btn').addEventListener('click', () => hide('#entity-dialog'));

    // Prompt dialog
    $('#add-prompt-btn').addEventListener('click', () => openPromptDialog(null));
    $('#confirm-prompt-btn').addEventListener('click', confirmPrompt);
    $('#cancel-prompt-btn').addEventListener('click', () => hide('#prompt-dialog'));

    // Discussion setup
    $('#entity-search').addEventListener('input', () => renderAvailableEntities());
    $('#topic-input').addEventListener('input', updateStartButton);
    $('#start-btn').addEventListener('click', onStartDiscussion);

    // Discussion phase
    $('#send-btn').addEventListener('click', onSendMessage);
    $('#message-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSendMessage(); }
    });
    $('#reassign-btn').addEventListener('click', onReassign);
    $('#mediate-btn').addEventListener('click', onMediate);
    $('#conclude-btn').addEventListener('click', onConclude);
    $('#export-btn').addEventListener('click', () => toggleExportMenu());
    document.addEventListener('click', (ev) => {
        if (!ev.target.closest('.export-dropdown')) {
            closeExportMenu();
            closeAllHistoryMenus();
        }
    });
    $('#back-btn').addEventListener('click', onBack);

    // Moderator dialog
    $('#confirm-moderator-btn').addEventListener('click', onConfirmModeratorInput);
    $('#cancel-moderator-btn').addEventListener('click', () => hide('#moderator-dialog'));
    $('#moderator-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onConfirmModeratorInput(); }
    });

    // Reassign dialog
    $('#cancel-reassign-btn').addEventListener('click', () => hide('#reassign-dialog'));

    // Close dialogs on overlay click
    $$('.dialog-overlay').forEach(overlay => {
        overlay.addEventListener('click', (e) => { if (e.target === overlay) hide(overlay); });
    });

    // Enter key in dialogs
    $('#entity-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') confirmEntity(); });
    $('#prov-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') confirmProvider(); });

    // Event delegation for dynamically rendered buttons
    document.addEventListener('click', (e) => {
        const target = e.target.closest('[data-action]');
        if (!target) return;
        const action = target.dataset.action;
        const id = target.dataset.id != null ? Number(target.dataset.id) : null;
        switch (action) {
            case 'edit-provider': editProvider(id); break;
            case 'delete-provider': removeProvider(id); break;
            case 'edit-profile': editProfile(id); break;
            case 'delete-profile': removeProfile(id); break;
            case 'reactivate-profile': reactivateProfile(id); break;
            case 'edit-prompt': editPrompt(id); break;
            case 'delete-prompt': removePrompt(id); break;
            case 'load-discussion': loadDiscussion(id); break;
            case 'add-to-discussion': addToDiscussion(id); break;
            case 'set-moderator': setModerator(id); break;
            case 'remove-from-discussion': removeFromDiscussion(id); break;
            case 'do-reassign': doReassign(id); break;
            case 'export-json': closeExportMenu(); exportAsJson(); break;
            case 'export-html': closeExportMenu(); exportAsHtml(); break;
            case 'export-pdf': closeExportMenu(); exportAsPdf(); break;
            case 'toggle-history-export': toggleHistoryExportMenu(id); break;
            case 'export-history-json': closeAllHistoryMenus(); exportHistoryDiscussion(id, 'json'); break;
            case 'export-history-html': closeAllHistoryMenus(); exportHistoryDiscussion(id, 'html'); break;
            case 'export-history-pdf': closeAllHistoryMenus(); exportHistoryDiscussion(id, 'pdf'); break;
        }
    });

    // Load initial state
    api.getState().then(s => {
        onStateUpdate(s);
        renderSetupTab();
    });
}

// ============================================================
// Bootstrap
// ============================================================

function bootstrap() {
    api = window.pywebview ? new DesktopAPI() : new WebAPI();
    init();
}

// Bootstrap: detect pywebview or fall back to web mode
const WEBVIEW_DETECT_TIMEOUT_MS = 100;
if (window.pywebview) { bootstrap(); }
else {
    window.addEventListener('pywebviewready', bootstrap);
    setTimeout(() => { if (!api) bootstrap(); }, WEBVIEW_DETECT_TIMEOUT_MS);
}
