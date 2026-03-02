/* Consensus - Discussion Moderator Frontend */

// ============================================================
// API Adapters
// ============================================================

class DesktopAPI {
    async getState() { return await window.pywebview.api.get_state(); }
    // Providers
    async addProvider(n, u, k) { return await window.pywebview.api.add_provider(n, u, k || ''); }
    async updateProvider(id, n, u, k) { return await window.pywebview.api.update_provider(id, n, u, k); }
    async deleteProvider(id) { return await window.pywebview.api.delete_provider(id); }
    async fetchModels(providerId) { return await window.pywebview.api.fetch_models(providerId); }
    // Entity profiles
    async saveEntity(p) { return await window.pywebview.api.save_entity(p.name, p.entity_type, p.avatar_color||'#3b82f6', p.provider_id||'', p.model||'', p.temperature ?? 0.7, p.max_tokens ?? 1024, p.system_prompt||'', p.entity_id||''); }
    async deleteEntity(id) { return await window.pywebview.api.delete_entity(id); }
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
    async addProvider(n, u, k) { return await this._post('add_provider', { name: n, base_url: u, api_key_env: k || '' }); }
    async updateProvider(id, n, u, k) { return await this._post('update_provider', { provider_id: id, name: n, base_url: u, api_key_env: k }); }
    async deleteProvider(id) { return await this._post('delete_provider', { provider_id: id }); }
    async fetchModels(providerId) { return await this._post('fetch_models', { provider_id: providerId }); }
    async saveEntity(p) { return await this._post('save_entity', p); }
    async deleteEntity(id) { return await this._post('delete_entity', { entity_id: id }); }
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
    else if (tabName === 'settings-entities') renderProfiles();
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
                <div class="settings-detail">Key env: ${p.api_key_env ? escHtml(p.api_key_env) : '<em>none</em>'}</div>
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
    $('#prov-edit-id').value = provider?.id || '';
    show('#provider-dialog');
    $('#prov-name').focus();
}

async function confirmProvider() {
    const name = $('#prov-name').value.trim();
    const url = $('#prov-url').value.trim();
    if (!name || !url) return showToast('Name and URL are required');
    const keyEnv = $('#prov-key-env').value.trim();
    const editId = $('#prov-edit-id').value;

    if (editId) {
        await api.updateProvider(editId, name, url, keyEnv);
    } else {
        await api.addProvider(name, url, keyEnv);
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

function openEntityDialog(entity) {
    $('#entity-dialog-title').textContent = entity ? 'Edit Profile' : 'Add Profile';
    $('#entity-name').value = entity?.name || '';
    $('#entity-type').value = entity?.entity_type || 'human';
    $('#entity-color').value = entity?.avatar_color || '#3b82f6';
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

    const params = {
        name,
        entity_type: $('#entity-type').value,
        avatar_color: $('#entity-color').value,
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
    await api.deleteEntity(id);
    const s = await api.getState();
    onStateUpdate(s);
    renderProfiles();
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
        <div class="history-item" data-action="load-discussion" data-id="${d.id}">
            <div>
                <div class="history-topic">${escHtml(d.topic)}</div>
                <div class="history-meta">${d.started_at ? formatDate(d.started_at) : 'Not started'}</div>
            </div>
            <span class="history-status ${d.status}">${d.status}</span>
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

function renderAvailableEntities() {
    const container = $('#available-entities');
    const saved = state.saved_entities || [];
    const inDiscussion = new Set(state.entities.map(e => e.id));

    if (!saved.length) {
        container.innerHTML = '<div class="empty-state">No profiles yet. Create one in the Profiles tab or use the button below.</div>';
        return;
    }

    container.innerHTML = saved
        .filter(e => !inDiscussion.has(e.id))
        .map(e => `
            <div class="settings-item">
                <div class="entity-avatar" style="background:${e.avatar_color};width:28px;height:28px;font-size:0.65rem">${getInitials(e.name)}</div>
                <div class="entity-info">
                    <span class="entity-name">${escHtml(e.name)}</span>
                    <span class="entity-type">${e.entity_type === 'ai' ? 'AI' : 'Human'}</span>
                </div>
                <button class="btn btn-outline btn-sm" data-action="add-to-discussion" data-id="${e.id}">Add</button>
            </div>
        `).join('') || '<div class="text-muted" style="padding:0.5rem 0;font-size:0.85rem">All profiles added to discussion</div>';
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
    $('#confirm-entity-btn').addEventListener('click', confirmEntity);
    $('#cancel-entity-btn').addEventListener('click', () => hide('#entity-dialog'));

    // Prompt dialog
    $('#add-prompt-btn').addEventListener('click', () => openPromptDialog(null));
    $('#confirm-prompt-btn').addEventListener('click', confirmPrompt);
    $('#cancel-prompt-btn').addEventListener('click', () => hide('#prompt-dialog'));

    // Discussion setup
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
            case 'edit-prompt': editPrompt(id); break;
            case 'delete-prompt': removePrompt(id); break;
            case 'load-discussion': loadDiscussion(id); break;
            case 'add-to-discussion': addToDiscussion(id); break;
            case 'set-moderator': setModerator(id); break;
            case 'remove-from-discussion': removeFromDiscussion(id); break;
            case 'do-reassign': doReassign(id); break;
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
