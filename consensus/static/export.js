/**
 * @module export
 * Discussion export — JSON, HTML, and PDF formats.
 */

import { $, $$, show, hide, showToast, escHtml, getInitials, formatTime, renderMarkdown, slugify, safeColor } from './utils.js';
import { state } from './state.js';

/**
 * Calculate the total cost of messages in a discussion.
 * @param {Array<object>} messages
 * @returns {number} Total cost
 */
export function calculateDiscussionCost(messages) {
    let total = 0;
    for (const m of messages) {
        if (m.cost != null) total += m.cost;
    }
    return total;
}

/**
 * Generate export filename with topic slug and date.
 * @param {string} ext - File extension
 * @param {object} [exportState] - State to export (defaults to current)
 * @returns {string}
 */
function exportFilename(ext, exportState) {
    const s = exportState || state;
    const topic = slugify(s.topic || 'discussion');
    const date = new Date().toISOString().slice(0, 10);
    return `consensus-${topic}-${date}.${ext}`;
}

/**
 * Build structured export data object from discussion state.
 * @param {object} [exportState] - State to export (defaults to current)
 * @returns {object} Export data
 */
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
                    cost: m.cost,
                };
            } else {
                msg.ai_metadata = null;
            }
            if (m.tool_calls && m.tool_calls.length > 0) {
                msg.tool_calls = m.tool_calls;
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

/**
 * Download content as a file (native save dialog in desktop, blob download in web).
 * @param {string} content - File content
 * @param {string} filename
 * @param {string} mimeType
 * @returns {Promise<boolean>}
 */
async function downloadFile(content, filename, mimeType) {
    if (window.pywebview) {
        const ext = filename.split('.').pop();
        const typeMap = { json: 'JSON files (*.json)', html: 'HTML files (*.html)', txt: 'Text files (*.txt)' };
        const saved = await window.pywebview.api.save_file(content, filename, typeMap[ext] || '');
        return saved;
    }
    const blob = new Blob([content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    return true;
}

/**
 * Export discussion as JSON file.
 * @param {object} [exportState]
 */
export async function exportAsJson(exportState) {
    const data = buildExportData(exportState);
    const json = JSON.stringify(data, null, 2);
    const saved = await downloadFile(json, exportFilename('json', exportState), 'application/json');
    if (saved) showToast('Exported as JSON', 2000, 'success');
}

/**
 * Build a complete, self-contained HTML document for export.
 * @param {object} [exportState]
 * @returns {string} Full HTML document
 */
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
            const costStr = m.cost != null ? ` | $${m.cost.toFixed(4)}` : '';
            metaHtml = `<span class="meta">${escHtml(m.model_used)} | ${m.total_tokens}tok | ${m.latency_ms}ms${costStr}</span>`;
        }
        const cls = isMod ? 'message moderator' : isSystem ? 'message system' : 'message';
        const borderStyle = !isMod && !isSystem ? `border-left-color:${color};` : '';
        let toolCallsHtml = '';
        if (m.tool_calls && m.tool_calls.length > 0) {
            toolCallsHtml = m.tool_calls.map(tc => {
                const argsStr = typeof tc.arguments === 'object' ? JSON.stringify(tc.arguments) : tc.arguments;
                const statusIcon = tc.is_error ? '&#x26A0;' : '&#x2705;';
                const statusClass = tc.is_error ? 'tool-error' : 'tool-success';
                return `<details class="tool-call ${statusClass}">
                    <summary>${statusIcon} <strong>${escHtml(tc.tool_name)}</strong>(${escHtml(argsStr)})${tc.latency_ms ? ` <span class="meta">${tc.latency_ms}ms</span>` : ''}</summary>
                    <pre class="tool-result">${escHtml(tc.result)}</pre>
                </details>`;
            }).join('');
        }

        return `<div class="${cls}" style="${borderStyle}">
            <div class="msg-header">
                <div class="avatar" style="background:${color};width:24px;height:24px;font-size:0.65rem">${escHtml(initials)}</div>
                <span class="sender" ${isMod ? '' : `style="color:${color}"`}>${escHtml(m.entity_name)}</span>
                ${metaHtml}
                <span class="time">${formatTime(m.timestamp)}</span>
            </div>
            ${toolCallsHtml}
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
.tool-call { margin: 0.4rem 0; border: 1px solid var(--border); border-radius: var(--radius); font-size: 0.8rem; }
.tool-call summary { padding: 0.35rem 0.6rem; cursor: pointer; font-size: 0.8rem; color: var(--text-secondary); }
.tool-call summary strong { color: var(--text); }
.tool-call.tool-error summary { color: #ef4444; }
.tool-call .tool-result { padding: 0.5rem 0.75rem; margin: 0; font-size: 0.75rem; background: var(--surface-elevated); border-top: 1px solid var(--border); white-space: pre-wrap; word-break: break-word; max-height: 300px; overflow-y: auto; }
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
    <div class="subtitle">Exported from Consensus on ${exportDate}${(() => { const tc = calculateDiscussionCost(s.messages); return tc > 0 ? ` | Total cost: $${tc.toFixed(2)}` : ''; })()}</div>
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

/**
 * Export discussion as HTML file.
 * @param {object} [exportState]
 */
export async function exportAsHtml(exportState) {
    const html = buildExportHtml(exportState);
    const saved = await downloadFile(html, exportFilename('html', exportState), 'text/html');
    if (saved) showToast('Exported as HTML', 2000, 'success');
}

/**
 * Export discussion as PDF via print dialog.
 * @param {object} [exportState]
 */
export function exportAsPdf(exportState) {
    const html = buildExportHtml(exportState);
    if (window.pywebview) {
        document.open();
        document.write(html);
        document.close();
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
        const style = document.createElement('style');
        style.textContent = '#pdf-toolbar { display: none !important; } body { padding-top: 0 !important; }';
        style.media = 'print';
        document.head.appendChild(style);
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

/**
 * Toggle visibility of the active discussion export menu.
 */
export function toggleExportMenu() {
    const menu = $('#export-menu');
    menu.classList.toggle('hidden');
}

/**
 * Close the active discussion export menu.
 */
export function closeExportMenu() {
    const menu = $('#export-menu');
    if (menu) menu.classList.add('hidden');
}

/**
 * Toggle a history-item export dropdown menu.
 * @param {number} discussionId
 */
export function toggleHistoryExportMenu(discussionId) {
    const menu = $(`#history-export-menu-${discussionId}`);
    if (!menu) return;
    const wasHidden = menu.classList.contains('hidden');
    closeAllHistoryMenus();
    if (wasHidden) menu.classList.remove('hidden');
}

/**
 * Close all history export dropdown menus.
 */
export function closeAllHistoryMenus() {
    $$('.history-export-menu').forEach(m => m.classList.add('hidden'));
}

/**
 * Export a discussion from history by loading its data first.
 * @param {number} discussionId
 * @param {string} format - 'json', 'html', or 'pdf'
 */
export async function exportHistoryDiscussion(discussionId, format) {
    try {
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
