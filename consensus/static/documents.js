/**
 * @module documents
 * Document management UI — upload, list, and remove documents for discussions.
 */

import { $, show, hide, showToast } from './utils.js';
import { api } from './api.js';
import { state } from './state.js';

/**
 * Render the documents panel for the current discussion setup.
 * Called when the New Discussion tab is shown.
 */
export async function renderDocumentPanel() {
    const container = $('#document-list');
    if (!container) return;

    const discussionId = state.id || 0;
    let docs;
    try {
        const result = await api.getDiscussionDocuments(discussionId);
        docs = Array.isArray(result) ? result : (result?.documents || []);
    } catch {
        docs = [];
    }

    if (docs.length === 0) {
        container.innerHTML = '<p class="text-muted">No documents attached yet.</p>';
        return;
    }

    container.innerHTML = docs.map(doc => `
        <div class="document-item" data-doc-id="${doc.id}">
            <div class="document-info">
                <strong>${_escHtml(doc.title || doc.filename)}</strong>
                <span class="text-muted">${_formatSize(doc.char_count)} chars</span>
                ${doc.summary ? `<p class="document-summary">${_escHtml(doc.summary.slice(0, 200))}${doc.summary.length > 200 ? '…' : ''}</p>` : ''}
            </div>
            <button class="btn btn-ghost btn-sm doc-remove-btn" data-doc-id="${doc.id}" title="Remove from discussion">✕</button>
        </div>
    `).join('');

    // Wire remove buttons
    container.querySelectorAll('.doc-remove-btn').forEach(btn => {
        btn.addEventListener('click', async () => {
            const docId = parseInt(btn.dataset.docId);
            await removeDocument(docId, discussionId);
        });
    });
}

/**
 * Handle file upload button click.
 */
export async function uploadDocument() {
    const discussionId = state.id || 0;

    if (window.pywebview) {
        // Desktop mode: bridge handles the file picker
        try {
            const result = await api.uploadDocument(null, discussionId);
            if (result?.cancelled) return;
            if (result?.error) { showToast(result.error); return; }
            showToast(`Document added: ${result.title || result.filename}`);
            await renderDocumentPanel();
        } catch (e) {
            showToast('Upload failed: ' + e.message);
        }
        return;
    }

    // Web mode: use hidden file input
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = '.pdf,.html,.htm,.txt,.md,.text';
    input.addEventListener('change', async () => {
        const file = input.files?.[0];
        if (!file) return;

        const uploadBtn = $('#doc-upload-btn');
        if (uploadBtn) {
            uploadBtn.disabled = true;
            uploadBtn.textContent = 'Uploading…';
        }

        try {
            const result = await api.uploadDocument(file, discussionId);
            if (result?.error) { showToast(result.error); return; }
            showToast(`Document added: ${result.title || result.filename}`);
            await renderDocumentPanel();
        } catch (e) {
            showToast('Upload failed: ' + e.message);
        } finally {
            if (uploadBtn) {
                uploadBtn.disabled = false;
                uploadBtn.textContent = '+ Upload File';
            }
        }
    });
    input.click();
}

/**
 * Show a dialog to add a document by URL.
 */
export async function addDocumentByUrl() {
    const discussionId = state.id || 0;
    const urlInput = $('#doc-url-input');
    const url = urlInput?.value?.trim();
    if (!url) {
        showToast('Please enter a URL');
        return;
    }

    const addBtn = $('#doc-url-add-btn');
    if (addBtn) {
        addBtn.disabled = true;
        addBtn.textContent = 'Fetching…';
    }

    try {
        const result = await api.addDocumentFromUrl(url, discussionId, '');
        if (result?.error) { showToast(result.error); return; }
        showToast(`Document added: ${result.title || result.filename}`);
        if (urlInput) urlInput.value = '';
        await renderDocumentPanel();
    } catch (e) {
        showToast('Failed to add URL: ' + e.message);
    } finally {
        if (addBtn) {
            addBtn.disabled = false;
            addBtn.textContent = 'Add URL';
        }
    }
}

/**
 * Remove a document from the current discussion.
 */
export async function removeDocument(docId, discussionId) {
    try {
        const result = await api.removeDocument(docId, discussionId || state.id || 0);
        if (result?.error) { showToast(result.error); return; }
        showToast('Document removed');
        await renderDocumentPanel();
    } catch (e) {
        showToast('Remove failed: ' + e.message);
    }
}

/** Format character count for display */
function _formatSize(chars) {
    if (chars >= 1_000_000) return (chars / 1_000_000).toFixed(1) + 'M';
    if (chars >= 1_000) return (chars / 1_000).toFixed(1) + 'K';
    return String(chars);
}

/** Escape HTML to prevent XSS */
function _escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}
