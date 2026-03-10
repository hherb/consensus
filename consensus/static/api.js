/**
 * @module api
 * API adapters for Desktop (pywebview bridge) and Web (fetch) modes.
 */

import { getByokKeys } from './byok.js';
import { showToast } from './utils.js';
import { onStateUpdate } from './state.js';

/** @type {DesktopAPI|WebAPI|null} The active API instance */
export let api = null;

/**
 * Initialize the API adapter based on runtime environment.
 * @param {object} authCallbacks - Auth callback functions
 * @param {Function} authCallbacks.showAuthPhase - Show auth login screen
 * @param {Function} authCallbacks.getAuthRequired - Get whether auth is required
 * @param {Function} authCallbacks.setAuthUser - Set auth user to null
 */
export function initApi(authCallbacks) {
    api = window.pywebview ? new DesktopAPI() : new WebAPI(authCallbacks);
}

/**
 * Desktop API adapter — delegates to pywebview bridge methods.
 */
class DesktopAPI {
    /** @returns {Promise<object>} Current app state */
    async getState() { return await window.pywebview.api.get_state(); }

    // --- Providers ---
    async addProvider(n, u, ke, k) { return await window.pywebview.api.add_provider(n, u, ke || '', k || ''); }
    async updateProvider(id, n, u, ke, k) { return await window.pywebview.api.update_provider(id, n, u, ke, k || ''); }
    async deleteProvider(id) { return await window.pywebview.api.delete_provider(id); }
    async fetchModels(providerId) { return await window.pywebview.api.fetch_models(providerId); }

    // --- Entity profiles ---
    async saveEntity(p) { return await window.pywebview.api.save_entity(p.name, p.entity_type, p.avatar_color||'#3b82f6', p.provider_id||'', p.model||'', p.temperature ?? 0.7, p.max_tokens ?? 1024, p.system_prompt||'', p.entity_id||''); }
    async deleteEntity(id) { return await window.pywebview.api.delete_entity(id); }
    async reactivateEntity(id) { return await window.pywebview.api.reactivate_entity(id); }
    async getInactiveEntities() { return await window.pywebview.api.get_inactive_entities(); }

    // --- Prompts ---
    async savePrompt(p) { return await window.pywebview.api.save_prompt(p.prompt_id||'', p.name, p.role, p.target, p.task, p.content); }
    async deletePrompt(id) { return await window.pywebview.api.delete_prompt(id); }

    // --- Discussion setup ---
    async addToDiscussion(eid, isMod, alsoPart, role='standard') { return await window.pywebview.api.add_to_discussion(eid, !!isMod, !!alsoPart, role); }
    async removeFromDiscussion(eid) { return await window.pywebview.api.remove_from_discussion(eid); }
    async setModerator(id, alsoPart) { return await window.pywebview.api.set_moderator(id, !!alsoPart); }
    async setParticipantRole(eid, role) { return await window.pywebview.api.set_participant_role(eid, role); }
    async setTopic(t) { return await window.pywebview.api.set_topic(t); }
    async listDiscussionMethods() { return await window.pywebview.api.list_discussion_methods(); }
    async setDiscussionMethod(name) { return await window.pywebview.api.set_discussion_method(name); }
    async startDiscussion(modPart, maxRounds=0) { return await window.pywebview.api.start_discussion(!!modPart, maxRounds); }

    // --- Discussion lifecycle ---
    async submitMessage(eid, content) { return await window.pywebview.api.submit_human_message(eid, content); }
    async submitModeratorMessage(content) { return await window.pywebview.api.submit_moderator_message(content); }
    async submitUserInput(requestId, content) { return await window.pywebview.api.submit_user_input(requestId, content); }
    async generateAiTurn() { return await window.pywebview.api.generate_ai_turn(); }
    async completeTurn(summary) { return await window.pywebview.api.complete_turn(summary || ''); }
    async reassignTurn(eid) { return await window.pywebview.api.reassign_turn(eid); }
    async mediate(ctx) { return await window.pywebview.api.mediate(ctx || ''); }
    async conclude() { return await window.pywebview.api.conclude(); }
    async pauseDiscussion() { return await window.pywebview.api.pause_discussion(); }
    async resumeDiscussion() { return await window.pywebview.api.resume_discussion(); }
    async reopenDiscussion() { return await window.pywebview.api.reopen_discussion(); }

