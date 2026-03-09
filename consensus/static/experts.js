/**
 * @module experts
 * Expert consultation UI — dialog and tool progress indicator.
 */

import { $, show, hide, showToast, escHtml } from './utils.js';
import { state, onStateUpdate, progressStallTimer, setProgressStallTimer } from './state.js';
import { api } from './api.js';
import { renderDiscussion } from './discussion.js';

const STALL_TIMEOUT_MS = 60000;

/**
 * Show the expert consultation dialog with available experts.
 */
export function showConsultExpertDialog() {
    const experts = (state.experts || []);
    if (!experts.length) {
        showToast('No experts configured');
        return;
    }
    const optionsHtml = experts.map(ex =>
        `<option value="${escHtml(ex.entity_name || ex.name || '')}">${escHtml(ex.entity_name || ex.name || 'Expert')}</option>`
    ).join('');

    let overlay = $('#consult-expert-dialog');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'consult-expert-dialog';
        overlay.className = 'dialog-overlay hidden';
        overlay.innerHTML = `<div class="dialog">
            <h3>Consult Expert</h3>
            <div class="form-group">
                <label for="expert-select">Expert</label>
                <select id="expert-select"></select>
            </div>
            <div class="form-group">
                <label for="expert-query">Query</label>
                <textarea id="expert-query" rows="4" placeholder="What would you like to ask the expert?"></textarea>
            </div>
            <div class="dialog-actions">
                <button class="btn btn-ghost" id="cancel-consult-expert">Cancel</button>
                <button class="btn btn-primary" id="confirm-consult-expert">Consult</button>
            </div>
        </div>`;
        document.getElementById('app').appendChild(overlay);
        overlay.addEventListener('click', (e) => { if (e.target === overlay) hide(overlay); });
        $('#cancel-consult-expert').addEventListener('click', () => hide(overlay));
        $('#confirm-consult-expert').addEventListener('click', doConsultExpert);
    }
    $('#expert-select').innerHTML = optionsHtml;
    $('#expert-query').value = '';
    show(overlay);
    $('#expert-query').focus();
}

/**
 * Execute expert consultation with the selected expert and query.
 */
async function doConsultExpert() {
    const expertName = $('#expert-select').value;
    const query = $('#expert-query').value.trim();
    if (!query) return showToast('Please enter a query');
    hide('#consult-expert-dialog');
    showToast('Consulting expert...', 3000, 'info');
    try {
        const result = await api.consultExpert(expertName, query);
        if (result && result.is_error) {
            showToast('Expert error: ' + (result.content || 'Unknown error'), 5000, 'error');
        } else {
            onStateUpdate(await api.getState());
            renderDiscussion();
            showToast('Expert response received', 3000, 'success');
        }
    } catch (e) {
        showToast('Consult failed: ' + e.message, 5000, 'error');
    }
}

/**
 * Handle tool progress events (SSE or pywebview callback).
 * Updates typing indicator with progress info and manages stall detection.
 * @param {object} data - Progress event data
 * @param {string} [data.entity_name] - Entity performing the action
 * @param {string} [data.message] - Progress message
 * @param {number} [data.progress] - Current progress count
 * @param {number} [data.total] - Total expected count
 */
export function onToolProgress(data) {
    const container = $('#messages');
    if (!container) return;

    let indicator = container.querySelector('.typing-indicator');
    if (!indicator) {
        indicator = document.createElement('div');
        indicator.className = 'typing-indicator';
        container.appendChild(indicator);
    }

    const { entity_name, message, progress, total } = data;

    let progressText = message || 'Working...';
    let barHtml = '';

    if (total && total > 0) {
        const pct = Math.round((progress / total) * 100);
        progressText += ` (${progress}/${total})`;
        barHtml = `<div class="progress-bar-container">
            <div class="progress-bar-fill" style="width: ${pct}%"></div>
        </div>`;
    }

    indicator.innerHTML = `
        <div class="expert-progress">
            <span class="typing-name">${escHtml(entity_name || '')}</span>:
            <span class="typing-status">${escHtml(progressText)}</span>
            ${barHtml}
        </div>`;
    show(indicator);
    container.scrollTop = container.scrollHeight;

    if (progressStallTimer) clearTimeout(progressStallTimer);
    setProgressStallTimer(setTimeout(() => {
        const ind = container.querySelector('.typing-indicator');
        if (ind && ind.querySelector('.expert-progress')) {
            const status = ind.querySelector('.typing-status');
            if (status) status.textContent = 'Still working...';
        }
    }, STALL_TIMEOUT_MS));
}
