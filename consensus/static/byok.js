/**
 * @module byok
 * Bring Your Own Key — client-side API key management via sessionStorage.
 */

const BYOK_STORAGE_KEY = 'consensus_api_keys';

/**
 * Retrieve all stored BYOK API keys for the current session.
 * @returns {Object<string, string>} Map of provider ID to API key
 */
export function getByokKeys() {
    try {
        return JSON.parse(sessionStorage.getItem(BYOK_STORAGE_KEY) || '{}');
    } catch { return {}; }
}

/**
 * Store or remove a BYOK API key for a provider.
 * @param {string|number} providerId - Provider ID
 * @param {string} key - API key (empty string to remove)
 */
export function setByokKey(providerId, key) {
    const keys = getByokKeys();
    if (key) {
        keys[String(providerId)] = key;
    } else {
        delete keys[String(providerId)];
    }
    sessionStorage.setItem(BYOK_STORAGE_KEY, JSON.stringify(keys));
}

/**
 * Check whether a BYOK key exists for a provider.
 * @param {string|number} providerId
 * @returns {boolean}
 */
export function hasByokKey(providerId) {
    return !!getByokKeys()[String(providerId)];
}

/**
 * Remove all stored BYOK keys.
 */
export function clearByokKeys() {
    sessionStorage.removeItem(BYOK_STORAGE_KEY);
}
