/**
 * @module app
 * Application entrypoint — initialization, event wiring, and bootstrap.
 */

import { $, $$, show, hide } from './utils.js';
import { state, onStateUpdate, registerSetupCallback } from './state.js';
import { api, initApi } from './api.js';
import { checkAuthStatus, showAuthPhase, showAppPhase, authUser, authRequired, setAuthUser } from './auth.js';
import { renderProviders, openProviderDialog, confirmProvider, editProvider, removeProvider, promptByokKey, confirmByokKey, removeByokKey } from './providers.js';
import { renderProfiles, openEntityDialog, confirmEntity, editProfile, removeProfile, reactivateProfile, renderInactiveProfiles, loadModelsForProvider, selectColorSwatch, loadEntityTools } from './profiles.js';
import { renderPrompts, openPromptDialog, confirmPrompt, editPrompt, removePrompt } from './prompts.js';
import { renderHistory, deleteSelectedDiscussions, loadDiscussion } from './history.js';
import { renderSetupTab, renderAvailableEntities, updateStartButton, addToDiscussion, removeFromDiscussion, setModerator, setDevilsAdvocate, onMethodChange } from './setup.js';
import { onStartDiscussion, onSendMessage, onConfirmModeratorInput, onReassign, doReassign, onMediate, onConclude, onPause, onResume, onReopen, onBack, reopenFromHistory, onCostLimitContinue, onCostLimitConclude } from './discussion-actions.js';
import { exportAsJson, exportAsHtml, exportAsPdf, toggleExportMenu, closeExportMenu, toggleHistoryExportMenu, closeAllHistoryMenus, exportHistoryDiscussion } from './export.js';
import { openMcpServerDialog, confirmMcpServer, toggleMcpServer, deleteMcpServer, testMcpConnection, initMcpTransportToggle } from './mcp.js';
import { showConsultExpertDialog, onToolProgress } from './experts.js';
import { onUserInputRequest, checkPendingUserInput } from './ask-user.js';
import { loadMemoryConfig, saveMemoryConfig, testMemoryConnection } from './memory.js';
import { renderDocumentPanel, uploadDocument, addDocumentByUrl } from './documents.js';
import { renderImagePanel, uploadImage, addImageByUrl } from './images.js';

// Register the setup-tab callback so state.js can trigger it without circular imports
registerSetupCallback(renderSetupTab);

/**
 * Switch to a tab and render its content.
 * @param {string} tabName - Tab identifier
 */
function switchTab(tabName) {
    $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tabName));
    $$('.tab-content').forEach(tc => tc.classList.add('hidden'));
    const target = $(`#tab-${tabName}`);
    if (target) target.classList.remove('hidden');
    if (tabName === 'settings-providers') renderProviders();
    else if (tabName === 'settings-entities') { renderProfiles(); renderInactiveProfiles(); }
    else if (tabName === 'settings-prompts') renderPrompts();
    else if (tabName === 'history') renderHistory();
    else if (tabName === 'settings-memory') loadMemoryConfig();
    else if (tabName === 'new-discussion') { renderSetupTab(); renderDocumentPanel(); renderImagePanel(); }
}

/**
 * Attach all event listeners for the application UI.
 */
