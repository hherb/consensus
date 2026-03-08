/* === Consensus Evaluation UI === */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);
const show = (sel) => { const el = typeof sel === 'string' ? $(sel) : sel; if (el) el.classList.remove('hidden'); };
const hide = (sel) => { const el = typeof sel === 'string' ? $(sel) : sel; if (el) el.classList.add('hidden'); };

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

const API = {
    async _fetch(method, path, data = null) {
        const opts = { method, headers: { 'Content-Type': 'application/json' } };
        if (data) opts.body = JSON.stringify(data);
        const resp = await fetch('/eval/api' + path, opts);
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ error: resp.statusText }));
            throw new Error(err.error || resp.statusText);
        }
        return resp.json();
    },
    listCases() { return this._fetch('GET', '/cases'); },
    addCase(data) { return this._fetch('POST', '/cases', data); },
    updateCase(id, data) { return this._fetch('PUT', `/cases/${id}`, data); },
    deleteCase(id) { return this._fetch('DELETE', `/cases/${id}`); },

    listConditions() { return this._fetch('GET', '/conditions'); },
    addCondition(data) { return this._fetch('POST', '/conditions', data); },
    updateCondition(id, data) { return this._fetch('PUT', `/conditions/${id}`, data); },
    deleteCondition(id) { return this._fetch('DELETE', `/conditions/${id}`); },

    listBatches() { return this._fetch('GET', '/batches'); },
    getBatch(id) { return this._fetch('GET', `/batches/${id}`); },
    startBatch(data) { return this._fetch('POST', '/batches/run', data); },
    cancelBatch(id) { return this._fetch('POST', `/batches/${id}/cancel`); },

    getRun(id) { return this._fetch('GET', `/runs/${id}`); },
    scoreRun(id) { return this._fetch('POST', `/runs/${id}/score`); },
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let state = { cases: [], conditions: [], batches: [] };
let activeBatchId = null;
let pollTimer = null;

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

function showToast(msg, duration = 3000) {
    const el = $('#toast');
    el.textContent = msg;
    show(el);
    clearTimeout(el._timer);
    el._timer = setTimeout(() => hide(el), duration);
}

// ---------------------------------------------------------------------------
// Tabs
// ---------------------------------------------------------------------------

function switchTab(name) {
    $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    $$('.tab-content').forEach(tc => {
        tc.classList.toggle('hidden', tc.id !== `tab-${name}`);
    });
    if (name === 'cases') renderCases();
    else if (name === 'conditions') renderConditions();
    else if (name === 'dashboard') renderDashboard();
    else if (name === 'results') renderResults();
}

// ---------------------------------------------------------------------------
// Cases
// ---------------------------------------------------------------------------

async function loadCases() {
    try { state.cases = await API.listCases(); } catch (e) { showToast(e.message); }
}

function renderCases() {
    const el = $('#cases-list');
    if (!state.cases.length) {
        el.innerHTML = '<div class="empty-state">No cases yet. Add one to get started.</div>';
        return;
    }
    el.innerHTML = state.cases.map(c => `
        <div class="settings-item">
            <div class="item-info">
                <div class="item-title">${esc(c.case_key)}: ${esc(c.title)}</div>
                <div class="item-meta">
                    <span class="badge badge-${c.difficulty}">${c.difficulty}</span>
                    ${esc(c.gold_diagnosis)}
                    &mdash; ${c.aliases.length} aliases, ${c.findings.length} findings, ${c.differentials.length} differentials
                </div>
            </div>
            <div class="item-actions">
                <button class="btn btn-sm btn-ghost" data-action="edit-case" data-id="${c.id}">Edit</button>
                <button class="btn btn-sm btn-ghost" data-action="delete-case" data-id="${c.id}">Delete</button>
            </div>
        </div>
    `).join('');
}

function openCaseDialog(c = null) {
    $('#case-dialog-title').textContent = c ? 'Edit Case' : 'Add Case';
    $('#case-edit-id').value = c ? c.id : '';
    $('#case-key').value = c ? c.case_key : '';
    $('#case-title').value = c ? c.title : '';
    $('#case-presentation').value = c ? c.presentation : '';
    $('#case-gold').value = c ? c.gold_diagnosis : '';
    $('#case-difficulty').value = c ? c.difficulty : 'moderate';
    $('#case-aliases').value = c ? c.aliases.join('\n') : '';
    $('#case-findings').value = c ? c.findings.join('\n') : '';
    $('#case-differentials').value = c ? c.differentials.join('\n') : '';
    $('#case-source').value = c ? c.source : '';
    show('#case-dialog');
    $('#case-key').focus();
}