    // --- History ---
    async loadDiscussion(id) { return await window.pywebview.api.load_discussion(id); }
    async deleteDiscussions(ids) { return await window.pywebview.api.delete_discussions(ids); }
    async restoreDiscussion(id) { return await window.pywebview.api.restore_discussion(id); }
    async reset() { return await window.pywebview.api.reset(); }

    // --- Tools ---
    async listTools() { return await window.pywebview.api.list_tools(); }
    async getEntityTools(eid) { return await window.pywebview.api.get_entity_tools(eid); }
    async assignTool(eid, toolName, mode) { return await window.pywebview.api.assign_tool(eid, toolName, mode || 'private'); }
    async removeTool(eid, toolName) { return await window.pywebview.api.remove_tool(eid, toolName); }

    // --- Documents ---
    async uploadDocument(file, discussionId) { return await window.pywebview.api.upload_document(discussionId || 0); }
    async addDocumentFromUrl(url, discussionId, title) { return await window.pywebview.api.add_document_from_url(url, discussionId || 0, title || ''); }
    async getDiscussionDocuments(discussionId) { return await window.pywebview.api.get_discussion_documents(discussionId || 0); }
    async removeDocument(docId, discussionId) { return await window.pywebview.api.remove_document(docId, discussionId || 0); }
    async deleteDocument(docId) { return await window.pywebview.api.delete_document(docId); }

    // --- Images ---
    async uploadImage(file, discussionId) { return await window.pywebview.api.upload_image(discussionId || 0); }
    async addImageFromUrl(url, discussionId, title) { return await window.pywebview.api.add_image_from_url(url, discussionId || 0, title || ''); }
    async getDiscussionImages(discussionId) { return await window.pywebview.api.get_discussion_images(discussionId || 0); }
    async removeImage(imageId, discussionId) { return await window.pywebview.api.remove_image(imageId, discussionId || 0); }
    async deleteImage(imageId) { return await window.pywebview.api.delete_image(imageId); }
    getImageUrl(imageId) { return `/api/images/file/${imageId}`; }

    // --- Memory ---
    async getMemoryConfig() { return await window.pywebview.api.get_memory_config(); }
    async saveMemoryConfig(data) { return await window.pywebview.api.save_memory_config(data); }
    async testMemoryConnection() { return await window.pywebview.api.test_memory_connection(); }

    // --- MCP Servers ---
    async getMcpServers() { return await window.pywebview.api.get_mcp_servers(); }
    async addMcpServer(name, description, command, args, env, transport, url, headers) {
        return await window.pywebview.api.add_mcp_server(name, description, command, args, env, transport, url, headers);
    }
    async updateMcpServer(serverId, updates) {
        return await window.pywebview.api.update_mcp_server(serverId, updates);
    }
    async deleteMcpServer(serverId) { return await window.pywebview.api.delete_mcp_server(serverId); }
    async testMcpConnection(serverId) { return await window.pywebview.api.test_mcp_connection(serverId); }

    // --- Experts ---
    async saveExpertDefinition(entityId, mcpServerId, toolName, description, defaultArgs, timeout) {
        return await window.pywebview.api.save_expert_definition(entityId, mcpServerId, toolName, description, defaultArgs, timeout);
    }
    async getExpertDefinitions() { return await window.pywebview.api.get_expert_definitions(); }
    async consultExpert(expertName, query) {
        return await window.pywebview.api.consult_expert(expertName, query);
    }
}

/**
 * Web API adapter — communicates via fetch to aiohttp backend.
 */
class WebAPI {
    /**
     * @param {object} authCallbacks
     * @param {Function} authCallbacks.showAuthPhase
     * @param {Function} authCallbacks.getAuthRequired
     * @param {Function} authCallbacks.setAuthUser
     */
    constructor(authCallbacks) {
        this._authCallbacks = authCallbacks;
    }

    /**
     * POST to /api/{method} with JSON body, handling BYOK keys and auth.
     * @param {string} method - API method name
     * @param {object} [data={}] - Request body
     * @returns {Promise<*>} Result from the API
     */
    async _post(method, data = {}) {
        const headers = { 'Content-Type': 'application/json' };
        const byokKeys = getByokKeys();
        if (Object.keys(byokKeys).length > 0) {
            headers['X-API-Keys'] = JSON.stringify(byokKeys);
        }
        const resp = await fetch(`/api/${method}`, {
            method: 'POST',
            headers,
            body: JSON.stringify(data),
        });
        const json = await resp.json();
        if (!resp.ok) {
            if (resp.status === 401 && this._authCallbacks.getAuthRequired()) {
                this._authCallbacks.setAuthUser(null);
                this._authCallbacks.showAuthPhase();
                return { error: 'Session expired' };
            }
            const errMsg = json.error || `Server error (${resp.status})`;
            showToast(errMsg);
            return { error: errMsg };
        }
        if (json.state) onStateUpdate(json.state);
        return json.result;
    }

