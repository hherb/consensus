/**
 * @module discussion
 * Live discussion phase rendering — messages, storyboard, sidebar, input area.
 */

import { $, show, hide, escHtml, getInitials, formatTime, renderMarkdown } from './utils.js';
import { state, renderedMessageCount, renderedStoryboardCount, syncRenderedMessageCount, syncRenderedStoryboardCount, getEntity } from './state.js';
import { calculateDiscussionCost } from './export.js';
import { renderMessageImages, showLightbox, loadImageSrc } from './images.js';

/**
 * Render the full discussion view (header, messages, storyboard, input area).
 */
export function renderDiscussion() {
    $('#discussion-topic').textContent = state.topic;
    const speaker = getEntity(state.current_speaker_id);
    const badge = $('#turn-badge');
    if (state.status === 'paused') {
        badge.textContent = 'Paused';
        badge.className = 'badge paused';
    } else if (speaker && state.is_active) {
        const roundInfo = state.max_rounds > 0
            ? ` (round ${state.current_round}/${state.max_rounds})`
            : '';
        badge.textContent = `Turn ${state.turn_number}: ${speaker.name}${roundInfo}`;
        badge.className = 'badge active';
    } else if (state.status === 'concluded' && state.messages.length > 0) {
        badge.textContent = 'Concluded';
        badge.className = 'badge';
    } else {
        badge.textContent = '';
    }
    const costEl = $('#cost-badge');
    if (costEl) {
        const totalCost = calculateDiscussionCost(state.messages);
        const limit = state.cost_limit || 0;
        if (totalCost > 0 || limit > 0) {
            const limitStr = limit > 0 ? ` / $${limit.toFixed(2)}` : '';
            costEl.textContent = `Cost: $${totalCost.toFixed(2)}${limitStr}`;
            costEl.style.color = (limit > 0 && totalCost / limit > 0.8)
                ? 'var(--warning, #e67e22)' : '';
        } else {
            costEl.textContent = '';
            costEl.style.color = '';
        }
    }
    if (state.status === 'active') {
        show('#pause-btn'); hide('#resume-btn'); hide('#reopen-btn');
        show('#reassign-btn'); show('#mediate-btn'); show('#conclude-btn');
    } else if (state.status === 'paused') {
        hide('#pause-btn'); show('#resume-btn'); hide('#reopen-btn');
        hide('#reassign-btn'); hide('#mediate-btn'); show('#conclude-btn');
    } else if (state.status === 'concluded') {
        hide('#pause-btn'); hide('#resume-btn'); show('#reopen-btn');
        hide('#reassign-btn'); hide('#mediate-btn'); hide('#conclude-btn');
    } else {
        hide('#pause-btn'); hide('#resume-btn'); hide('#reopen-btn');
        hide('#reassign-btn'); hide('#mediate-btn'); hide('#conclude-btn');
    }
    renderSidebarEntities();
    renderNewMessages();
    renderNewStoryboard();
    updateInputArea();
}

/**
 * Render sidebar entity list with speaking indicators and pause management.
 */
function renderSidebarEntities() {
    const roles = state.member_roles || {};
    $('#discussion-entities').innerHTML = state.entities.map(e => {
        const isSpeaking = e.id === state.current_speaker_id && state.is_active;
        const isMod = e.id === state.moderator_id;
        const isDA = roles[String(e.id)] === 'devils_advocate';
        const canRemove = state.status === 'paused' && !isMod;
        return `
            <div class="entity-sidebar-item ${isSpeaking ? 'speaking' : ''}">
                <div class="entity-avatar" style="background:${e.avatar_color}">${getInitials(e.name)}</div>
                <div style="flex:1">
                    <div class="entity-name">${escHtml(e.name)}${isMod ? ' <span class="moderator-badge">MOD</span>' : ''}${isDA ? ' <span class="da-badge">DA</span>' : ''}</div>
                    <div class="entity-type">${e.entity_type === 'ai' ? e.ai_config?.model || 'AI' : 'Human'}</div>
                </div>
                ${isSpeaking ? '<div class="speaking-indicator"></div>' : ''}
                ${canRemove ? `<button class="btn btn-ghost btn-sm" data-action="remove-from-discussion" data-id="${e.id}" title="Remove" style="padding:0.1rem 0.3rem;font-size:0.75rem">✕</button>` : ''}
            </div>`;
    }).join('');

    const mgmt = $('#pause-management');
    if (state.status === 'paused') {
        show(mgmt);
        renderPauseAvailableEntities();
    } else {
        hide(mgmt);
    }
}

/**
 * Render the list of entities available to add while discussion is paused.
 */
function renderPauseAvailableEntities() {
    const container = $('#pause-available-entities');
    const inDiscussion = new Set(state.entities.map(e => e.id));
    const available = (state.saved_entities || []).filter(e => !inDiscussion.has(e.id));
    if (!available.length) {
        container.innerHTML = '<div class="text-muted" style="font-size:0.8rem">No additional profiles available</div>';
        return;
    }
    container.innerHTML = available.map(e => `
        <div class="settings-item" style="padding:0.25rem 0;display:flex;align-items:center;gap:0.5rem">
            <div class="entity-avatar" style="background:${e.avatar_color};width:24px;height:24px;font-size:0.6rem">${getInitials(e.name)}</div>
            <span style="font-size:0.85rem;flex:1">${escHtml(e.name)}</span>
            <button class="btn btn-outline btn-sm" data-action="add-to-discussion" data-id="${e.id}" style="font-size:0.75rem;padding:0.15rem 0.4rem">Add</button>
        </div>
    `).join('');
}

/**
 * Render discussion-level images at the top of the message flow.
 * Called once when the first messages arrive; updates if images change.
 */