function parseLinesNonEmpty(text) {
    return text.split('\n').map(s => s.trim()).filter(Boolean);
}

async function confirmCase() {
    const data = {
        case_key: $('#case-key').value.trim(),
        title: $('#case-title').value.trim(),
        presentation: $('#case-presentation').value.trim(),
        gold_diagnosis: $('#case-gold').value.trim(),
        difficulty: $('#case-difficulty').value,
        source: $('#case-source').value.trim(),
        aliases: parseLinesNonEmpty($('#case-aliases').value),
        findings: parseLinesNonEmpty($('#case-findings').value),
        differentials: parseLinesNonEmpty($('#case-differentials').value),
    };
    if (!data.case_key || !data.title || !data.presentation || !data.gold_diagnosis) {
        return showToast('Case key, title, presentation, and gold diagnosis are required');
    }
    try {
        const editId = $('#case-edit-id').value;
        if (editId) await API.updateCase(editId, data);
        else await API.addCase(data);
        hide('#case-dialog');
        await loadCases();
        renderCases();
    } catch (e) { showToast(e.message); }
}

async function deleteCase(id) {
    if (!confirm('Delete this case?')) return;
    try {
        await API.deleteCase(id);
        await loadCases();
        renderCases();
    } catch (e) { showToast(e.message); }
}

// ---------------------------------------------------------------------------
// Conditions
// ---------------------------------------------------------------------------

async function loadConditions() {
    try { state.conditions = await API.listConditions(); } catch (e) { showToast(e.message); }
}

function renderConditions() {
    const el = $('#conditions-list');
    if (!state.conditions.length) {
        el.innerHTML = '<div class="empty-state">No conditions yet.</div>';
        return;
    }
    el.innerHTML = state.conditions.map(c => {
        const flags = [];
        if (c.enable_da) flags.push('<span class="badge badge-da">DA</span>');
        if (c.enable_memory) flags.push('<span class="badge badge-running">Memory</span>');
        if (c.enable_tools) flags.push('<span class="badge badge-success">Tools</span>');
        return `
        <div class="settings-item">
            <div class="item-info">
                <div class="item-title">${esc(c.name)}</div>
                <div class="item-meta">
                    ${esc(c.description)} &mdash; ${c.participants.length} participants, ${c.num_rounds} rounds
                    ${flags.join(' ')}
                </div>
            </div>
            <div class="item-actions">
                <button class="btn btn-sm btn-ghost" data-action="edit-condition" data-id="${c.id}">Edit</button>
                <button class="btn btn-sm btn-ghost" data-action="delete-condition" data-id="${c.id}">Delete</button>
            </div>
        </div>`;
    }).join('');
}

function openConditionDialog(c = null) {
    $('#condition-dialog-title').textContent = c ? 'Edit Condition' : 'Add Condition';
    $('#cond-edit-id').value = c ? c.id : '';
    $('#cond-name').value = c ? c.name : '';
    $('#cond-description').value = c ? c.description : '';
    $('#cond-rounds').value = c ? c.num_rounds : 2;
    $('#cond-da').checked = c ? !!c.enable_da : false;
    $('#cond-memory').checked = c ? !!c.enable_memory : false;
    $('#cond-tools').checked = c ? !!c.enable_tools : false;
    // Participants
    const container = $('#cond-participants');
    container.innerHTML = '';
    const participants = c ? c.participants : [{ name: '', system_prompt: '', role: 'standard' }];
    participants.forEach(p => addParticipantRow(p));
    show('#condition-dialog');
    $('#cond-name').focus();
}

function addParticipantRow(p = { name: '', system_prompt: '', role: 'standard' }) {
    const container = $('#cond-participants');
    const row = document.createElement('div');
    row.className = 'participant-row';
    row.innerHTML = `
        <div class="participant-fields">
            <div class="form-row">
                <input type="text" class="p-name" value="${esc(p.name)}" placeholder="Name">
                <select class="p-role">
                    <option value="standard" ${p.role === 'standard' ? 'selected' : ''}>Standard</option>
                    <option value="devils_advocate" ${p.role === 'devils_advocate' ? 'selected' : ''}>Devil's Advocate</option>
                </select>
            </div>
            <textarea class="p-prompt" rows="2" placeholder="System prompt...">${esc(p.system_prompt)}</textarea>
        </div>
        <button class="btn btn-sm btn-ghost" data-action="remove-participant">&times;</button>
    `;
    container.appendChild(row);
}

