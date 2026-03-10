/**
 * @module images
 * Image management UI — upload, list, and remove images for discussions.
 */

import { $, show, hide, showToast } from './utils.js';
import { api } from './api.js';
import { state } from './state.js';

/**
 * Load the displayable src for an image element.
 * In desktop mode, fetches a base64 data URL from the bridge.
 * In web mode, uses the HTTP endpoint directly.
 */
export async function loadImageSrc(imgEl, imageId) {
    if (api.getImageDataUrl) {
        try {
            const dataUrl = await api.getImageDataUrl(imageId);
            if (dataUrl) { imgEl.src = dataUrl; return; }
        } catch { /* fall through */ }
    }
    imgEl.src = api.getImageUrl(imageId);
}

/**
 * Render the images panel for the current discussion setup.
 */
export async function renderImagePanel() {
    const container = $('#image-list');
    if (!container) return;

    const discussionId = state.id || 0;
    let images;
    try {
        const result = await api.getDiscussionImages(discussionId);
        images = Array.isArray(result) ? result : (result?.images || []);
    } catch {
        images = [];
    }

    if (images.length === 0) {
        container.innerHTML = '<p class="text-muted">No images attached yet.</p>';
        return;
    }

    container.innerHTML = `<div class="image-grid">${images.map(img => `
        <div class="image-thumb-wrapper" data-image-id="${img.id}">
            <img class="image-thumb" data-image-id="${img.id}"
                 alt="${_escHtml(img.title || img.filename)}"
                 title="${_escHtml(img.title || img.filename)}"
                 loading="lazy">
            <div class="image-thumb-label">${_escHtml(img.title || img.original_filename || 'Image')}</div>
            <button class="btn btn-ghost btn-sm image-remove-btn" data-image-id="${img.id}" title="Remove from discussion">\u2715</button>
        </div>
    `).join('')}</div>`;

    // Load image sources asynchronously (needed for desktop mode)
    container.querySelectorAll('.image-thumb[data-image-id]').forEach(imgEl => {
        loadImageSrc(imgEl, parseInt(imgEl.dataset.imageId));
    });

    // Wire remove buttons
    container.querySelectorAll('.image-remove-btn').forEach(btn => {
        btn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const imageId = parseInt(btn.dataset.imageId);
            await removeImage(imageId, discussionId);
        });
    });

    // Wire thumbnail click for lightbox
    container.querySelectorAll('.image-thumb').forEach(thumb => {
        thumb.addEventListener('click', () => {
            showLightbox(thumb.src, thumb.alt);
        });
    });
}

/**
 * Handle image file upload button click.
 */
export async function uploadImage() {
    const discussionId = state.id || 0;

    if (window.pywebview) {
        try {
            const result = await api.uploadImage(null, discussionId);
            if (result?.cancelled) return;
            if (result?.error) { showToast(result.error); return; }
            showToast(`Image added: ${result.title || result.filename}`);
            await renderImagePanel();
        } catch (e) {
            showToast('Upload failed: ' + e.message);
        }
        return;
    }

    // Web mode: use hidden file input
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/png,image/jpeg,image/gif,image/webp';
    input.addEventListener('change', async () => {
        const file = input.files?.[0];
        if (!file) return;

        const uploadBtn = $('#img-upload-btn');
        if (uploadBtn) {
            uploadBtn.disabled = true;
            uploadBtn.textContent = 'Uploading\u2026';
        }

        try {
            const result = await api.uploadImage(file, discussionId);
            if (result?.error) { showToast(result.error); return; }
            showToast(`Image added: ${result.title || result.filename}`);
            await renderImagePanel();
        } catch (e) {
            showToast('Upload failed: ' + e.message);
        } finally {
            if (uploadBtn) {
                uploadBtn.disabled = false;
                uploadBtn.textContent = '+ Upload Image';
            }
        }
    });
    input.click();
}

/**
 * Add an image by URL.
 */
export async function addImageByUrl() {
    const discussionId = state.id || 0;
    const urlInput = $('#img-url-input');
    const url = urlInput?.value?.trim();
    if (!url) {
        showToast('Please enter a URL');
        return;
    }

    const addBtn = $('#img-url-add-btn');
    if (addBtn) {
        addBtn.disabled = true;
        addBtn.textContent = 'Fetching\u2026';
    }

    try {
        const result = await api.addImageFromUrl(url, discussionId, '');
        if (result?.error) { showToast(result.error); return; }
        showToast(`Image added: ${result.title || result.filename}`);
        if (urlInput) urlInput.value = '';
        await renderImagePanel();
    } catch (e) {
        showToast('Failed to add image: ' + e.message);
    } finally {
        if (addBtn) {
            addBtn.disabled = false;
            addBtn.textContent = 'Add URL';
        }
    }
}

/**
 * Remove an image from the current discussion.
 */
export async function removeImage(imageId, discussionId) {
    try {
        const result = await api.removeImage(imageId, discussionId || state.id || 0);
        if (result?.error) { showToast(result.error); return; }
        showToast('Image removed');
        await renderImagePanel();
    } catch (e) {
        showToast('Remove failed: ' + e.message);
    }
}

/**
 * Show a lightbox overlay with a full-size image.
 */
export function showLightbox(src, alt) {
    let overlay = $('#image-lightbox');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'image-lightbox';
        overlay.className = 'image-lightbox';
        overlay.innerHTML = '<img class="lightbox-img" alt="">';
        overlay.addEventListener('click', () => overlay.classList.add('hidden'));
        document.body.appendChild(overlay);
    }
    const img = overlay.querySelector('img');
    img.src = src;
    img.alt = alt || '';
    overlay.classList.remove('hidden');
}

/**
 * Render inline images for a message element.
 * @param {HTMLElement} div - The message element
 * @param {number[]} imageIds - Array of image IDs
 */
export function renderMessageImages(div, imageIds) {
    if (!imageIds || imageIds.length === 0) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'message-images';
    for (const id of imageIds) {
        const img = document.createElement('img');
        img.className = 'message-image-thumb';
        img.loading = 'lazy';
        img.addEventListener('click', () => showLightbox(img.src, ''));
        loadImageSrc(img, id);
        wrapper.appendChild(img);
    }
    div.appendChild(wrapper);
}

/** Escape HTML to prevent XSS */
function _escHtml(str) {
    const d = document.createElement('div');
    d.textContent = str;
    return d.innerHTML;
}
