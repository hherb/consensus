/**
 * @module discussion-actions
 * Discussion lifecycle actions — start, send, conclude, pause, resume, reassign, mediate.
 */

import { $, show, hide, showToast, escHtml, getInitials, TOAST_WARNING_DURATION_MS } from './utils.js';
import { state, onStateUpdate, getEntity, resetRenderedMessageCount, resetRenderedStoryboardCount, processing, setProcessing } from './state.js';
import { api } from './api.js';
import { renderDiscussion, showTypingIndicator } from './discussion.js';
import { renderSetupTab } from './setup.js';

/**
 * Start a new discussion.
 */
export async function onStartDiscussion() {
    const topic = $('#topic-input').value.trim();
    if (!topic) return;
    await api.setTopic(topic);
    const modParticipates = $('#mod-participates').checked;
    const maxRounds = parseInt($('#max-rounds')?.value) || 0;
    const costLimit = parseFloat($('#cost-limit')?.value) || 0;
    const result = await api.startDiscussion(modParticipates, maxRounds, costLimit);
    if (result?.error) return showToast(result.error);
    onStateUpdate(result);
    if (result.panel_advisory?.message) {
        showToast(result.panel_advisory.message, TOAST_WARNING_DURATION_MS, 'warning');
    }
    hide('#setup-phase');
    show('#discussion-phase');
    resetRenderedMessageCount(0);
    resetRenderedStoryboardCount(0);
    renderDiscussion();
    processCurrentTurn();
}

/**
 * Send a message (human input or moderator guidance while paused).
 */
