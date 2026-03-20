# Chapter 14: Multi-User Mode and Authentication

For shared or public deployments, Consensus supports multi-user mode with full authentication.

## Enabling Multi-User Mode

```bash
consensus --web --multi-user
```

In multi-user mode:
- Each browser session gets its own isolated `ConsensusApp` instance and SQLite database
- Sessions are identified by a `consensus_sid` cookie
- Sessions expire after 24 hours of inactivity
- Users must authenticate before accessing the application

## Authentication Methods

### Email and Password

Users can register with:
- Display name
- Email address
- Password (minimum 8 characters)

Passwords are hashed using PBKDF2-SHA256 with 600,000 iterations (OWASP 2023 recommendation). Brute-force protection limits login attempts to 5 per email address per 5-minute window.

### OAuth Providers

Consensus supports OAuth sign-in via four providers. Each requires environment variables for the client credentials:

**GitHub:**
```bash
export CONSENSUS_GITHUB_CLIENT_ID="your-client-id"
export CONSENSUS_GITHUB_CLIENT_SECRET="your-client-secret"
```

**Google:**
```bash
export CONSENSUS_GOOGLE_CLIENT_ID="your-client-id"
export CONSENSUS_GOOGLE_CLIENT_SECRET="your-client-secret"
```

**LinkedIn:**
```bash
export CONSENSUS_LINKEDIN_CLIENT_ID="your-client-id"
export CONSENSUS_LINKEDIN_CLIENT_SECRET="your-client-secret"
```

**Apple:**
```bash
export CONSENSUS_APPLE_CLIENT_ID="your-client-id"
export CONSENSUS_APPLE_CLIENT_SECRET="your-client-secret"
```

OAuth buttons appear on the login screen only for providers that have credentials configured.

Users can link multiple OAuth identities to a single account.

### Base URL Configuration

OAuth redirect URIs are derived from the `CONSENSUS_BASE_URL` environment variable. Set this to your deployment's public URL:

```bash
export CONSENSUS_BASE_URL="https://consensus.example.com"
```

## Security Features

| Feature | Details |
|---------|---------|
| **Password hashing** | PBKDF2-SHA256, 600k iterations |
| **Auth tokens** | SHA-256 hashed in storage, httpOnly cookies, 30-day TTL |
| **Brute-force protection** | 5 login attempts per email per 5 minutes |
| **CSRF protection** | Content-Type header enforcement |
| **Rate limiting** | 120 requests per 60 seconds per client IP |
| **CORS** | Configurable allowed origins |
| **Security headers** | Applied to all responses |
| **Path traversal protection** | Static file serving sanitisation |

### CORS Configuration

For cross-origin deployments, set allowed origins:

```bash
export CONSENSUS_ALLOWED_ORIGINS="https://consensus.example.com,https://app.example.com"
```

## BYOK in Multi-User Mode

In web mode, users can provide their own API keys through the browser UI (the "Set Key" button on each provider). These keys are stored in the browser's `sessionStorage` — they are sent per-request and never stored on the server. This allows a shared deployment where each user brings their own API keys.

Server-side environment variables serve as a fallback when no user-provided key is available.
