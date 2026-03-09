/**
 * @module memory
 * Memory configuration UI — endpoint setup and connection testing.
 */

import { $, show, hide, showToast } from './utils.js';
import { api } from './api.js';

/**
 * Load and display the current memory configuration.
 */
export async function loadMemoryConfig() {
    try {
        const result = await api.getMemoryConfig();
        if (result && result.error) {
            show('#memory-unavailable-msg');
            hide('#memory-config-form');
            const msgEl = $('#memory-unavailable-msg');
            if (msgEl) msgEl.textContent = result.error;
            return;
        }
        hide('#memory-unavailable-msg');
        show('#memory-config-form');
        if (result) {
            $('#memory-endpoint').value = result.embedding_endpoint || '';
            $('#memory-model').value = result.embedding_model || '';
        }
    } catch (e) {
        show('#memory-unavailable-msg');
        hide('#memory-config-form');
    }
}

/**
 * Save the memory configuration from the form.
 */
export async function saveMemoryConfig() {
    const endpoint = $('#memory-endpoint').value.trim();
    const model = $('#memory-model').value.trim();
    try {
        const result = await api.saveMemoryConfig({ embedding_endpoint: endpoint, embedding_model: model });
        if (result && result.error) showToast(result.error);
        else if (result && result.ok) showToast('Memory config saved');
        else showToast('Failed to save memory config');
    } catch (e) {
        showToast('Error: ' + e.message);
    }
}

/**
 * Test the memory connection and display the result.
 */
export async function testMemoryConnection() {
    const resultEl = $('#memory-test-result');
    resultEl.textContent = 'Testing…';
    try {
        const data = await api.testMemoryConnection();
        resultEl.textContent = data.message || (data.ok ? 'Connected' : 'Failed');
        resultEl.style.color = data.ok ? 'var(--color-success, #22c55e)' : 'var(--color-error, #ef4444)';
    } catch (e) {
        resultEl.textContent = 'Connection error: ' + e.message;
        resultEl.style.color = 'var(--color-error, #ef4444)';
    }
}