function init() {
    // Tab navigation
    $$('.tab').forEach(tab => {
        tab.addEventListener('click', () => switchTab(tab.dataset.tab));
    });

    // Evaluation link — in desktop mode, use bridge to launch eval server
    const evalLink = $('#eval-link');
    if (evalLink && window.pywebview) {
        evalLink.addEventListener('click', async (e) => {
            e.preventDefault();
            evalLink.textContent = 'Opening…';
            try {
                await window.pywebview.api.open_evaluation();
            } catch (err) {
                console.error('Failed to open evaluation:', err);
            }
            evalLink.textContent = 'Evaluation';
        });
    }

    // Provider dialog
    $('#add-provider-btn').addEventListener('click', () => openProviderDialog(null));
    $('#confirm-provider-btn').addEventListener('click', confirmProvider);
    $('#cancel-provider-btn').addEventListener('click', () => hide('#provider-dialog'));

    // BYOK key dialog
    $('#confirm-byok-btn').addEventListener('click', confirmByokKey);
    $('#cancel-byok-btn').addEventListener('click', () => hide('#byok-dialog'));
    $('#byok-remove-btn').addEventListener('click', removeByokKey);

    // Entity profile dialog
    $('#add-profile-btn').addEventListener('click', () => openEntityDialog(null));
    $('#quick-add-btn').addEventListener('click', () => openEntityDialog(null));
    $('#entity-type').addEventListener('change', (e) => {
        if (e.target.value === 'ai') {
            show('#ai-config');
            loadEntityTools($('#entity-edit-id').value || undefined);
        } else {
            hide('#ai-config');
        }
    });
    $('#ai-provider').addEventListener('change', (e) => {
        const pid = e.target.value;
        if (pid) loadModelsForProvider(parseInt(pid), '');
        else {
            $('#ai-model').innerHTML = '<option value="">-- Select a provider first --</option>';
            $('#ai-model-custom').value = '';
        }
    });
    $('#ai-model').addEventListener('change', () => {
        if ($('#ai-model').value) $('#ai-model-custom').value = '';
    });
    $('#color-swatches').addEventListener('click', (e) => {
        const swatch = e.target.closest('.color-swatch');
        if (!swatch) return;
        selectColorSwatch(swatch.dataset.color);
    });
    $('#entity-color-hex').addEventListener('input', (e) => {
        const v = e.target.value;
        if (/^#[0-9a-fA-F]{6}$/.test(v)) selectColorSwatch(v);
    });
    $('#confirm-entity-btn').addEventListener('click', confirmEntity);
    $('#cancel-entity-btn').addEventListener('click', () => hide('#entity-dialog'));

    // Prompt dialog
    $('#add-prompt-btn').addEventListener('click', () => openPromptDialog(null));
    $('#confirm-prompt-btn').addEventListener('click', confirmPrompt);
    $('#cancel-prompt-btn').addEventListener('click', () => hide('#prompt-dialog'));

    // Discussion setup
    $('#entity-search').addEventListener('input', () => renderAvailableEntities());
    $('#topic-input').addEventListener('input', updateStartButton);
    $('#discussion-method').addEventListener('change', onMethodChange);
    $('#start-btn').addEventListener('click', onStartDiscussion);

    // Discussion phase
    $('#send-btn').addEventListener('click', onSendMessage);
    $('#message-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSendMessage(); }
    });
    $('#reassign-btn').addEventListener('click', onReassign);
    $('#mediate-btn').addEventListener('click', onMediate);
    $('#pause-btn').addEventListener('click', onPause);
    $('#resume-btn').addEventListener('click', onResume);
    $('#reopen-btn').addEventListener('click', onReopen);
    $('#conclude-btn').addEventListener('click', onConclude);
    $('#cost-limit-continue-btn').addEventListener('click', onCostLimitContinue);
    $('#cost-limit-conclude-btn').addEventListener('click', onCostLimitConclude);
    $('#export-btn').addEventListener('click', () => toggleExportMenu());
    document.addEventListener('click', (ev) => {
        if (!ev.target.closest('.export-dropdown')) {
            closeExportMenu();
            closeAllHistoryMenus();
        }
    });
    $('#back-btn').addEventListener('click', onBack);

    // Moderator dialog
    $('#confirm-moderator-btn').addEventListener('click', onConfirmModeratorInput);
    $('#cancel-moderator-btn').addEventListener('click', () => hide('#moderator-dialog'));
    $('#moderator-input').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onConfirmModeratorInput(); }
    });

    // Reassign dialog
    $('#cancel-reassign-btn').addEventListener('click', () => hide('#reassign-dialog'));

    // Close dialogs on overlay click
    $$('.dialog-overlay').forEach(overlay => {
        overlay.addEventListener('click', (e) => { if (e.target === overlay) hide(overlay); });
    });

    // Enter key in dialogs
    $('#entity-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') confirmEntity(); });
    $('#prov-name').addEventListener('keydown', (e) => { if (e.key === 'Enter') confirmProvider(); });

    // Event delegation for dynamically rendered buttons
    document.addEventListener('click', (e) => {
        const target = e.target.closest('[data-action]');
        if (!target) return;
        const action = target.dataset.action;
        const id = target.dataset.id != null ? Number(target.dataset.id) : null;
        switch (action) {
            case 'edit-provider': editProvider(id); break;
            case 'delete-provider': removeProvider(id); break;
            case 'set-byok': promptByokKey(id); break;
            case 'edit-profile': editProfile(id); break;
            case 'delete-profile': removeProfile(id); break;
            case 'reactivate-profile': reactivateProfile(id); break;
            case 'edit-prompt': editPrompt(id); break;
            case 'delete-prompt': removePrompt(id); break;
            case 'load-discussion': loadDiscussion(id); break;
            case 'reopen-discussion': reopenFromHistory(id); break;
            case 'delete-selected': deleteSelectedDiscussions(); break;
            case 'add-to-discussion': addToDiscussion(id); break;
            case 'set-moderator': setModerator(id); break;
            case 'set-devils-advocate': setDevilsAdvocate(id); break;
            case 'remove-from-discussion': removeFromDiscussion(id); break;
            case 'do-reassign': doReassign(id); break;
            case 'export-json': closeExportMenu(); exportAsJson(); break;
            case 'export-html': closeExportMenu(); exportAsHtml(); break;
            case 'export-pdf': closeExportMenu(); exportAsPdf(); break;
            case 'toggle-history-export': toggleHistoryExportMenu(id); break;
            case 'export-history-json': closeAllHistoryMenus(); exportHistoryDiscussion(id, 'json'); break;
            case 'export-history-html': closeAllHistoryMenus(); exportHistoryDiscussion(id, 'html'); break;
            case 'export-history-pdf': closeAllHistoryMenus(); exportHistoryDiscussion(id, 'pdf'); break;
            case 'test-mcp': testMcpConnection(id); break;
            case 'toggle-mcp': toggleMcpServer(id); break;
            case 'edit-mcp': { const srv = (state.mcp_servers || []).find(s => s.id === id); if (srv) openMcpServerDialog(srv); break; }
            case 'delete-mcp': deleteMcpServer(id); break;
            case 'consult-expert-btn': showConsultExpertDialog(); break;
        }
    });

    // MCP Server dialog
    const addMcpBtn = $('#add-mcp-server-btn');
    if (addMcpBtn) addMcpBtn.addEventListener('click', () => openMcpServerDialog(null));
    const confirmMcpBtn = $('#confirm-mcp-btn');
    if (confirmMcpBtn) confirmMcpBtn.addEventListener('click', confirmMcpServer);
    const cancelMcpBtn = $('#cancel-mcp-btn');
    if (cancelMcpBtn) cancelMcpBtn.addEventListener('click', () => hide('#mcp-server-dialog'));
    initMcpTransportToggle();

    // Memory config
    const memorySaveBtn = $('#memory-save-btn');
    if (memorySaveBtn) memorySaveBtn.addEventListener('click', saveMemoryConfig);
    const memoryTestBtn = $('#memory-test-btn');
    if (memoryTestBtn) memoryTestBtn.addEventListener('click', testMemoryConnection);

    // Document management
    const docUploadBtn = $('#doc-upload-btn');
    if (docUploadBtn) docUploadBtn.addEventListener('click', uploadDocument);
    const docUrlAddBtn = $('#doc-url-add-btn');
    if (docUrlAddBtn) docUrlAddBtn.addEventListener('click', addDocumentByUrl);
    const docUrlInput = $('#doc-url-input');
    if (docUrlInput) docUrlInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addDocumentByUrl();
    });

    // Image management
    const imgUploadBtn = $('#img-upload-btn');
    if (imgUploadBtn) imgUploadBtn.addEventListener('click', uploadImage);
    const imgUrlAddBtn = $('#img-url-add-btn');
    if (imgUrlAddBtn) imgUrlAddBtn.addEventListener('click', addImageByUrl);
    const imgUrlInput = $('#img-url-input');
    if (imgUrlInput) imgUrlInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') addImageByUrl();
    });

    // Load initial state
    api.getState().then(s => {
        onStateUpdate(s);
        renderSetupTab();
        // Re-show ask_user input bubble if there's a pending request
        if (s && s.pending_user_input) checkPendingUserInput(s.pending_user_input);
    });
}

