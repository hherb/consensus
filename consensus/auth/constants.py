"""Authentication constants and the OAuth provider registry."""

from ..http_fetch import FETCH_BASE_DELAY, FETCH_MAX_RETRIES

AUTH_TOKEN_BYTES = 32
AUTH_TOKEN_TTL = 86400 * 30  # 30 days
PASSWORD_SALT_BYTES = 32
PASSWORD_HASH_ITERATIONS = 600_000  # OWASP 2023 recommendation for PBKDF2-SHA256
OAUTH_STATE_TTL = 600  # 10 minutes: max time for user to complete OAuth flow
OAUTH_HTTP_TIMEOUT = 15  # seconds per request to a provider
#: Attempts per provider request, the first included (shared fetch policy).
OAUTH_MAX_RETRIES = FETCH_MAX_RETRIES
#: Seconds before the first retry; doubled for each later one.
OAUTH_RETRY_BASE_DELAY = FETCH_BASE_DELAY
MIN_PASSWORD_LENGTH = 8

# OAuth provider definitions (client IDs/secrets come from env vars)
OAUTH_PROVIDERS = {
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