    async getState() { return await this._post('get_state'); }
    async addProvider(n, u, ke, k) { return await this._post('add_provider', { name: n, base_url: u, api_key_env: ke || '', api_key: k || '' }); }
    async updateProvider(id, n, u, ke, k) { return await this._post('update_provider', { provider_id: id, name: n, base_url: u, api_key_env: ke, api_key: k || '' }); }
    async deleteProvider(id) { return await this._post('delete_provider', { provider_id: id }); }
    async fetchModels(providerId) { return await this._post('fetch_models', { provider_id: providerId }); }
    async saveEntity(p) { return await this._post('save_entity', p); }
    async deleteEntity(id) { return await this._post('delete_entity', { entity_id: id }); }
    async reactivateEntity(id) { return await this._post('reactivate_entity', { entity_id: id }); }
    async getInactiveEntities() { return await this._post('get_inactive_entities'); }
    async savePrompt(p) { return await this._post('save_prompt', p); }
    async deletePrompt(id) { return await this._post('delete_prompt', { prompt_id: id }); }
    async addToDiscussion(eid, isMod, alsoPart, role='standard') { return await this._post('add_to_discussion', { entity_id: eid, is_moderator: !!isMod, also_participant: !!alsoPart, participant_role: role }); }
    async removeFromDiscussion(eid) { return await this._post('remove_from_discussion', { entity_id: eid }); }
    async setModerator(id, alsoPart) { return await this._post('set_moderator', { entity_id: id, also_participant: !!alsoPart }); }
    async setParticipantRole(eid, role) { return await this._post('set_participant_role', { entity_id: eid, participant_role: role }); }
    async setTopic(t) { return await this._post('set_topic', { topic: t }); }
    async listDiscussionMethods() { return await this._post('list_discussion_methods'); }
    async setDiscussionMethod(name) { return await this._post('set_discussion_method', { method_name: name }); }
    async startDiscussion(modPart, maxRounds=0) { return await this._post('start_discussion', { moderator_participates: !!modPart, max_rounds: maxRounds }); }
    async submitMessage(eid, content) { return await this._post('submit_human_message', { entity_id: eid, content }); }
    async submitModeratorMessage(content) { return await this._post('submit_moderator_message', { content }); }
    async submitUserInput(requestId, content) { return await this._post('submit_user_input', { request_id: requestId, content }); }
    async generateAiTurn() { return await this._post('generate_ai_turn'); }
    async completeTurn(summary) { return await this._post('complete_turn', { moderator_summary: summary || '' }); }
    async reassignTurn(eid) { return await this._post('reassign_turn', { entity_id: eid }); }
    async mediate(ctx) { return await this._post('mediate', { context: ctx || '' }); }
    async conclude() { return await this._post('conclude'); }
    async pauseDiscussion() { return await this._post('pause_discussion'); }
    async resumeDiscussion() { return await this._post('resume_discussion'); }
    async reopenDiscussion() { return await this._post('reopen_discussion'); }
    async loadDiscussion(id) { return await this._post('load_discussion', { discussion_id: id }); }
    async deleteDiscussions(ids) { return await this._post('delete_discussions', { discussion_ids: ids }); }
    async restoreDiscussion(id) { return await this._post('restore_discussion', { discussion_id: id }); }
    async reset() { return await this._post('reset'); }
    async listTools() { return await this._post('list_tools'); }
    async getEntityTools(eid) { return await this._post('get_entity_tools', { entity_id: eid }); }
    async assignTool(eid, toolName, mode) { return await this._post('assign_tool', { entity_id: eid, tool_name: toolName, access_mode: mode || 'private' }); }
    async removeTool(eid, toolName) { return await this._post('remove_tool', { entity_id: eid, tool_name: toolName }); }