export async function onSendMessage() {
    const input = $('#message-input');
    const content = input.value.trim();
    if (!content) return;

    if (state.status === 'concluded') {
        input.value = '';
        input.disabled = true;
        $('#send-btn').disabled = true;
        try {
            const result = await api.continueDiscussion(content);
            if (result?.error) { showToast(result.error); input.disabled = false; $('#send-btn').disabled = false; return; }
            onStateUpdate(result);
            renderDiscussion();
            processCurrentTurn();
        } catch (e) {
            showToast('Failed to continue: ' + e.message);
            input.disabled = false;
            $('#send-btn').disabled = false;
        }
        return;
    }

    if (state.status === 'paused') {
        const speaker = getEntity(state.current_speaker_id);
        if (speaker && speaker.entity_type === 'human' && speaker.id !== state.moderator_id) {
            input.value = '';
            const result = await api.submitMessage(speaker.id, content);
            if (result?.error) return showToast(result.error);
            renderDiscussion();
            return;
        }
        input.value = '';
        const result = await api.submitModeratorMessage(content);
        if (result?.error) return showToast(result.error);
        renderDiscussion();
        return;
    }

    if (!state.current_speaker_id) return;
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

/**
 * Insert an `[evidence: ]` marker at the caret in the message input, so a
 * human participant can cite grounding evidence for a `track_evidence`
 * phase. Caret is placed just before the closing bracket so the user can
 * type the citation immediately.
 */
export function insertEvidenceMarker() {
    const input = $('#message-input');
    if (!input) return;
    const marker = '[evidence: ]';
    const pos = input.selectionStart ?? input.value.length;
    input.value = input.value.slice(0, pos) + marker + input.value.slice(pos);
    // Place caret just before the closing bracket.
    const caret = pos + marker.length - 1;
    input.focus();
    input.setSelectionRange(caret, caret);
}

/**
 * Complete the current turn — AI moderator auto-summarizes, human moderator gets prompted.
 * @returns {Promise<boolean>} True if turn is fully completed, false if waiting for input
 */
async function completeTurnFlow() {
    if (!state.is_active || state.status === 'concluded') return false;
    const mod = getEntity(state.moderator_id);
    if (!mod) return true;
    if (mod.entity_type === 'ai') {
        showTypingIndicator(mod.name + ' (summarizing)');
        try {
            const result = await api.completeTurn();
            if (result?.error) {
                // Discussion may have been concluded while we were waiting
                onStateUpdate(await api.getState());
                renderDiscussion();
                return false;
            }
            if (result?.state) onStateUpdate(result.state);
            else onStateUpdate(await api.getState());
            if (await handleTurnLimitFlags(result)) return false;
        } catch (e) {
            showToast('Summary failed: ' + e.message);
            onStateUpdate(await api.getState());
        }
        renderDiscussion();
        return state.is_active && state.status !== 'concluded';
    } else {
        promptModeratorInput('summary');
        return false;
    }
}

/**
 * Handle terminal flags returned by completeTurn (max rounds, cost limit,
 * or an exhausted discussion method). Without this, a completed method
 * would keep generating turns in its final phase indefinitely.
 * @returns {Promise<boolean>} True if a terminal flag was handled.
 */
async function handleTurnLimitFlags(result) {
    if (result?.max_rounds_reached) {
        renderDiscussion();
        showToast('Max rounds reached — concluding discussion');
        await onConclude();
        return true;
    }
    if (result?.method_complete) {
        renderDiscussion();
        // A blocked method switch (e.g. a model without tool support)
        // must surface its reason instead of the generic completion toast.
        showToast(result.switch_error
            ? 'Method switch failed: ' + result.switch_error
            : 'All method phases complete — concluding discussion');
        await onConclude();
        return true;
    }
    if (result?.cost_limit_reached) {
        renderDiscussion();
        showCostLimitDialog(result.total_cost, result.cost_limit);
        return true;
    }
    return false;
}

/**
 * Show the moderator input dialog for summary or mediation.
 * @param {string} mode - 'summary' or 'mediation'
 */
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

/**
 * Handle confirmation of moderator input dialog.
 */
export async function onConfirmModeratorInput() {
    const input = $('#moderator-input');
    const content = input.value.trim();
    if (!content) return showToast('Please enter text');
    const mode = input.dataset.mode;
    hide('#moderator-dialog');

    if (mode === 'summary') {
        const result = await api.completeTurn(content);
        if (result?.state) onStateUpdate(result.state);
        else onStateUpdate(await api.getState());
        if (await handleTurnLimitFlags(result)) return;
        renderDiscussion();
        processCurrentTurn();
    } else {
        await api.submitModeratorMessage(content);
        onStateUpdate(await api.getState());
        renderDiscussion();
    }
}

/**
 * Process the current turn — loops through sequential AI speakers.
 */
export async function processCurrentTurn() {
    if (!state.is_active || state.status === 'concluded' || processing) return;
    setProcessing(true);
    try {
        while (state.is_active && state.status !== 'concluded') {
            const speaker = getEntity(state.current_speaker_id);
            if (!speaker || speaker.entity_type !== 'ai') {
                renderDiscussion();
                break;
            }
            showTypingIndicator(speaker.name);
            renderDiscussion();
            const result = await api.generateAiTurn();
            // Re-check after await — discussion may have been concluded
            if (!state.is_active || state.status === 'concluded') break;
            if (result?.cost_limit_reached) {
                onStateUpdate(await api.getState());
                renderDiscussion();
                showCostLimitDialog(result.total_cost, result.cost_limit);
                break;
            }
            if (result?.error && !result?.skipped) { showToast(result.error); break; }
            if (result?.skipped) showToast(`${speaker.name} skipped due to API error`, 5000, 'warning');
            if (result?.warning) showToast(result.warning, 5000, 'info');
            onStateUpdate(await api.getState());
            if (!state.is_active || state.status === 'concluded') break;
            renderDiscussion();
            const turnCompleted = await completeTurnFlow();
            if (!turnCompleted) break;
        }
    } catch (e) {
        showToast('AI turn failed: ' + e.message);
    } finally {
        setProcessing(false);
    }
}

/**
 * Show the reassign turn dialog.
 */
export async function onReassign() {
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

/**
 * Execute turn reassignment to a specific entity.
 * @param {number} entityId
 */
export async function doReassign(entityId) {
    hide('#reassign-dialog');
    const result = await api.reassignTurn(entityId);
    if (result?.error) return showToast(result.error);
    if (result?.state) onStateUpdate(result.state);
    else onStateUpdate(await api.getState());
    renderDiscussion();
    processCurrentTurn();
}

/**
 * Trigger moderator mediation (AI auto-mediates, human gets prompted).
 */
export async function onMediate() {
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

/**
 * Conclude the discussion.
 */
export async function onConclude() {
    const mod = getEntity(state.moderator_id);
    if (mod?.entity_type === 'ai') showTypingIndicator(mod.name + ' (concluding)');
    try {
        const result = await api.conclude();
        onStateUpdate(result);
        renderDiscussion();
    } catch (e) { showToast('Conclusion failed: ' + e.message); }
}

/**
 * Pause the active discussion.
 */
export async function onPause() {
    const result = await api.pauseDiscussion();
    if (result?.error) return showToast(result.error);
    onStateUpdate(result);
    renderDiscussion();
}

/**
 * Resume a paused discussion (optionally with a moderator prompt).
 */
export async function onResume() {
    const input = $('#message-input');
    const resumePrompt = input.value.trim();
    if (resumePrompt) {
        input.value = '';
        const msgResult = await api.submitModeratorMessage(resumePrompt);
        if (msgResult?.error) showToast(msgResult.error);
    }
    const result = await api.resumeDiscussion();
    if (result?.error) return showToast(result.error);
    onStateUpdate(result);
    renderDiscussion();
    processCurrentTurn();
}

/**
 * Reopen a concluded discussion from history (load + transition to paused).
 * @param {number} id - Discussion ID
 */
export async function reopenFromHistory(id) {
    const result = await api.loadDiscussion(id);
    if (result?.error) return showToast(result.error);
    onStateUpdate(result);
    hide('#setup-phase');
    show('#discussion-phase');
    resetRenderedMessageCount(0);
    resetRenderedStoryboardCount(0);
    renderDiscussion();
    await onReopen();
}

/**
 * Reopen the currently loaded concluded discussion.
 */
export async function onReopen() {
    const result = await api.reopenDiscussion();
    if (result?.error) return showToast(result.error);
    onStateUpdate(result);
    resetRenderedMessageCount(0);
    resetRenderedStoryboardCount(0);
    renderDiscussion();
}

/**
 * Go back to the setup phase from the discussion view.
 */
export async function onBack() {
    await api.reset();
    onStateUpdate(await api.getState());
    resetRenderedMessageCount(0);
    resetRenderedStoryboardCount(0);
    $('#messages').innerHTML = '';
    $('#storyboard').innerHTML = '';
    hide('#discussion-phase');
    show('#setup-phase');
    renderSetupTab();
}

// -- Cost limit dialog helpers --

function showCostLimitDialog(totalCost, costLimit) {
    $('#cost-limit-total').textContent = totalCost?.toFixed(2) || '?';
    $('#cost-limit-amount').textContent = costLimit?.toFixed(2) || '?';
    // Suggest a new limit: double, rounded up to nearest $0.50
    const doubled = (costLimit || 1) * 2;
    const suggested = Math.ceil(doubled * 2) / 2;
    $('#cost-limit-new').value = suggested.toFixed(2);
    show('#cost-limit-dialog');
}

/**
 * Continue the discussion with a new cost limit.
 */
export async function onCostLimitContinue() {
    const newLimit = parseFloat($('#cost-limit-new').value) || 0;
    if (newLimit <= 0) return showToast('Please enter a valid cost limit');
    hide('#cost-limit-dialog');
    await api.setCostLimit(newLimit);
    onStateUpdate(await api.getState());
    renderDiscussion();
    processCurrentTurn();
}

/**
 * Conclude the discussion when cost limit is reached.
 */
export async function onCostLimitConclude() {
    hide('#cost-limit-dialog');
    await onConclude();
}