function getParticipantsFromDialog() {
    const rows = $$('#cond-participants .participant-row');
    return Array.from(rows).map(row => ({
        name: row.querySelector('.p-name').value.trim(),
        system_prompt: row.querySelector('.p-prompt').value.trim(),
        role: row.querySelector('.p-role').value,
    })).filter(p => p.name);
}

async function confirmCondition() {
    const data = {
        name: $('#cond-name').value.trim(),
        description: $('#cond-description').value.trim(),
        num_rounds: parseInt($('#cond-rounds').value) || 2,
        enable_da: $('#cond-da').checked,
        enable_memory: $('#cond-memory').checked,
        enable_tools: $('#cond-tools').checked,
        participants: getParticipantsFromDialog(),
    };
    if (!data.name) return showToast('Name is required');
    if (!data.participants.length) return showToast('At least one participant is required');
    try {
        const editId = $('#cond-edit-id').value;
        if (editId) await API.updateCondition(editId, data);
        else await API.addCondition(data);
        hide('#condition-dialog');
        await loadConditions();
        renderConditions();
    } catch (e) { showToast(e.message); }
}

async function deleteCondition(id) {
    if (!confirm('Delete this condition?')) return;
    try {
        await API.deleteCondition(id);
        await loadConditions();
        renderConditions();
    } catch (e) { showToast(e.message); }
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

function renderDashboard() {
    // Restore BYOK from sessionStorage
    const saved = sessionStorage.getItem('eval_api_key');
    if (saved) $('#dash-api-key').value = saved;

    // Cases checklist
    const casesEl = $('#dash-cases-checklist');
    casesEl.innerHTML = state.cases.map(c => `
        <label class="checklist-item">
            <input type="checkbox" value="${c.id}" checked>
            <span class="badge badge-${c.difficulty}">${c.difficulty[0].toUpperCase()}</span>
            ${esc(c.case_key)}: ${esc(c.title)}
        </label>
    `).join('');

    // Conditions checklist
    const condsEl = $('#dash-conditions-checklist');
    condsEl.innerHTML = state.conditions.map(c => `
        <label class="checklist-item">
            <input type="checkbox" value="${c.id}" checked>
            ${esc(c.name)} (${c.participants.length}p, ${c.num_rounds}r)
        </label>
    `).join('');
}

function getSelectedIds(containerSel) {
    return Array.from($$(containerSel + ' input[type="checkbox"]:checked'))
        .map(cb => parseInt(cb.value));
}

async function runEvaluation() {
    const providerUrl = $('#dash-provider-url').value.trim();
    const model = $('#dash-model').value.trim();
    const apiKey = $('#dash-api-key').value.trim();
    const caseIds = getSelectedIds('#dash-cases-checklist');
    const conditionIds = getSelectedIds('#dash-conditions-checklist');

    if (!providerUrl || !model) return showToast('Provider URL and model are required');
    if (!caseIds.length) return showToast('Select at least one case');
    if (!conditionIds.length) return showToast('Select at least one condition');

    // Save API key to sessionStorage (BYOK pattern)
    if (apiKey) sessionStorage.setItem('eval_api_key', apiKey);
    else sessionStorage.removeItem('eval_api_key');

    try {
        const batch = await API.startBatch({
            case_ids: caseIds,
            condition_ids: conditionIds,
            provider_url: providerUrl,
            model: model,
            api_key: apiKey,
        });
        activeBatchId = batch.id;
        showToast(`Batch #${batch.id} started: ${batch.total_runs} runs`);
        startPolling(batch.id);
    } catch (e) { showToast(e.message); }
}

function startPolling(batchId) {
    show('#dash-progress');
    hide('#run-eval-btn');
    show('#cancel-eval-btn');

    pollTimer = setInterval(async () => {
        try {
            const batch = await API.getBatch(batchId);
            updateProgress(batch);
            if (['done', 'error', 'cancelled'].includes(batch.status)) {
                stopPolling();
                showToast(`Batch ${batch.status}`);
            }
        } catch (e) {
            stopPolling();
            showToast(e.message);
        }
    }, 2000);
}

function stopPolling() {
    clearInterval(pollTimer);
    pollTimer = null;
    activeBatchId = null;
    show('#run-eval-btn');
    hide('#cancel-eval-btn');
}

function updateProgress(batch) {
    const counts = batch.run_counts || {};
    const done = (counts.done || 0) + (counts.error || 0);
    const total = batch.total_runs || 1;
    const pct = Math.round(100 * done / total);

    $('#dash-progress-fill').style.width = pct + '%';
    $('#dash-progress-text').textContent =
        `${done}/${total} runs complete` +
        (counts.error ? ` (${counts.error} errors)` : '') +
        ` — ${batch.status}`;
    $('#dash-status').innerHTML =
        `<span class="status-indicator ${batch.status}"></span>${batch.status}`;
}

async function cancelEvaluation() {
    if (!activeBatchId) return;
    try {
        await API.cancelBatch(activeBatchId);
        stopPolling();
        showToast('Batch cancelled');
    } catch (e) { showToast(e.message); }
}

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

async function loadBatches() {
    try { state.batches = await API.listBatches(); } catch (e) { showToast(e.message); }
}

async function renderResults() {
    await loadBatches();

    const sel = $('#results-batch-select');
    sel.innerHTML = state.batches.length
        ? state.batches.map(b => `<option value="${b.id}">${esc(b.name)} (${b.model}) — ${b.status}</option>`).join('')
        : '<option value="">No batches yet</option>';

    if (state.batches.length) {
        await renderBatchResults(state.batches[0].id);
    } else {
        $('#results-summary').innerHTML = '<div class="empty-state">Run an evaluation from the Dashboard tab to see results here.</div>';
        $('#results-detail').innerHTML = '';
    }
}

async function renderBatchResults(batchId) {
    try {
        const batch = await API.getBatch(batchId);
        const runs = batch.runs || [];

        // Summary table: group by condition
        const byCondition = {};
        runs.forEach(r => {
            const cn = r.condition_name;
            if (!byCondition[cn]) byCondition[cn] = [];
            byCondition[cn].push(r);
        });

        let summaryHTML = `<table class="results-table">
            <thead><tr>
                <th>Condition</th><th>N</th><th>Done</th><th>Errors</th><th>Tokens</th>
            </tr></thead><tbody>`;

        for (const [cond, cRuns] of Object.entries(byCondition)) {
            const done = cRuns.filter(r => r.status === 'done').length;
            const errors = cRuns.filter(r => r.status === 'error').length;
            const tokens = cRuns.reduce((s, r) => s + (r.total_tokens || 0), 0);
            summaryHTML += `<tr>
                <td>${esc(cond)}</td>
                <td>${cRuns.length}</td>
                <td>${done}</td>
                <td>${errors || '—'}</td>
                <td>${tokens.toLocaleString()}</td>
            </tr>`;
        }
        summaryHTML += '</tbody></table>';
        $('#results-summary').innerHTML = summaryHTML;

        // Per-run detail
        let detailHTML = '<h3>Per-Run Breakdown</h3>';
        runs.forEach(r => {
            const statusBadge = `<span class="badge badge-${r.status === 'done' ? 'success' : r.status === 'error' ? 'error' : 'pending'}">${r.status}</span>`;
            detailHTML += `
            <div class="settings-item">
                <div class="item-info">
                    <div class="item-title">${esc(r.case_key || '')} / ${esc(r.condition_name || '')}</div>
                    <div class="item-meta">
                        ${statusBadge}
                        ${r.total_tokens ? r.total_tokens.toLocaleString() + ' tokens' : ''}
                        ${r.error_text ? ' — ' + esc(r.error_text.substring(0, 80)) : ''}
                    </div>
                </div>
                <div class="item-actions">
                    <button class="btn btn-sm btn-ghost" data-action="view-run" data-id="${r.id}">View</button>
                </div>
            </div>`;
        });
        $('#results-detail').innerHTML = detailHTML;

    } catch (e) { showToast(e.message); }
}

async function viewRun(runId) {
    try {
        const run = await API.getRun(runId);
        const msgs = run.messages || [];
        const scores = run.scores || [];

        let html = `
            <div class="text-muted" style="margin-bottom: 0.75rem;">
                ${esc(run.case_key || '')} / ${esc(run.condition_name || '')}
                &mdash; ${run.total_tokens ? run.total_tokens.toLocaleString() + ' tokens' : ''}
            </div>`;

        if (msgs.length) {
            html += '<h4 style="margin: 0.75rem 0 0.5rem;">Messages</h4>';
            msgs.forEach(m => {
                html += `<div class="message" style="border-left-color: ${m.role === 'devils_advocate' ? 'var(--accent)' : 'var(--primary)'}">
                    <div class="speaker">${esc(m.speaker)} <span class="text-muted">(${esc(m.role)})</span></div>
                    <div>${esc(m.content).substring(0, 500)}${m.content.length > 500 ? '...' : ''}</div>
                </div>`;
            });
        }

        if (run.conclusion) {
            html += `<div class="conclusion"><strong>Conclusion:</strong><br>${esc(run.conclusion).substring(0, 1000)}</div>`;
        }

        if (scores.length) {
            html += '<h4 style="margin: 0.75rem 0 0.5rem;">Scores</h4>';
            scores.forEach(s => {
                const data = s.score_data || {};
                html += `<div class="text-muted">${esc(s.score_type)}: <code>${esc(JSON.stringify(data))}</code></div>`;
            });
        }

        if (run.error_text) {
            html += `<div style="color: var(--error); margin-top: 0.75rem;">Error: ${esc(run.error_text)}</div>`;
        }

        $('#run-dialog-title').textContent = `Run #${run.id}`;
        $('#run-dialog-content').innerHTML = html;
        show('#run-dialog');
    } catch (e) { showToast(e.message); }
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

function esc(s) {
    if (s == null) return '';
    const div = document.createElement('div');
    div.textContent = String(s);
    return div.innerHTML;
}

// ---------------------------------------------------------------------------
// Event delegation
// ---------------------------------------------------------------------------

document.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    const action = btn.dataset.action;
    const id = btn.dataset.id ? parseInt(btn.dataset.id) : null;

    switch (action) {
        // Tabs
        case 'switch-tab': switchTab(btn.dataset.tab); break;

        // Cases
        case 'add-case': openCaseDialog(); break;
        case 'edit-case': {
            const c = state.cases.find(x => x.id === id);
            if (c) openCaseDialog(c);
            break;
        }
        case 'delete-case': deleteCase(id); break;
        case 'confirm-case': confirmCase(); break;
        case 'cancel-case-dialog': hide('#case-dialog'); break;

        // Conditions
        case 'add-condition': openConditionDialog(); break;
        case 'edit-condition': {
            const c = state.conditions.find(x => x.id === id);
            if (c) openConditionDialog(c);
            break;
        }
        case 'delete-condition': deleteCondition(id); break;
        case 'confirm-condition': confirmCondition(); break;
        case 'cancel-condition-dialog': hide('#condition-dialog'); break;
        case 'add-participant': addParticipantRow(); break;
        case 'remove-participant': {
            const row = btn.closest('.participant-row');
            if (row) row.remove();
            break;
        }

        // Dashboard
        case 'select-all-cases':
            $$('#dash-cases-checklist input').forEach(cb => cb.checked = true); break;
        case 'select-no-cases':
            $$('#dash-cases-checklist input').forEach(cb => cb.checked = false); break;
        case 'select-all-conditions':
            $$('#dash-conditions-checklist input').forEach(cb => cb.checked = true); break;
        case 'select-no-conditions':
            $$('#dash-conditions-checklist input').forEach(cb => cb.checked = false); break;
        case 'run-evaluation': runEvaluation(); break;
        case 'cancel-evaluation': cancelEvaluation(); break;

        // Results
        case 'view-run': viewRun(id); break;
        case 'close-run-dialog': hide('#run-dialog'); break;
    }
});

// Tab clicks
$$('.tab').forEach(t => t.addEventListener('click', () => switchTab(t.dataset.tab)));

// Batch selector change
$('#results-batch-select').addEventListener('change', (e) => {
    if (e.target.value) renderBatchResults(parseInt(e.target.value));
});

// Close dialogs on overlay click
$$('.dialog-overlay').forEach(overlay => {
    overlay.addEventListener('click', (e) => {
        if (e.target === overlay) hide(overlay);
    });
});

// Enter key in dialogs
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        $$('.dialog-overlay:not(.hidden)').forEach(o => hide(o));
    }
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

async function init() {
    await Promise.all([loadCases(), loadConditions()]);
    renderCases();
}

init();
