# 11. Authentication

[Back to index](programmer-manual.md) | [Previous: Contributing](10-contributing.md) | [Next: Cost Tracking](12-cost-tracking.md)

---

Authentication is only enforced in **multi-user web mode** (`--web --multi-user`).
Desktop mode and single-user web mode work without authentication.

## Overview

The auth system supports two registration paths:

1. **Email/password** — simple registration with PBKDF2-SHA256 hashing
2. **OAuth** — Authorization Code flow for GitHub, Google, LinkedIn, and Apple

Both paths produce an auth token stored as an httpOnly cookie. The token is
SHA-256 hashed before storage in the database (never stored in plaintext).

## Package: `auth/`

`consensus/auth/` (split from `auth.py`, issue #61): `constants.py`
(constants and `SIGN_IN_PROVIDERS`), `models.py`, `passwords.py`,
`database.py` (`AuthDatabase`), `oauth.py` (provider helpers and code
exchange) and `manager.py` (`AuthManager`). `__init__.py` re-exports the
public API.

### Data Classes

| Class | Purpose |
|-------|---------|
| `User` | Registered user with email, display name, OAuth info, timestamps. `to_dict()` excludes `password_hash` |
| `AuthToken` | Token metadata (user_id, token_hash, expiry) |

### Password Hashing

- Algorithm: PBKDF2-SHA256 with 600,000 iterations (OWASP 2023 recommendation)
- Salt: 32 bytes of `os.urandom()`
- Storage format: `salt_hex$hash_hex`
- Verification uses `hmac.compare_digest()` for timing-safe comparison

### Token Management

- Tokens are generated with `secrets.token_urlsafe(32)` (256 bits of entropy)
- Stored as SHA-256 hashes (never plaintext)
- Default TTL: 30 days (`AUTH_TOKEN_TTL`); `create_token()` prunes expired
  tokens and `store_oauth_state()` prunes abandoned states
- Tokens are not returned in JSON responses — they are set as httpOnly cookies only

### `AuthDatabase`

SQLite persistence for auth data. Uses the same `threading.Lock` pattern as
the main `Database` class. In multi-user mode, auth data lives in a dedicated
`auth.db` file (separate from per-session discussion databases).

**Tables:**

| Table | Purpose |
|-------|---------|
| `users` | User accounts (email, password hash, OAuth info, timestamps) |
| `auth_tokens` | Hashed bearer tokens with expiry |
| `user_oauth_identities` | Multiple OAuth identities per user (provider + oauth_id) |
| `oauth_states` | CSRF state tokens for OAuth flow (10-minute TTL) |

An OAuth sign-up stores its identity only in the `users` columns
`oauth_provider` and `oauth_id`; the first `link_oauth()` copies it into
`user_oauth_identities`, which holds every linked provider from then on.
Lookups check that table first and fall back to the `users` columns.

**Key methods:**

| Method | Purpose |
|--------|---------|
| `create_user()` | Register with email/password or OAuth |
| `get_user_by_email()` | Lookup by email |
| `get_user_by_oauth()` | Lookup by provider + oauth_id (checks `user_oauth_identities` first, falls back to legacy columns) |
| `create_token()` | Generate and store a new auth token |
| `validate_token()` | Verify token and return associated user |
| `link_oauth()` | Link an OAuth identity to an existing user (supports multiple); first copies the legacy-column identity into `user_oauth_identities` so an OAuth sign-up's original provider is not lost |
| `update_user()` | Update `display_name` / `avatar_url` only — `email` is deliberately not editable (see Security Summary) |
| `clear_password()` | Remove the password, leaving OAuth as the only sign-in |
| `store_oauth_state()` / `consume_oauth_state()` | CSRF protection for OAuth flow |

### `AuthManager`

High-level API used by `server/` (`routes_auth.py`). Methods:

| Method | Purpose |
|--------|---------|
| `register(email, password, display_name)` | Create user + token. Validates email format and password length (`MIN_PASSWORD_LENGTH`, 8) |
| `login(email, password)` | Authenticate, return user + token |
| `logout(token)` | Revoke a single token |
| `get_current_user(token)` | Validate token, return `User` or `None` |
| `oauth_callback(provider, code, redirect_uri)` | Exchange OAuth code, find-or-create user, return user + token. Refuses a reply without an email or provider user id. Links to an existing account by email only when the provider says the email is verified; if that account has a password, the password is cleared and its sessions revoked |

### OAuth Flow

1. User clicks an OAuth provider button in the UI
2. Frontend navigates to `/auth/oauth/authorize/{provider}`
3. Server generates a state token, stores it in `oauth_states`, and redirects
   to the provider's authorization URL
4. Provider redirects back to `/auth/oauth/callback/{provider}` with code + state
5. Server validates the state token, exchanges the code for an access token,
   fetches user info, and finds-or-creates the user. Each provider request
   retries transient faults (transport errors, 408/425/429, most 5xx) with
   exponential backoff (`SIGN_IN_MAX_RETRIES`, `SIGN_IN_RETRY_BASE_DELAY`, shared
   with `http_fetch`)
6. Auth cookie is set and user is redirected to `/`

**OAuth redirect URI** is derived from the `CONSENSUS_BASE_URL` environment
variable (not request headers) to prevent host header injection attacks. If
not set, falls back to request host with a warning log.

**Supported providers:**

| Provider | User info source | Special handling |
|----------|-----------------|-----------------|
| GitHub | `/user` + `/user/emails` | Fetches verified primary email separately; a failed lookup is logged and the email treated as unverified |
| Google | `/oauth2/v2/userinfo` | Requests offline access for refresh tokens |
| LinkedIn | `/v2/userinfo` (OpenID Connect) | Standard OIDC |
| Apple | ID token (JWT payload) | Uses `response_mode=form_post`; JWT signature verification is a TODO |

**Adding a new OAuth provider:**

1. Add provider config to `SIGN_IN_PROVIDERS` dict in `auth/constants.py`
2. Add a case to `_extract_user_info()` (`auth/oauth.py`) for the provider's
   userinfo format, returning `email`, `name`, `avatar_url`, `oauth_id` (via
   `_provider_id()`) and `email_verified`
3. Set `CONSENSUS_<PROVIDER>_CLIENT_ID` and `CONSENSUS_<PROVIDER>_CLIENT_SECRET`
   environment variables
4. The provider will automatically appear in the login UI

---

## Server Integration (`server/`)

### Middleware

| Middleware | Purpose |
|------------|---------|
| `csrf_middleware` | Rejects a POST to `/api/` or `/auth/` that is neither `application/json` nor carries the `X-Consensus-Request` header. Excludes `/auth/oauth/callback/` (Apple uses `form_post`) |
| `auth_middleware` | Enforces authentication on `/api/*` routes in multi-user mode and resolves the user for `/auth/me`. Extracts token from `Authorization: Bearer` header or `consensus_auth` cookie |

### Login Rate Limiting

Per-email brute-force protection: maximum 5 failed login attempts per email
address within a 5-minute window. Successful logins do not count against
the limit. The rate limit state is in-memory (resets on server restart);
emails with no attempt inside the window are pruned once per window.

### Auth Endpoints

All auth endpoints are under `/auth/` (not `/api/`), so the auth middleware
does not enforce sign-in on them (it only resolves the user for `/auth/me`).
They are protected by the CSRF middleware (except OAuth callbacks).

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/auth/register` | POST | Register with email/password |
| `/auth/login` | POST | Login with email/password |
| `/auth/logout` | POST | Revoke current token, clear cookie |
| `/auth/status` | GET | Check auth state (returns `auth_required`, `authenticated`, `user`, `oauth_providers`) |
| `/auth/me` | GET | Get current user profile (401 when not signed in) |
| `/auth/me` | POST | Update profile (display_name, avatar_url only) |
| `/auth/oauth/providers` | GET | List configured OAuth providers |
| `/auth/oauth/authorize/{provider}` | GET | Start OAuth flow (redirects to provider) |
| `/auth/oauth/callback/{provider}` | GET/POST | Handle OAuth callback |

### Response Format

Auth endpoints return user info without the token:
```json
{"user": {"id": 1, "email": "...", "display_name": "...", ...}}
```

The auth token is set only as an httpOnly cookie — never in the response body.

---

## Frontend (`app.js`)

### Auth State

Two global variables track auth state:
- `authUser` — current authenticated user object, or `null`
- `authRequired` — whether the server requires authentication

### Bootstrap Flow

1. `bootstrap()` creates the API adapter
2. In web mode, calls `checkAuthStatus()` which fetches `/auth/status`
3. If `auth_required && !authUser`, shows the login screen (`showAuthPhase()`)
4. Otherwise, initializes the app normally

### Auth UI

- **`#auth-phase`** — login/register forms with toggle between them
- **`#user-bar`** — top bar showing authenticated user name + sign out button
- **OAuth buttons** — rendered dynamically from `/auth/status` response
- **401 handling** — `WebAPI._post()` intercepts 401 responses and redirects
  to the login screen

Event listeners are attached once via a `_authListenersAttached` guard to
prevent stacking on repeated `showAuthPhase()` / `showAppPhase()` calls.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `CONSENSUS_BASE_URL` | For OAuth | Public base URL (e.g. `https://yourdomain.com`). Used for OAuth redirect URIs |
| `CONSENSUS_GITHUB_CLIENT_ID` | No | GitHub OAuth app client ID |
| `CONSENSUS_GITHUB_CLIENT_SECRET` | No | GitHub OAuth app client secret |
| `CONSENSUS_GOOGLE_CLIENT_ID` | No | Google OAuth client ID |
| `CONSENSUS_GOOGLE_CLIENT_SECRET` | No | Google OAuth client secret |
| `CONSENSUS_LINKEDIN_CLIENT_ID` | No | LinkedIn OAuth client ID |
| `CONSENSUS_LINKEDIN_CLIENT_SECRET` | No | LinkedIn OAuth client secret |
| `CONSENSUS_APPLE_CLIENT_ID` | No | Apple OAuth client ID |
| `CONSENSUS_APPLE_CLIENT_SECRET` | No | Apple OAuth client secret |

OAuth providers only appear in the UI when both client ID and secret are set.

---

## Security Summary

- Passwords: PBKDF2-SHA256, 600k iterations, 32-byte random salt, stored as
  `pbkdf2_sha256$<iterations>$<salt>$<hash>` so the count can be raised: a
  hash below the current count (or in the legacy `salt$hash` format, which
  meant 600k) is re-hashed at the user's next successful login
- Login timing: an unknown or password-less email is checked against a
  cached dummy hash, so every failed login costs one PBKDF2. Registration
  still says when an email is taken (#139)
- Tokens: `secrets.token_urlsafe(32)`, SHA-256 hashed in DB, httpOnly cookie
- CSRF: a POST to `/api/` or `/auth/` must be `application/json` or carry
  the `X-Consensus-Request` header
- Brute-force: 5 attempts per email per 5 minutes
- OAuth state: cryptographically random, 10-minute expiry, single-use
- OAuth redirect: derived from `CONSENSUS_BASE_URL` (not request headers)
- Profile updates: explicit field allowlist (display_name, avatar_url). The
  email is not editable: nothing verifies a new address, and OAuth links a
  provider-verified email to the account holding it, so an edit could claim
  someone else's address
- Account pre-hijack: registration does not verify the email, so when a
  verified OAuth sign-in links to a password account, the password is cleared
  and every session revoked — whoever set it may not own the address
- Login checks the password before reporting a disabled account
- XSS: OAuth error pages use `html.escape()` on user-controlled input

---

[Next: Cost Tracking](12-cost-tracking.md)
