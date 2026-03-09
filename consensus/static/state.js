/**
 * @module state
 * Shared application state and state update handling.
 * No imports from other app modules to avoid circular dependencies.
 */

import { $ } from './utils.js';

/** @type {object} Global application state */
export let state = {
    topic: '', entities: [], moderator_id: null, messages: [],
    storyboard: [], turn_order: [], current_turn_index: 0,
    turn_number: 0, is_active: false, status: 'setup', current_speaker_id: null,
    providers: [], saved_entities: [], prompts: [], discussions_history: [],
};

/** @type {boolean} Whether an AI turn is currently being processed */
export let processing = false;

/** @type {number} Count of messages already rendered in the DOM */
export let renderedMessageCount = 0;

/** @type {number} Count of storyboard entries already rendered in the DOM */
export let renderedStoryboardCount = 0;

/** @type {number|null} Timer for stall detection on progress indicators */
export let progressStallTimer = null;

/**
 * Callback invoked after state update when setup phase is visible.
 * Registered by the app module to avoid circular imports.
 * @type {Function|null}
 */
let _onSetupVisible = null;

/**
 * Register a callback to run when state updates while setup phase is visible.
 * @param {Function} fn
 */
export function registerSetupCallback(fn) {
    _onSetupVisible = fn;
}

/**
 * Set the processing flag.
 * @param {boolean} value
 */
export function setProcessing(value) {
    processing = value;
}

/**
 * Reset rendered message count (e.g. when switching discussions).
 * @param {number} [value=0]
 */
export function resetRenderedMessageCount(value = 0) {
    renderedMessageCount = value;
}

/**
 * Update rendered message count to match current state.
 */
export function syncRenderedMessageCount() {
    renderedMessageCount = state.messages.length;
}

/**
 * Reset rendered storyboard count.
 * @param {number} [value=0]
 */
export function resetRenderedStoryboardCount(value = 0) {
    renderedStoryboardCount = value;
}

/**
 * Update rendered storyboard count to match current state.
 */
export function syncRenderedStoryboardCount() {
    renderedStoryboardCount = state.storyboard.length;
}

/**
 * Set the progress stall timer.
 * @param {number|null} timer
 */
export function setProgressStallTimer(timer) {
    progressStallTimer = timer;
}

/**
 * Look up an entity by ID in the current state.
 * @param {number} id - Entity ID
 * @returns {object|undefined}
 */
export function getEntity(id) {
    return state.entities.find(e => e.id === id);
}

/**
 * Handle a state update from the backend.
 * Replaces current state and re-renders setup tab if visible.
 * @param {object} newState - New state from backend
 */
export function onStateUpdate(newState) {
    if (!newState) return;
    if (progressStallTimer) {
        clearTimeout(progressStallTimer);
        progressStallTimer = null;
    }
    state = newState;
    if (_onSetupVisible && $('#setup-phase') && !$('#setup-phase').classList.contains('hidden')) {
        _onSetupVisible();
    }
}