/**
 * Bootstrap the application — detect mode, check auth, initialize.
 */
async function bootstrap() {
    initApi({
        showAuthPhase,
        getAuthRequired: () => authRequired,
        setAuthUser: (u) => setAuthUser(u),
    });

    // In web mode, check auth status before initializing the app
    if (!window.pywebview) {
        await checkAuthStatus();
        if (authRequired && !authUser) {
            showAuthPhase();
            return;
        }
    }

    init();

    if (window.pywebview) {
        // Expose callbacks for pywebview Python→JS evaluate_js() calls
        window.onStateUpdate = onStateUpdate;
        window.onToolProgress = onToolProgress;
        window.onUserInputRequest = onUserInputRequest;
    } else {
        // SSE connection for real-time events (web mode only)
        const evtSource = new EventSource('/api/events');
        evtSource.addEventListener('tool_progress', (e) => {
            onToolProgress(JSON.parse(e.data));
        });
        evtSource.addEventListener('user_input_request', (e) => {
            onUserInputRequest(JSON.parse(e.data));
        });
        evtSource.onerror = () => console.debug('SSE reconnecting...');
    }

    // Show user bar if authenticated
    if (authUser) {
        showAppPhase();
    }
}

// Bootstrap: detect pywebview or fall back to web mode
const WEBVIEW_DETECT_TIMEOUT_MS = 100;
if (window.pywebview) { bootstrap(); }
else {
    window.addEventListener('pywebviewready', bootstrap);
    setTimeout(() => { if (!api) bootstrap(); }, WEBVIEW_DETECT_TIMEOUT_MS);
}
