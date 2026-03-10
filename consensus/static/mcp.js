/**
 * @module mcp
 * MCP (Model Context Protocol) server management UI.
 */

import { $, show, hide, showToast, escHtml } from './utils.js';
import { state, onStateUpdate } from './state.js';
import { api } from './api.js';

/**
 * Render the MCP servers list in the Providers tab.
 */
export function renderMcpServers() {
    const container = $('#mcp-server-list');
    if (!container) return;
    const servers = state.mcp_servers || [];
    if (!servers.length) {
        container.innerHTML = '<div class="empty-state">No MCP servers configured</div>';
        return;
    }
    container.innerHTML = servers.map(s => {
        const transport = s.transport || 'stdio';
        const transportBadge = transport === 'http'
            ? '<span class="badge" style="font-size:0.6rem;margin-left:0.3rem;background:var(--accent)">HTTP</span>'
            : '<span class="badge" style="font-size:0.6rem;margin-left:0.3rem">stdio</span>';
        const detail = transport === 'http'
            ? escHtml(s.url || '')
            : escHtml(s.command || '');
        return `
        <div class="settings-item">
            <div class="entity-info">
                <div class="entity-name">
                    ${escHtml(s.name)}
                    ${transportBadge}
                    <span class="badge ${s.enabled ? 'active' : ''}" style="font-size:0.65rem;margin-left:0.3rem">
                        ${s.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                </div>
                <div class="settings-detail">${escHtml(s.description || '')}</div>
                <div class="settings-detail" style="font-family:var(--font-mono);font-size:0.75rem">${detail}</div>
            </div>
            <div class="entity-actions">
                <button class="btn btn-outline btn-sm" data-action="test-mcp" data-id="${s.id}">Test</button>
                <button class="btn btn-outline btn-sm" data-action="toggle-mcp" data-id="${s.id}">${s.enabled ? 'Disable' : 'Enable'}</button>
                <button class="btn btn-ghost btn-sm" data-action="edit-mcp" data-id="${s.id}">Edit</button>
                <button class="btn btn-ghost btn-sm" data-action="delete-mcp" data-id="${s.id}">Delete</button>
            </div>
        </div>`;
    }).join('');
}

/**
 * Set up the transport radio toggle to show/hide stdio vs HTTP fields.
 * Call once after DOM is ready.
 */
export function initMcpTransportToggle() {
    const radios = document.querySelectorAll('input[name="mcp-transport"]');
    for (const radio of radios) {
        radio.addEventListener('change', () => _applyTransportVisibility(radio.value));
    }
}

/**
 * Show/hide the correct field groups based on transport type.
 * @param {string} transport - 'stdio' or 'http'
 */
function _applyTransportVisibility(transport) {
    const stdioFields = $('#mcp-stdio-fields');
    const httpFields = $('#mcp-http-fields');
    if (!stdioFields || !httpFields) return;

    if (transport === 'http') {
        hide(stdioFields);
        show(httpFields);
    } else {
        show(stdioFields);
        hide(httpFields);
    }
}

/**
 * Open the MCP server add/edit dialog.
 * @param {object|null} server - Existing server to edit, or null for new
 */
export function openMcpServerDialog(server) {
    $('#mcp-dialog-title').textContent = server ? 'Edit MCP Server' : 'Add MCP Server';
    $('#mcp-name').value = server?.name || '';
    $('#mcp-description').value = server?.description || '';
    $('#mcp-command').value = server?.command || '';
    $('#mcp-args').value = (server?.args || []).join(', ');
    $('#mcp-env').value = server?.env ? Object.entries(server.env).map(([k, v]) => `${k}=${v}`).join('\n') : '';
    $('#mcp-url').value = server?.url || '';
    $('#mcp-headers').value = server?.headers ? Object.entries(server.headers).map(([k, v]) => `${k}=${v}`).join('\n') : '';
    $('#mcp-edit-id').value = server?.id || '';

    // Set transport radio and toggle field visibility
    const transport = server?.transport || 'stdio';
    const radio = document.querySelector(`input[name="mcp-transport"][value="${transport}"]`);
    if (radio) radio.checked = true;
    _applyTransportVisibility(transport);

    show('#mcp-server-dialog');
    $('#mcp-name').focus();
}

/**
 * Confirm and save MCP server from dialog form.
 */
export async function confirmMcpServer() {
    const name = $('#mcp-name').value.trim();
    const description = $('#mcp-description').value.trim();
    const transportRadio = document.querySelector('input[name="mcp-transport"]:checked');
    const transport = transportRadio ? transportRadio.value : 'stdio';

    if (!name) return showToast('Name is required');

    let command = '', args = [], env = {}, url = '', headers = {};

    if (transport === 'stdio') {
        command = $('#mcp-command').value.trim();
        if (!command) return showToast('Command is required for stdio transport');

        const argsStr = $('#mcp-args').value.trim();
        args = argsStr ? argsStr.split(',').map(a => a.trim()).filter(Boolean) : [];

        const envStr = $('#mcp-env').value.trim();
        if (envStr) {
            for (const line of envStr.split('\n')) {
                const eq = line.indexOf('=');
                if (eq > 0) env[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
            }
        }
    } else {
        url = $('#mcp-url').value.trim();
        if (!url) return showToast('URL is required for HTTP transport');

        const headersStr = $('#mcp-headers').value.trim();
        if (headersStr) {
            for (const line of headersStr.split('\n')) {
                const eq = line.indexOf('=');
                if (eq > 0) headers[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
            }
        }
    }

    const editId = $('#mcp-edit-id').value;
    if (editId) {
        await api.updateMcpServer(Number(editId), {
            name, description, command, args, env, transport, url, headers,
        });
    } else {
        await api.addMcpServer(name, description, command, args, env, transport, url, headers);
    }
    const s = await api.getState();
    onStateUpdate(s);
    hide('#mcp-server-dialog');
    renderMcpServers();
}

/**
 * Toggle enabled/disabled state of an MCP server.
 * @param {number} id - Server ID
 */
export async function toggleMcpServer(id) {
    const server = (state.mcp_servers || []).find(s => s.id === id);
    if (!server) return;
    await api.updateMcpServer(id, { enabled: !server.enabled });
    const s = await api.getState();
    onStateUpdate(s);
    renderMcpServers();
}

/**
 * Delete an MCP server.
 * @param {number} id - Server ID
 */
export async function deleteMcpServer(id) {
    await api.deleteMcpServer(id);
    const s = await api.getState();
    onStateUpdate(s);
    renderMcpServers();
}

/**
 * Test connectivity to an MCP server.
 * @param {number} id - Server ID
 */
export async function testMcpConnection(id) {
    showToast('Testing connection...', 3000, 'info');
    try {
        const result = await api.testMcpConnection(id);
        if (result && result.success) {
            const toolNames = (result.tools || []).map(t => t.name).join(', ');
            showToast(`Connected! Tools: ${toolNames || 'none'}`, 6000, 'success');
        } else {
            showToast(`Connection failed: ${result?.error || 'Unknown error'}`, 5000, 'error');
        }
    } catch (e) {
        showToast('Test failed: ' + e.message, 5000, 'error');
    }
}
