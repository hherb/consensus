/**
 * @module ask-user
 * Handles the ask_user interactive tool — displays an inline input bubble
 * when an AI participant requests user input mid-turn.
 */

import { $, escHtml, getInitials, renderMarkdown, showToast } from './utils.js';
import { api } from './api.js';
import { state } from './state.js';

/** @type {string|null} Currently pending request ID */
let pendingRequestId = null;

/**
 * Handle a user_input_request event from the backend.
 * Shows an inline input bubble in the messages panel.
 * @param {object} data - {request_id, discussion_id, entity_id, entity_name, question, context}
 */
export function onUserInputRequest(data) {
    pendingRequestId = data.request_id;

    const container = $('#messages');
    if (!container) return;

    // Remove typing indicator if present
    const typing = container.querySelector('.typing-indicator');
    if (typing) typing.remove();

    // Find entity for avatar color
    const entity = (state.entities || []).find(e => e.id === data.entity_id);
    const color = entity?.avatar_color || '#666';

    const div = document.createElement('div');
    div.className = 'message user-input-request';
    div.dataset.requestId = data.request_id;
    div.style.borderLeftColor = color;

    const contextHtml = data.context
        ? `<div class="user-input-context">${renderMarkdown(data.context)}</div>`
        : '';

    div.innerHTML = `
        <div class="message-header">
            <div class="entity-avatar" style="background:${color};width:24px;height:24px;font-size:0.65rem">${getInitials(data.entity_name)}</div>
            <span class="message-sender" style="color:${color}">${escHtml(data.entity_name)}</span>
            <span class="text-muted" style="font-size:0.75rem;margin-left:0.25rem">asks:</span>
        </div>
        <div class="message-content">${renderMarkdown(data.question)}</div>
        ${contextHtml}
        <div class="user-input-area">
            <textarea class="user-input-response" placeholder="Type your answer..." rows="3"></textarea>
            <button class="btn btn-primary user-input-submit-btn">Submit</button>
        </div>`;

    container.appendChild(div);

    const textarea = div.querySelector('.user-input-response');
    const submitBtn = div.querySelector('.user-input-submit-btn');

    // Submit on button click
    submitBtn.addEventListener('click', () => submitUserInputResponse(div));

    // Submit on Enter (without Shift)
    textarea.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            submitUserInputResponse(div);
        }
    });

    textarea.focus();
    container.scrollTop = container.scrollHeight;
}

/**
 * Submit the user's response to the pending ask_user request.
 * @param {HTMLElement} bubbleEl - The input bubble element
 */
async function submitUserInputResponse(bubbleEl) {
    const textarea = bubbleEl.querySelector('.user-input-response');
    const submitBtn = bubbleEl.querySelector('.user-input-submit-btn');
    const content = textarea.value.trim();

    if (!content) {
        showToast('Please enter a response', 1500, 'warning');
        textarea.focus();
        return;
    }

    if (!pendingRequestId) {
        showToast('No pending input request', 1500, 'error');
        return;
    }

    // Disable input to prevent double-submit
    textarea.disabled = true;
    submitBtn.disabled = true;
    submitBtn.textContent = 'Submitting...';

    try {
        const result = await api.submitUserInput(pendingRequestId, content);
        if (result?.error) {
            showToast(result.error, 3000, 'error');
            textarea.disabled = false;
            submitBtn.disabled = false;
            submitBtn.textContent = 'Submit';
            return;
        }

        // Replace the input bubble with a static answer display
        const inputArea = bubbleEl.querySelector('.user-input-area');
        inputArea.innerHTML = `
            <div class="user-input-answer">
                <span class="text-muted" style="font-size:0.75rem">Your answer:</span>
                <div class="message-content">${renderMarkdown(content)}</div>
            </div>`;
        bubbleEl.classList.add('answered');

        pendingRequestId = null;
    } catch (err) {
        showToast(`Failed to submit: ${err.message}`, 3000, 'error');
        textarea.disabled = false;
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit';
    }
}

/**
 * Re-show input bubble if there's a pending request (e.g. after page reload).
 * Called from state update when pending_user_input is present.
 * @param {object|null} pendingData - pending_user_input from app state
 */
export function checkPendingUserInput(pendingData) {
    if (!pendingData) return;
    // Don't re-show if we already have this request displayed
    const existing = document.querySelector(`.user-input-request[data-request-id="${pendingData.request_id}"]`);
    if (existing) return;
    onUserInputRequest(pendingData);
}