    // --- Documents ---
    async uploadDocument(file, discussionId) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('discussion_id', String(discussionId || 0));
        const resp = await fetch('/api/documents/upload', { method: 'POST', body: formData });
        if (!resp.ok) { const d = await resp.json().catch(() => ({})); return { error: d.error || `Upload failed (${resp.status})` }; }
        return await resp.json();
    }
    async addDocumentFromUrl(url, discussionId, title) {
        const resp = await fetch('/api/documents/add-url', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, discussion_id: discussionId || 0, title: title || '' }),
        });
        if (!resp.ok) { const d = await resp.json().catch(() => ({})); return { error: d.error || `Failed (${resp.status})` }; }
        return await resp.json();
    }
    async getDiscussionDocuments(discussionId) {
        const resp = await fetch(`/api/documents/${discussionId || 0}`);
        if (!resp.ok) return { documents: [] };
        const data = await resp.json();
        return data.documents || [];
    }
    async removeDocument(docId, discussionId) {
        const resp = await fetch(`/api/documents/${docId}/${discussionId}`, { method: 'DELETE' });
        if (!resp.ok) { const d = await resp.json().catch(() => ({})); return { error: d.error || `Failed (${resp.status})` }; }
        return await resp.json();
    }
    async deleteDocument(docId) {
        const resp = await fetch(`/api/documents/${docId}`, { method: 'DELETE' });
        if (!resp.ok) { const d = await resp.json().catch(() => ({})); return { error: d.error || `Failed (${resp.status})` }; }
        return await resp.json();
    }

    // --- Images ---
    async uploadImage(file, discussionId) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('discussion_id', String(discussionId || 0));
        const resp = await fetch('/api/images/upload', { method: 'POST', body: formData });
        if (!resp.ok) { const d = await resp.json().catch(() => ({})); return { error: d.error || `Upload failed (${resp.status})` }; }
        return await resp.json();
    }
    async addImageFromUrl(url, discussionId, title) {
        const resp = await fetch('/api/images/add-url', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, discussion_id: discussionId || 0, title: title || '' }),
        });
        if (!resp.ok) { const d = await resp.json().catch(() => ({})); return { error: d.error || `Failed (${resp.status})` }; }
        return await resp.json();
    }
    async getDiscussionImages(discussionId) {
        const resp = await fetch(`/api/images/${discussionId || 0}`);
        if (!resp.ok) return { images: [] };
        const data = await resp.json();
        return data.images || [];
    }
    async removeImage(imageId, discussionId) {
        const resp = await fetch(`/api/images/${imageId}/${discussionId}`, { method: 'DELETE' });
        if (!resp.ok) { const d = await resp.json().catch(() => ({})); return { error: d.error || `Failed (${resp.status})` }; }
        return await resp.json();
    }
    async deleteImage(imageId) {
        const resp = await fetch(`/api/images/${imageId}`, { method: 'DELETE' });
        if (!resp.ok) { const d = await resp.json().catch(() => ({})); return { error: d.error || `Failed (${resp.status})` }; }
        return await resp.json();
    }
    getImageUrl(imageId) { return `/api/images/file/${imageId}`; }

    async getMemoryConfig() {
        const resp = await fetch('/api/memory/config');
        if (!resp.ok) { const d = await resp.json().catch(() => ({})); return { error: d.error || `Server error (${resp.status})` }; }
        return await resp.json();
    }
    async saveMemoryConfig(data) {
        const resp = await fetch('/api/memory/config', {
            method: 'PUT', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        if (!resp.ok) { const d = await resp.json().catch(() => ({})); return { error: d.error || `Server error (${resp.status})` }; }
        return await resp.json();
    }
    async testMemoryConnection() {
        const resp = await fetch('/api/memory/test', { method: 'POST' });
        return await resp.json();
    }

    async getMcpServers() { return await this._post('get_mcp_servers'); }
    async addMcpServer(name, description, command, args, env, transport, url, headers) {
        return await this._post('add_mcp_server', { name, description, command, args, env, transport, url, headers });
    }
    async updateMcpServer(serverId, updates) {
        return await this._post('update_mcp_server', { server_id: serverId, ...updates });
    }
    async deleteMcpServer(serverId) { return await this._post('delete_mcp_server', { server_id: serverId }); }
    async testMcpConnection(serverId) { return await this._post('test_mcp_connection', { server_id: serverId }); }

    async saveExpertDefinition(entityId, mcpServerId, toolName, description, defaultArgs, timeout) {
        return await this._post('save_expert_definition', {
            entity_id: entityId, mcp_server_id: mcpServerId, tool_name: toolName,
            description, default_arguments: defaultArgs, timeout_seconds: timeout
        });
    }
    async getExpertDefinitions() { return await this._post('get_expert_definitions'); }
    async consultExpert(expertName, query) {
        return await this._post('consult_expert', { expert_name: expertName, query });
    }
}
