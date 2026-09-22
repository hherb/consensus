/**
 * @module turn-notices
 * Wording for turn-level failure notifications.
 *
 * Split out of discussion-actions (golden rule 8) and kept pure: the
 * backend classifies a failure and this module decides how to say it, so
 * both halves can change independently.
 */

// Wording for a skipped turn, keyed by the backend's error_kind. The
// backend classifies the failure (see helpers.classify_flow_error) so the
// toast does not blame the provider for a Consensus bug, or Consensus for
// a missing API key (#74). Unknown kinds fall back to a neutral phrasing
// rather than guessing.
const SKIP_TOAST_BY_KIND = {
    provider: (name) => `${name} skipped due to an API error`,
    config: (name) => `${name} skipped due to a configuration problem`,
    internal: (name) => `${name} skipped due to an internal Consensus error`,
};

// Shown when the backend could not persist the skip notice it just
// posted. The transcript bubble says so too, but it scrolls away; the
// toast is what the user is looking at when it happens.
const UNSAVED_NOTICE_WARNING = ' (this notice could not be saved)';

/**
 * Build the toast text for a skipped participant turn.
 * @param {object} speaker - The entity whose turn was skipped
 * @param {object} result - The generate_ai_turn result, carrying
 *   error_kind and optionally notice_unsaved
 * @returns {string} Toast text naming the kind of failure
 */
export function skipToastMessage(speaker, result) {
    const phrase = SKIP_TOAST_BY_KIND[result?.error_kind];
    const base = phrase ? phrase(speaker.name) : `${speaker.name} was skipped`;
    const detail = result?.error ? `${base}: ${result.error}` : base;
    return result?.notice_unsaved ? detail + UNSAVED_NOTICE_WARNING : detail;
}
