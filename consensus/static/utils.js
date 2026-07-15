/**
 * @module utils
 * Pure utility functions with no state dependencies.
 */

/**
 * Query selector shorthand.
 * @param {string} sel - CSS selector
 * @returns {Element|null}
 */
export const $ = (sel) => document.querySelector(sel);

/**
 * Query selector all shorthand.
 * @param {string} sel - CSS selector
 * @returns {NodeListOf<Element>}
 */
export const $$ = (sel) => document.querySelectorAll(sel);

/**
 * Remove 'hidden' class from an element.
 * @param {string|Element} el - CSS selector or DOM element
 */
export function show(el) {
    if (typeof el === 'string') el = $(el);
    el?.classList.remove('hidden');
}

/**
 * Add 'hidden' class to an element.
 * @param {string|Element} el - CSS selector or DOM element
 */
export function hide(el) {
    if (typeof el === 'string') el = $(el);
    el?.classList.add('hidden');
}

const TOAST_DEFAULT_DURATION_MS = 4000;
const TOAST_FADE_DELAY_MS = 300;

// Duration for advisory/warning toasts that carry more text than a
// transient error and need longer to read (e.g. the same-model panel
// warning, #29).
export const TOAST_WARNING_DURATION_MS = 6000;

/**
 * Show a temporary toast notification.
 * @param {string} msg - Message text
 * @param {number} [duration=4000] - Display duration in ms
 * @param {string} [type='error'] - Toast type: 'error', 'success', 'info', 'warning'
 */
export function showToast(msg, duration = TOAST_DEFAULT_DURATION_MS, type = 'error') {
    const existing = $('.toast');
    if (existing) existing.remove();
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('toast-fade-out');
        setTimeout(() => toast.remove(), TOAST_FADE_DELAY_MS);
    }, duration);
}

/**
 * Extract up to 2-character initials from a name.
 * @param {string} name
 * @returns {string}
 */
export function getInitials(name) {
    return (name || '?').split(/\s+/).map(w => w[0]).join('').toUpperCase().slice(0, 2);
}

/**
 * Format a Unix timestamp as a short time string (HH:MM).
 * @param {number} ts - Unix timestamp in seconds
 * @returns {string}
 */
export function formatTime(ts) {
    return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/**
 * Format a Unix timestamp as a short date string.
 * @param {number} ts - Unix timestamp in seconds
 * @returns {string}
 */
export function formatDate(ts) {
    return new Date(ts * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/**
 * Escape HTML special characters to prevent XSS.
 * @param {string} s - Raw string
 * @returns {string} HTML-safe string
 */
export function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
}

/**
 * Render a subset of Markdown to HTML (code blocks, headers, bold, italic, lists).
 * Input is HTML-escaped first to prevent XSS.
 * @param {string} text - Raw markdown text
 * @returns {string} HTML string
 */
export function renderMarkdown(text) {
    if (!text) return '';
    let html = escHtml(text);
    return html
        .replace(/```(\w*)\n([\s\S]*?)```/g, '<pre><code>$2</code></pre>')
        .replace(/^### (.+)$/gm, '<h3>$1</h3>')
        .replace(/^## (.+)$/gm, '<h2>$1</h2>')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/^[-*] (.+)$/gm, '<uli>$1</uli>')
        .replace(/^\d+\. (.+)$/gm, '<oli>$1</oli>')
        .replace(/((?:<uli>.*<\/uli>\n?)+)/g, (m) =>
            '<ul>' + m.replace(/<uli>/g, '<li>').replace(/<\/uli>/g, '</li>') + '</ul>')
        .replace(/((?:<oli>.*<\/oli>\n?)+)/g, (m) =>
            '<ol>' + m.replace(/<oli>/g, '<li>').replace(/<\/oli>/g, '</li>') + '</ol>')
        .replace(/^(?!<(?:h[1-6]|ul|ol|li|p|pre))(.*\S.*)$/gm, '<p>$1</p>');
}

/**
 * Convert text to a URL-safe slug, truncated to 40 characters.
 * @param {string} text
 * @returns {string}
 */
export function slugify(text) {
    return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40);
}

/**
 * Validate and return a hex color, defaulting to '#666' if invalid.
 * @param {string} c - Color string
 * @returns {string} Valid hex color
 */
export function safeColor(c) {
    return /^#[0-9a-fA-F]{3,8}$/.test(c) ? c : '#666';
}