/** Track which image IDs are currently rendered in the banner */
let _renderedDiscussionImageIds = '';

function renderDiscussionImages() {
    const container = $('#messages');
    const images = state.discussion_images || [];
    const existing = container.querySelector('.discussion-images-banner');
    const imageKey = images.map(img => img.id).join(',');

    if (images.length === 0) {
        if (existing) existing.remove();
        _renderedDiscussionImageIds = '';
        return;
    }

    // Skip rebuild if images haven't changed
    if (existing && imageKey === _renderedDiscussionImageIds) return;
    _renderedDiscussionImageIds = imageKey;

    // Build or rebuild the banner
    const banner = existing || document.createElement('div');
    banner.className = 'discussion-images-banner';
    banner.innerHTML = images.map(img => `
        <img class="discussion-image" data-image-id="${img.id}"
             alt="${escHtml(img.title || img.original_filename || 'Image')}"
             loading="lazy">
    `).join('');

    // Load image sources asynchronously and wire lightbox clicks
    banner.querySelectorAll('.discussion-image').forEach(imgEl => {
        loadImageSrc(imgEl, parseInt(imgEl.dataset.imageId));
        imgEl.addEventListener('click', () => showLightbox(imgEl.src, imgEl.alt));
    });

    if (!existing) {
        container.insertBefore(banner, container.firstChild);
    }
}

/**
 * Render only new messages since last render (incremental append).
 */
function renderNewMessages() {
    const container = $('#messages');
    renderDiscussionImages();
    const newMessages = state.messages.slice(renderedMessageCount);
    const typing = container.querySelector('.typing-indicator');
    if (typing) typing.remove();

    for (const msg of newMessages) {
        const entity = getEntity(msg.entity_id);
        const color = entity?.avatar_color || '#666';
        const isMod = msg.role === 'moderator';
        const isExpert = entity && entity.entity_type === 'expert';
        const expertBadge = isExpert ? '<span class="expert-badge">Expert</span>' : '';
        const div = document.createElement('div');
        div.className = `message ${isMod ? 'moderator' : ''} ${msg.role === 'system' ? 'system' : ''}`;
        if (!isMod) div.style.borderLeftColor = color;

        let metaHtml = '';
        if (msg.model_used) {
            const costStr = msg.cost != null ? ` | $${msg.cost.toFixed(4)}` : '';
            metaHtml = `<span class="text-muted" style="font-size:0.7rem;margin-left:0.5rem">${msg.model_used} | ${msg.total_tokens}tok | ${msg.latency_ms}ms${costStr}</span>`;
        }

        let toolCallsHtml = '';
        if (msg.tool_calls && msg.tool_calls.length > 0) {
            toolCallsHtml = msg.tool_calls.map(tc => {
                const argsStr = typeof tc.arguments === 'object' ? JSON.stringify(tc.arguments) : tc.arguments;
                const statusIcon = tc.is_error ? '&#x26A0;' : '&#x2705;';
                const statusClass = tc.is_error ? 'tool-error' : 'tool-success';
                return `<details class="tool-call ${statusClass}">
                    <summary>${statusIcon} <strong>${escHtml(tc.tool_name)}</strong>(${escHtml(argsStr)})${tc.latency_ms ? ` <span class="text-muted">${tc.latency_ms}ms</span>` : ''}</summary>
                    <pre class="tool-result">${escHtml(tc.result)}</pre>
                </details>`;
            }).join('');
        }

        div.innerHTML = `
            <div class="message-header">
                <div class="entity-avatar" style="background:${color};width:24px;height:24px;font-size:0.65rem">${getInitials(msg.entity_name)}</div>
                <span class="message-sender" ${isMod ? '' : `style="color:${color}"`}>${escHtml(msg.entity_name)}${expertBadge}</span>
                ${metaHtml}
                <span class="message-time">${formatTime(msg.timestamp)}</span>
            </div>
            ${toolCallsHtml}
            <div class="message-content">${renderMarkdown(msg.content)}</div>`;
        container.appendChild(div);
        // Render inline images if present
        if (msg.image_ids) renderMessageImages(div, msg.image_ids);
    }
    syncRenderedMessageCount();
    container.scrollTop = container.scrollHeight;
}

/**
 * Render only new storyboard entries since last render (incremental append).
 */
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
    syncRenderedStoryboardCount();
    container.scrollTop = container.scrollHeight;
}

/**
 * Update the input area based on current discussion state (active, paused, concluded).
 */
function updateInputArea() {
    const input = $('#message-input');
    const sendBtn = $('#send-btn');
    const turnInfo = $('#turn-info');
    const speaker = getEntity(state.current_speaker_id);

    const consultBtn = $('#consult-expert-btn');
    if (consultBtn) {
        const hasExperts = (state.experts || []).length > 0;
        if (hasExperts && state.is_active) show(consultBtn);
        else hide(consultBtn);
    }

    if (state.status === 'paused') {
        turnInfo.textContent = 'Discussion is paused. Manage participants, then click Resume.';
        input.disabled = false; sendBtn.disabled = false;
        input.placeholder = 'Optional: Enter a prompt to guide the resumed discussion...';
        return;
    }
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

/**
 * Show a typing indicator for an entity in the messages panel.
 * @param {string} name - Entity name
 */
export function showTypingIndicator(name) {
    const container = $('#messages');
    const existing = container.querySelector('.typing-indicator');
    if (existing) existing.remove();
    const div = document.createElement('div');
    div.className = 'typing-indicator';
    div.innerHTML = `<span>${escHtml(name)} is thinking</span><div class="typing-dots"><span></span><span></span><span></span></div>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}
