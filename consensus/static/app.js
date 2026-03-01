/* Consensus - Discussion Moderator Frontend */

// ============================================================
// API Adapters
// ============================================================

class DesktopAPI {
    async getState() { return await window.pywebview.api.get_state(); }
    async addEntity(p) { return await window.pywebview.api.add_entity(p); }
    async removeEntity(id) { return await window.pywebview.api.remove_entity(id); }
    async setModerator(id) { return await window.pywebview.api.set_moderator(id); }
    async setTopic(t) { return await window.pywebview.api.set_topic(t); }
    async startDiscussion() { return await window.pywebview.api.start_discussion(); }
    async submitMessage(eid, content) { return await window.pywebview.api.submit_human_message(eid, content); }
    async submitModeratorMessage(content) { return await window.pywebview.api.submit_moderator_message(content); }
    async generateAiTurn() { return await window.pywebview.api.generate_ai_turn(); }
    async completeTurn(summary) { return await window.pywebview.api.complete_turn(summary || ''); }
    async reassignTurn(eid) { return await window.pywebview.api.reassign_turn(eid); }
    async mediate(ctx) { return await window.pywebview.api.mediate(ctx || ''); }
    async conclude() { return await window.pywebview.api.conclude(); }
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
        if (json.state) onStateUpdate(json.state);
        return json.result;
    }
    async getState() { return await this._post('get_state'); }
    async addEntity(p) { return await this._post('add_entity', p); }
    async removeEntity(id) { return await this._post('remove_entity', { entity_id: id }); }
    async setModerator(id) { return await this._post('set_moderator', { entity_id: id }); }
    async setTopic(t) { return await this._post('set_topic', { topic: t }); }
    async startDiscussion() { return await this._post('start_discussion'); }
    async submitMessage(eid, content) { return await this._post('submit_human_message', { entity_id: eid, content }); }
    async submitModeratorMessage(content) { return await this._post('submit_moderator_message', { content }); }
    async generateAiTurn() { return await this._post('generate_ai_turn'); }
    async completeTurn(summary) { return await this._post('complete_turn', { moderator_summary: summary || '' }); }
    async reassignTurn(eid) { return await this._post('reassign_turn', { entity_id: eid }); }
    async mediate(ctx) { return await this._post('mediate', { context: ctx || '' }); }
    async conclude() { return await this._post('conclude'); }
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
};
let processing = false;
let renderedMessageCount = 0;
let renderedStoryboardCount = 0;

// ============================================================
// Simple Markdown Renderer
// ============================================================

function renderMarkdown(text) {
    if (!text) return '';
    let html = text
        // Code blocks
        .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
        // Headers
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        // Bold & italic
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        // Inline code
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        // Unordered lists
        .replace(/^[-*] (.+)$/gm, '<li>$1</li>')
        // Ordered lists
        .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
        // Wrap consecutive <li> in <ul>
        .replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>')
        // Paragraphs (lines not already wrapped)
        .replace(/^(?!<[huplo])(.*\S.*)$/gm, '<p>$1</p>');
    return html;
}

// ============================================================
// DOM Helpers
// ============================================================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function show(el) { if (typeof el === 'string') el = $(el); el?.classList.remove('hidden'); }
function hide(el) { if (typeof el === 'string') el = $(el); el?.classList.add('hidden'); }

function showToast(msg, duration = 4000) {
    const existing = $('.toast');
    if (existing) existing.remove();
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), duration);
}

function getInitials(name) {
    return name.split(/\s+/).map(w => w[0]).join('').toUpperCase().slice(0, 2);
}

