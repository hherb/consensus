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
    container.innerHTML = servers.map(s => `
        <div class="settings-item">
            <div class="entity-info">
                <div class="entity-name">
                    ${escHtml(s.name)}
                    <span class="badge ${s.enabled ? 'active' : ''}" style="font-size:0.65rem;margin-left:0.3rem">
                        ${s.enabled ? 'Enabled' : 'Disabled'}
                    </span>
                </div>
                <div class="settings-detail">${escHtml(s.description || '')}</div>
                <div class="settings-detail" style="font-family:var(--font-mono);font-size:0.75rem">${escHtml(s.command || '')}</div>
            </div>
            <div class="entity-actions">
                <button class="btn btn-outline btn-sm" data-action="test-mcp" data-id="${s.id}">Test</button>
                <button class="btn btn-outline btn-sm" data-action="toggle-mcp" data-id="${s.id}">${s.enabled ? 'Disable' : 'Enable'}</button>
                <button class="btn btn-ghost btn-sm" data-action="edit-mcp" data-id="${s.id}">Edit</button>
                <button class="btn btn-ghost btn-sm" data-action="delete-mcp" data-id="${s.id}">Delete</button>
            </div>
        </div>
    `).join('');
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
    $('#mcp-edit-id').value = server?.id || '';
    show('#mcp-server-dialog');
    $('#mcp-name').focus();
}

/**
 * Confirm and save MCP server from dialog form.
 */
export async function confirmMcpServer() {
    const name = $('#mcp-name').value.trim();
    const description = $('#mcp-description').value.trim();
    const command = $('#mcp-command').value.trim();
    if (!name || !command) return showToast('Name and command are required');

    const argsStr = $('#mcp-args').value.trim();
    const args = argsStr ? argsStr.split(',').map(a => a.trim()).filter(Boolean) : [];

    const envStr = $('#mcp-env').value.trim();
    const env = {};
    if (envStr) {
        for (const line of envStr.split('\n')) {
            const eq = line.indexOf('=');
            if (eq > 0) {
                env[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
            }
        }
    }

    const editId = $('#mcp-edit-id').value;
    if (editId) {
        await api.updateMcpServer(Number(editId), { name, description, command, args, env });
    } else {
        await api.addMcpServer(name, description, command, args, env);
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
