/**
 * @module auth
 * Authentication UI — login, registration, OAuth flows.
 */

import { $, show, hide, showToast } from './utils.js';
import { api } from './api.js';
import { onStateUpdate } from './state.js';
import { renderSetupTab } from './setup.js';

/** @type {object|null} Current authenticated user */
export let authUser = null;

/** @type {boolean} Whether the server requires authentication */
export let authRequired = false;

/** @type {boolean} Whether auth event listeners have been attached */
let _authListenersAttached = false;

/**
 * Set the authUser value.
 * @param {object|null} user
 */
export function setAuthUser(user) {
    authUser = user;
}

/** SVG icons for OAuth providers */
const OAUTH_ICONS = {
    github: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/></svg>',
    google: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 01-2.2 3.32v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.1z" fill="#4285F4"/><path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/><path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05"/><path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/></svg>',
    linkedin: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M20.447 20.452h-3.554v-5.569c0-1.328-.027-3.037-1.852-3.037-1.853 0-2.136 1.445-2.136 2.939v5.667H9.351V9h3.414v1.561h.046c.477-.9 1.637-1.85 3.37-1.85 3.601 0 4.267 2.37 4.267 5.455v6.286zM5.337 7.433a2.062 2.062 0 01-2.063-2.065 2.064 2.064 0 112.063 2.065zm1.782 13.019H3.555V9h3.564v11.452zM22.225 0H1.771C.792 0 0 .774 0 1.729v20.542C0 23.227.792 24 1.771 24h20.451C23.2 24 24 23.227 24 22.271V1.729C24 .774 23.2 0 22.222 0h.003z" fill="#0A66C2"/></svg>',
    apple: '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M18.71 19.5c-.83 1.24-1.71 2.45-3.05 2.47-1.34.03-1.77-.79-3.29-.79-1.53 0-2 .77-3.27.82-1.31.05-2.3-1.32-3.14-2.53C4.25 17 2.94 12.45 4.7 9.39c.87-1.52 2.43-2.48 4.12-2.51 1.28-.02 2.5.87 3.29.87.78 0 2.26-1.07 3.8-.91.65.03 2.47.26 3.64 1.98-.09.06-2.17 1.28-2.15 3.81.03 3.02 2.65 4.03 2.68 4.04-.03.07-.42 1.44-1.38 2.83M13 3.5c.73-.83 1.94-1.46 2.94-1.5.13 1.17-.34 2.35-1.04 3.19-.69.85-1.83 1.51-2.95 1.42-.15-1.15.41-2.35 1.05-3.11z"/></svg>',
};

/**
 * Check authentication status with the server.
 * Sets authRequired and authUser based on response.
 */
export async function checkAuthStatus() {
    try {
        const resp = await fetch('/auth/status');
        if (!resp.ok) return;
        const data = await resp.json();
        authRequired = data.auth_required;
        if (data.authenticated && data.user) {
            authUser = data.user;
        }
        if (authRequired && data.oauth_providers) {
            renderOAuthButtons(data.oauth_providers);
        }
    } catch {
        // Auth endpoint not available (e.g. desktop mode) — continue without auth
    }
}

/**
 * Render OAuth provider buttons in login and register forms.
 * @param {Array<{id: string, name: string}>} providers
 */
function renderOAuthButtons(providers) {
    if (!providers || providers.length === 0) return;
    for (const containerId of ['oauth-providers', 'oauth-providers-register']) {
        const section = document.getElementById(containerId);
        if (!section) continue;
        show(section);
        const btnContainer = section.querySelector('.oauth-buttons');
        if (!btnContainer) continue;
        btnContainer.innerHTML = '';
        for (const p of providers) {
            const btn = document.createElement('button');
            btn.className = 'oauth-btn';
            btn.innerHTML = (OAUTH_ICONS[p.id] || '') + `<span>Continue with ${p.name}</span>`;
            btn.addEventListener('click', () => {
                window.location.href = `/auth/oauth/authorize/${p.id}`;
            });
            btnContainer.appendChild(btn);
        }
    }
}

/**
 * Show the authentication phase (login/register screen).
 */
export function showAuthPhase() {
    show('#auth-phase');
    hide('#setup-phase');
    hide('#discussion-phase');
    hide('#user-bar');

    show('#auth-login-form');
    hide('#auth-register-form');

    if (!_authListenersAttached) {
        _authListenersAttached = true;

        $('#login-btn').addEventListener('click', doLogin);
        $('#login-password').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') doLogin();
        });
        $('#show-register-link').addEventListener('click', (e) => {
            e.preventDefault();
            hide('#auth-login-form');
            show('#auth-register-form');
        });

        $('#register-btn').addEventListener('click', doRegister);
        $('#register-password').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') doRegister();
        });
        $('#show-login-link').addEventListener('click', (e) => {
            e.preventDefault();
            hide('#auth-register-form');
            show('#auth-login-form');
        });

        $('#logout-btn').addEventListener('click', doLogout);
    }
}

/**
 * Show the main application phase (hide auth screen).
 */
export function showAppPhase() {
    hide('#auth-phase');
    show('#setup-phase');
    if (authUser) {
        show('#user-bar');
        $('#user-bar-name').textContent = authUser.display_name || authUser.email;
        if (!_authListenersAttached) {
            _authListenersAttached = true;
            $('#logout-btn').addEventListener('click', doLogout);
        }
    }
}

/**
 * Handle login form submission.
 */
async function doLogin() {
    const email = $('#login-email').value.trim();
    const password = $('#login-password').value;
    hide('#auth-error');

    if (!email || !password) {
        showAuthError('auth-error', 'Please enter email and password');
        return;
    }

    try {
        const resp = await fetch('/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            showAuthError('auth-error', data.error || 'Login failed');
            return;
        }
        authUser = data.user;
        showAppPhase();
        api.getState().then(s => {
            onStateUpdate(s);
            renderSetupTab();
        });
    } catch (err) {
        showAuthError('auth-error', 'Connection error. Please try again.');
    }
}

/**
 * Handle registration form submission.
 */
async function doRegister() {
    const name = $('#register-name').value.trim();
    const email = $('#register-email').value.trim();
    const password = $('#register-password').value;
    hide('#register-error');

    if (!email || !password) {
        showAuthError('register-error', 'Please fill in all fields');
        return;
    }

    try {
        const resp = await fetch('/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email, password, display_name: name }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            showAuthError('register-error', data.error || 'Registration failed');
            return;
        }
        authUser = data.user;
        showAppPhase();
        api.getState().then(s => {
            onStateUpdate(s);
            renderSetupTab();
        });
    } catch (err) {
        showAuthError('register-error', 'Connection error. Please try again.');
    }
}

/**
 * Handle logout.
 */
async function doLogout() {
    try {
        await fetch('/auth/logout', { method: 'POST' });
    } catch { /* ignore */ }
    authUser = null;
    if (authRequired) {
        showAuthPhase();
    }
}

/**
 * Display an error message in an auth form error element.
 * @param {string} elementId - ID of the error display element
 * @param {string} message - Error message text
 */
function showAuthError(elementId, message) {
    const el = document.getElementById(elementId);
    if (el) {
        el.textContent = message;
        show(el);
    }
}