function formatTime(ts) {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function getEntity(id) {
    return state.entities.find(e => e.id === id);
}

// ============================================================
// Setup Phase Rendering
// ============================================================

function renderSetupEntities() {
    const list = $('#entity-list');
    if (!state.entities.length) {
        list.innerHTML = '<div class="empty-state">No participants added yet</div>';
    } else {
        list.innerHTML = state.entities.map(e => `
            <div class="entity-item">
                <div class="entity-avatar" style="background:${e.avatar_color}">${getInitials(e.name)}</div>
                <div class="entity-info">
                    <span class="entity-name">${e.name}</span>
                    ${e.id === state.moderator_id ? '<span class="moderator-badge">MOD</span>' : ''}
                    <div class="entity-type">${e.entity_type === 'ai' ? 'AI - ' + (e.ai_config?.model || 'LLM') : 'Human'}</div>
                </div>
                <div class="entity-actions">
                    ${e.id !== state.moderator_id
                        ? `<button class="btn btn-ghost btn-sm" onclick="onSetModerator('${e.id}')">Set Mod</button>`
                        : ''}
                    <button class="btn btn-ghost btn-sm" onclick="onRemoveEntity('${e.id}')">Remove</button>
                </div>
            </div>
        `).join('');
    }
    updateStartButton();
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

// ============================================================
// Discussion Phase Rendering
// ============================================================

function renderDiscussion() {
    $('#discussion-topic').textContent = state.topic;

    // Turn badge
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
    const container = $('#discussion-entities');
    container.innerHTML = state.entities.map(e => {
        const isSpeaking = e.id === state.current_speaker_id && state.is_active;
        const isMod = e.id === state.moderator_id;
        return `
            <div class="entity-sidebar-item ${isSpeaking ? 'speaking' : ''}">
                <div class="entity-avatar" style="background:${e.avatar_color}">${getInitials(e.name)}</div>
                <div>
                    <div class="entity-name">
                        ${e.name}${isMod ? ' <span class="moderator-badge">MOD</span>' : ''}
                    </div>
                    <div class="entity-type">${e.entity_type === 'ai' ? e.ai_config?.model || 'AI' : 'Human'}</div>
                </div>
                ${isSpeaking ? '<div class="speaking-indicator"></div>' : ''}
            </div>
        `;
    }).join('');
}

function renderNewMessages() {
    const container = $('#messages');
    const newMessages = state.messages.slice(renderedMessageCount);

    // Remove typing indicator if present
    const typing = container.querySelector('.typing-indicator');
    if (typing) typing.remove();

    for (const msg of newMessages) {
        const entity = getEntity(msg.entity_id);
        const color = entity?.avatar_color || '#666';
        const isMod = msg.role === 'moderator';

        const div = document.createElement('div');
        div.className = `message ${isMod ? 'moderator' : ''} ${msg.role === 'system' ? 'system' : ''}`;
        if (!isMod) div.style.borderLeftColor = color;

        div.innerHTML = `
            <div class="message-header">
                <div class="entity-avatar" style="background:${color};width:24px;height:24px;font-size:0.65rem">
                    ${getInitials(msg.entity_name)}
                </div>
                <span class="message-sender" ${isMod ? '' : `style="color:${color}"`}>
                    ${msg.entity_name}
                </span>
                <span class="message-time">${formatTime(msg.timestamp)}</span>
            </div>
            <div class="message-content">${renderMarkdown(msg.content)}</div>
        `;
        container.appendChild(div);
    }

    renderedMessageCount = state.messages.length;
    container.scrollTop = container.scrollHeight;
}

function renderNewStoryboard() {
    const container = $('#storyboard');
    const newEntries = state.storyboard.slice(renderedStoryboardCount);

    if (!state.storyboard.length && !newEntries.length) {
        if (!container.querySelector('.empty-state')) {
            container.innerHTML = '<div class="empty-state">Summaries will appear here after each turn</div>';
        }
        return;
    }

    // Remove empty state
    const empty = container.querySelector('.empty-state');
    if (empty) empty.remove();

    for (const entry of newEntries) {
        const isConclusion = entry.summary.startsWith('CONCLUSION:');
        const div = document.createElement('div');
        div.className = `storyboard-entry ${isConclusion ? 'conclusion' : ''}`;
        div.innerHTML = `
            <div class="storyboard-turn">${isConclusion ? 'Conclusion' : `Turn ${entry.turn_number}`}</div>
            <div class="storyboard-speaker">${entry.speaker_name}</div>
            <div class="storyboard-text">${renderMarkdown(isConclusion ? entry.summary.replace('CONCLUSION: ', '') : entry.summary)}</div>
        `;
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
    const mod = getEntity(state.moderator_id);

    if (!state.is_active) {
        turnInfo.textContent = 'Discussion has concluded.';
        input.disabled = true;
        sendBtn.disabled = true;
        return;
    }

    if (!speaker) {
        turnInfo.textContent = 'Waiting...';
        input.disabled = true;
        sendBtn.disabled = true;
        return;
    }

    if (speaker.entity_type === 'ai') {
        turnInfo.textContent = `${speaker.name} (AI) is thinking...`;
        input.disabled = true;
        sendBtn.disabled = true;
    } else {
        turnInfo.textContent = `${speaker.name}'s turn to speak`;
        input.disabled = false;
        input.placeholder = `Type ${speaker.name}'s message...`;
        sendBtn.disabled = false;
        input.focus();
    }
}

function showTypingIndicator(name) {
    const container = $('#messages');
    const existing = container.querySelector('.typing-indicator');
    if (existing) existing.remove();

    const div = document.createElement('div');
    div.className = 'typing-indicator';
    div.innerHTML = `<span>${name} is thinking</span><div class="typing-dots"><span></span><span></span><span></span></div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

// ============================================================
// Event Handlers
// ============================================================

async function onAddEntity() {
    // Reset dialog
    $('#entity-name').value = '';
    $('#entity-type').value = 'human';
    hide('#ai-config');
    show('#entity-dialog');
    $('#entity-name').focus();
}

async function onConfirmEntity() {
    const name = $('#entity-name').value.trim();
    if (!name) return showToast('Please enter a name');

    const entityType = $('#entity-type').value;
    const params = {
        name,
        entity_type: entityType,
        avatar_color: $('#entity-color').value,
    };

    if (entityType === 'ai') {
        params.base_url = $('#ai-base-url').value;
        params.api_key = $('#ai-api-key').value;
        params.model = $('#ai-model').value;
        params.temperature = parseFloat($('#ai-temperature').value);
        params.max_tokens = parseInt($('#ai-max-tokens').value);
        params.system_prompt = $('#ai-system-prompt').value;
    }

    try {
        const result = await api.addEntity(params);
        if (result?.error) return showToast(result.error);
        const newState = await api.getState();
        onStateUpdate(newState);
        hide('#entity-dialog');
    } catch (e) {
        showToast('Failed to add entity: ' + e.message);
    }
}

async function onRemoveEntity(id) {
    await api.removeEntity(id);
    const newState = await api.getState();
    onStateUpdate(newState);
}

async function onSetModerator(id) {
    await api.setModerator(id);
    const newState = await api.getState();
    onStateUpdate(newState);
}

async function onStartDiscussion() {
    const topic = $('#topic-input').value.trim();
    if (!topic) return;
    await api.setTopic(topic);
    const result = await api.startDiscussion();
    if (result?.error) return showToast(result.error);
    onStateUpdate(result);

    hide('#setup-phase');
    show('#discussion-phase');
    renderedMessageCount = 0;
    renderedStoryboardCount = 0;
    renderDiscussion();

    // Start processing turns
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

        const newState = await api.getState();
        onStateUpdate(newState);

        // Complete turn (handles moderator summary + advance)
        await completeTurnFlow();
    } catch (e) {
        showToast('Failed to send: ' + e.message);
        input.disabled = false;
        $('#send-btn').disabled = false;
    }
}

async function completeTurnFlow() {
    const mod = getEntity(state.moderator_id);
    if (!mod) return;

    if (mod.entity_type === 'ai') {
        showTypingIndicator(mod.name + ' (summarizing)');
        try {
            const result = await api.completeTurn();
            if (result?.state) onStateUpdate(result.state);
            else {
                const s = await api.getState();
                onStateUpdate(s);
            }
        } catch (e) {
            showToast('Summary failed: ' + e.message);
            const s = await api.getState();
            onStateUpdate(s);
        }
        renderDiscussion();
        processCurrentTurn();
    } else {
        // Human moderator: prompt for summary
        promptModeratorInput('summary');
    }
}

function promptModeratorInput(mode) {
    const title = $('#moderator-dialog-title');
    const input = $('#moderator-input');

    if (mode === 'summary') {
        title.textContent = 'Moderator Summary';
        input.placeholder = 'Summarize the key points from this turn...';
    } else if (mode === 'mediation') {
        title.textContent = 'Moderator Mediation';
        input.placeholder = 'Enter your mediation or commentary...';
    }

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
        else {
            const s = await api.getState();
            onStateUpdate(s);
        }
        renderDiscussion();
        processCurrentTurn();
    } else if (mode === 'mediation') {
        await api.submitModeratorMessage(content);
        const s = await api.getState();
        onStateUpdate(s);
        renderDiscussion();
    }
}

async function processCurrentTurn() {
    if (!state.is_active || processing) return;

    const speaker = getEntity(state.current_speaker_id);
    if (!speaker) return;

    if (speaker.entity_type === 'ai') {
        processing = true;
        showTypingIndicator(speaker.name);
        renderDiscussion();

        try {
            const result = await api.generateAiTurn();
            if (result?.error) {
                showToast(result.error);
                processing = false;
                return;
            }

            const s = await api.getState();
            onStateUpdate(s);
            renderDiscussion();

            // Complete turn
            await completeTurnFlow();
        } catch (e) {
            showToast('AI turn failed: ' + e.message);
        }
        processing = false;
    } else {
        // Human's turn - enable input
        renderDiscussion();
    }
}

async function onReassign() {
    const list = $('#reassign-list');
    list.innerHTML = state.entities
        .filter(e => e.id !== state.moderator_id && state.turn_order.includes(e.id))
        .map(e => `
            <div class="reassign-item" onclick="doReassign('${e.id}')">
                <div class="entity-avatar" style="background:${e.avatar_color};width:28px;height:28px;font-size:0.7rem">
                    ${getInitials(e.name)}
                </div>
                <span>${e.name}</span>
                <span class="text-muted">${e.entity_type}</span>
            </div>
        `).join('');
    show('#reassign-dialog');
}

async function doReassign(entityId) {
    hide('#reassign-dialog');
    const result = await api.reassignTurn(entityId);
    if (result?.error) return showToast(result.error);
    if (result?.state) onStateUpdate(result.state);
    else {
        const s = await api.getState();
        onStateUpdate(s);
    }
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
            const s = await api.getState();
            onStateUpdate(s);
            renderDiscussion();
        } catch (e) {
            showToast('Mediation failed: ' + e.message);
        }
    } else {
        promptModeratorInput('mediation');
    }
}

async function onConclude() {
    const mod = getEntity(state.moderator_id);
    if (mod?.entity_type === 'ai') {
        showTypingIndicator(mod.name + ' (concluding)');
    }
    try {
        const result = await api.conclude();
        onStateUpdate(result);
        renderDiscussion();
    } catch (e) {
        showToast('Conclusion failed: ' + e.message);
    }
}

async function onBack() {
    await api.reset();
    const s = await api.getState();
    onStateUpdate(s);
    renderedMessageCount = 0;
    renderedStoryboardCount = 0;
    $('#messages').innerHTML = '';
    $('#storyboard').innerHTML = '';
    hide('#discussion-phase');
    show('#setup-phase');
    renderSetupEntities();
}

// ============================================================
// State Updates (called by pywebview push or Web API responses)
// ============================================================

function onStateUpdate(newState) {
    if (!newState) return;
    state = newState;

    if ($('#setup-phase') && !$('#setup-phase').classList.contains('hidden')) {
        renderSetupEntities();
    }
}

// ============================================================
// Initialization
// ============================================================

function init() {
    // Setup phase events
    $('#add-entity-btn').addEventListener('click', onAddEntity);
    $('#entity-type').addEventListener('change', (e) => {
        if (e.target.value === 'ai') show('#ai-config');
        else hide('#ai-config');
    });
    $('#confirm-entity-btn').addEventListener('click', onConfirmEntity);
    $('#cancel-entity-btn').addEventListener('click', () => hide('#entity-dialog'));
    $('#topic-input').addEventListener('input', updateStartButton);
    $('#start-btn').addEventListener('click', onStartDiscussion);

    // Discussion phase events
    $('#send-btn').addEventListener('click', onSendMessage);
    $('#message-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            onSendMessage();
        }
    });
    $('#reassign-btn').addEventListener('click', onReassign);
    $('#mediate-btn').addEventListener('click', onMediate);
    $('#conclude-btn').addEventListener('click', onConclude);
    $('#back-btn').addEventListener('click', onBack);

    // Moderator dialog events
    $('#confirm-moderator-btn').addEventListener('click', onConfirmModeratorInput);
    $('#cancel-moderator-btn').addEventListener('click', () => hide('#moderator-dialog'));
    $('#moderator-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            onConfirmModeratorInput();
        }
    });

    // Reassign dialog events
    $('#cancel-reassign-btn').addEventListener('click', () => hide('#reassign-dialog'));

    // Close dialogs on overlay click
    for (const overlay of $$('.dialog-overlay')) {
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) hide(overlay);
        });
    }

    // Entity dialog Enter key
    $('#entity-name').addEventListener('keydown', (e) => {
        if (e.key === 'Enter') onConfirmEntity();
    });

    // Load initial state
    api.getState().then(s => {
        onStateUpdate(s);
        renderSetupEntities();
    });
}

// ============================================================
// Bootstrap: detect pywebview vs web mode
// ============================================================

function bootstrap() {
    if (window.pywebview) {
        api = new DesktopAPI();
        init();
    } else {
        api = new WebAPI();
        init();
    }
}

if (window.pywebview) {
    bootstrap();
} else {
    window.addEventListener('pywebviewready', bootstrap);
    setTimeout(() => {
        if (!api) bootstrap();
    }, 500);
}
