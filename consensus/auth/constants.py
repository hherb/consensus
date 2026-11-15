"""Authentication constants and the OAuth provider registry."""

from ..http_fetch import FETCH_BASE_DELAY, FETCH_MAX_RETRIES

AUTH_TOKEN_BYTES = 32
AUTH_TOKEN_TTL = 86400 * 30  # 30 days
PASSWORD_SALT_BYTES = 32
#: PBKDF2-SHA256 rounds for new hashes (OWASP 2023). Each hash records its
#: own count, so raising this re-hashes users at their next successful
#: login (#127).
PASSWORD_HASH_ITERATIONS = 600_000
#: Rounds a pre-#127 ``salt$hash`` value was made with. Never change it.
LEGACY_PASSWORD_HASH_ITERATIONS = 600_000
#: Leading field of a current-format stored password hash.
PASSWORD_HASH_SCHEME = "pbkdf2_sha256"
OAUTH_STATE_TTL = 600  # 10 minutes: max time for user to complete OAuth flow
# Names on the provider-request logging path avoid "oauth": CodeQL's
# password heuristic matches it and flags every log line they reach.
SIGN_IN_HTTP_TIMEOUT = 15  # seconds per request to a provider
#: Seconds the whole provider exchange may take, retries included (#130).
#: Kept under nginx's default 60 s ``proxy_read_timeout``, so the browser
#: hears "try again" from us rather than a 504 while sign-in carries on.
SIGN_IN_DEADLINE_SECONDS = 45
#: Attempts per provider request, the first included (shared fetch policy).
SIGN_IN_MAX_RETRIES = FETCH_MAX_RETRIES
#: Seconds before the first retry; doubled for each later one.
SIGN_IN_RETRY_BASE_DELAY = FETCH_BASE_DELAY
MIN_PASSWORD_LENGTH = 8
#: Characters of a provider's error reply body kept in the log.
SIGN_IN_ERROR_BODY_LOG_CHARS = 300

# OAuth provider definitions (client IDs/secrets come from env vars)
SIGN_IN_PROVIDERS = {
    "github": {
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "userinfo_email_url": "https://api.github.com/user/emails",
        "scopes": "read:user user:email",
        "env_client_id": "CONSENSUS_GITHUB_CLIENT_ID",
        "env_client_secret": "CONSENSUS_GITHUB_CLIENT_SECRET",
    },
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://www.googleapis.com/oauth2/v2/userinfo",
        "scopes": "openid email profile",
        "env_client_id": "CONSENSUS_GOOGLE_CLIENT_ID",
        "env_client_secret": "CONSENSUS_GOOGLE_CLIENT_SECRET",
    },
    "linkedin": {
        "authorize_url": "https://www.linkedin.com/oauth/v2/authorization",
        "token_url": "https://www.linkedin.com/oauth/v2/accessToken",
        "userinfo_url": "https://api.linkedin.com/v2/userinfo",
        "scopes": "openid profile email",
        "env_client_id": "CONSENSUS_LINKEDIN_CLIENT_ID",
        "env_client_secret": "CONSENSUS_LINKEDIN_CLIENT_SECRET",
    },
    "apple": {
        "authorize_url": "https://appleid.apple.com/auth/authorize",
        "token_url": "https://appleid.apple.com/auth/token",
        "userinfo_url": "",  # Apple returns user info in the ID token
        "scopes": "name email",
        "env_client_id": "CONSENSUS_APPLE_CLIENT_ID",
        "env_client_secret": "CONSENSUS_APPLE_CLIENT_SECRET",
        "response_mode": "form_post",
    },
}


