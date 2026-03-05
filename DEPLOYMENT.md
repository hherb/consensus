# Consensus — Oracle Cloud Free Tier Deployment Plan

## Overview

Deploy Consensus as a public BYOK (Bring Your Own Key) service on Oracle Cloud's Always Free Tier. Users provide their own LLM API keys — no keys stored server-side. Zero ongoing cost.

---

## 1. Oracle Cloud Free Tier Resources

| Resource | Allocation |
|----------|-----------|
| **ARM A1 VM** | 1 instance: 2 OCPU / 12 GB RAM (half the free allowance — room to grow) |
| **Boot volume** | 47 GB (minimum, preserves storage budget) |
| **Load Balancer** | 1 Flexible LB (10 Mbps, free — handles TLS termination) |
| **Outbound data** | 10 TB/month |
| **Public IP** | 1 reserved IP |
| **VCN** | 1 virtual cloud network |

**Recommended region:** A less popular region (e.g., ap-chuncheon-1, me-jeddah-1) to avoid ARM capacity shortages.

**Critical first step:** Upgrade to Pay-As-You-Go (PAYG) immediately after account creation. This:
- Prevents idle instance reclamation (the biggest gotcha)
- Does NOT incur charges while staying within Always Free limits
- Set a budget alert at $0.01 as a safety net

---

## 2. Architecture Changes Required

### Phase 1: Multi-User Session Isolation (Critical)

**Problem:** Current `ConsensusApp` is a single shared instance — all web users see the same state.

**Solution:** Cookie-based session management with per-session app instances.

```
Browser ──cookie──→ aiohttp session middleware
                      │
                      ├─ Session A → ConsensusApp instance A → SQLite A
                      ├─ Session B → ConsensusApp instance B → SQLite B
                      └─ Session C → ConsensusApp instance C → SQLite C
```

**Implementation:**
- Add `aiohttp-session` with encrypted cookie storage
- Create a `SessionManager` class that maps session IDs to `ConsensusApp` instances
- Each session gets its own SQLite database file in a temp/data directory
- Sessions expire after configurable inactivity (e.g., 24 hours) — cleanup removes DB files
- Cap maximum concurrent sessions (e.g., 50) to prevent resource exhaustion

**Files to modify:**
- `server.py` — add session middleware, replace single `ConsensusApp` with `SessionManager`
- New: `session.py` — `SessionManager` class with TTL-based expiry and cleanup

### Phase 2: Client-Side API Key Management (Critical)

**Problem:** API keys are stored in `~/.consensus/.env` on the server. Multi-user deployment cannot share a server-side env file.

**Solution:** Keys stay in the browser, sent per-request via HTTP headers.

**Backend changes:**
- API endpoints accept an `X-API-Key` header (or keys in the JSON body per-provider)
- `ConsensusApp` and `Moderator` pass per-request keys to `AIClient` instead of reading from env
- Remove server-side key persistence for web mode (keep for desktop mode)
- Keys are **never logged, never persisted, never cached** on the server

**Frontend changes:**
- Store API keys in `sessionStorage` (cleared when tab closes) or `localStorage` (persists)
- Add a "Provider Keys" UI section where users enter their keys
- Attach keys to every API request that triggers AI generation
- Show clear indicators of which providers have keys configured

**Files to modify:**
- `ai_client.py` — accept per-call API key override
- `moderator.py` — pass keys from request context to AI client
- `app.py` — add key parameter to methods that trigger AI calls
- `server.py` — extract keys from request headers, pass through to app
- `static/app.js` — key management UI, attach keys to requests

### Phase 3: Rate Limiting & Abuse Prevention

**Implementation:**
- Per-session rate limiting middleware (e.g., 60 requests/minute)
- Maximum message length enforcement (e.g., 10,000 chars)
- Maximum concurrent AI calls per session (e.g., 1)
- Session count cap with informative "server busy" response
- Request size limit on aiohttp (already default 1MB, can tighten)

**Files to modify:**
- `server.py` — add rate limiting middleware

### Phase 4: Production Hardening

- **CORS:** Update to allow the actual domain instead of `http://host:port`
- **Security headers:** Add CSP, X-Frame-Options, X-Content-Type-Options via middleware
- **Error handling:** Ensure stack traces are not leaked to clients in production mode
- **Logging:** Structured logging with rotation (no secrets in logs)
- **Health endpoint:** `GET /health` for load balancer health checks

---

## 3. Server Stack

```
Internet
  │
  ▼
OCI Flexible Load Balancer (free, TLS termination with Let's Encrypt cert)
  │
  ▼
Caddy (reverse proxy, auto-HTTPS for direct access, static file serving)
  │   ├── /static/*  → file system (consensus/static/)
  │   └── /*         → proxy to localhost:8080
  ▼
aiohttp (consensus --web --host 127.0.0.1 --port 8080)
  │   ├── Session middleware (encrypted cookies)
  │   ├── Rate limiting middleware
  │   ├── Security headers middleware
  │   └── API handlers → per-session ConsensusApp
  ▼
SQLite (per-session databases in /opt/consensus/data/sessions/)
```

**Why Caddy instead of Nginx:**
- Zero-config automatic HTTPS via Let's Encrypt (handles cert renewal)
- Simpler configuration (3-line Caddyfile vs multi-block nginx.conf)
- Efficient static file serving
- Works well as a backup if you skip the OCI load balancer

---

## 4. VM Setup Procedure

### 4.1 Provision the Instance

1. Sign up for Oracle Cloud, select a less-popular home region
2. Upgrade to PAYG immediately, set $0.01 budget alert
3. Create ARM A1 Flex VM: 2 OCPU, 12 GB RAM, Ubuntu 24.04
4. Add SSH key, note the public IP
5. Configure VCN security list: allow TCP 80, 443 ingress from 0.0.0.0/0
6. Configure instance iptables:
   ```bash
   sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT
   sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
   sudo netfilter-persistent save
   ```

### 4.2 Install Dependencies

```bash
sudo apt update && sudo apt install -y python3-pip python3-venv git debian-keyring debian-archive-keyring apt-transport-https
# Install Caddy
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

### 4.3 Deploy Application

```bash
sudo mkdir -p /opt/consensus
sudo chown ubuntu:ubuntu /opt/consensus
cd /opt/consensus
git clone https://github.com/hherb/consensus.git .
python3 -m venv venv
source venv/bin/activate
pip install -e ".[web]"
mkdir -p data/sessions
```

### 4.4 Configure Caddy

`/etc/caddy/Caddyfile`:
```
your-domain.com {
    root * /opt/consensus/consensus/static
    file_server /static/*

    reverse_proxy /* localhost:8080

    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
    }
}
```

### 4.5 Create systemd Service

`/etc/systemd/system/consensus.service`:
```ini
[Unit]
Description=Consensus Discussion Platform
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/consensus
Environment=CONSENSUS_ENV=production
ExecStart=/opt/consensus/venv/bin/python -m consensus --web --host 127.0.0.1 --port 8080
Restart=always
RestartSec=5
MemoryMax=4G

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable consensus
sudo systemctl start consensus
sudo systemctl enable caddy
sudo systemctl start caddy
```

### 4.6 Domain & DNS

- Register a domain (or use a free subdomain service)
- Point A record to the OCI instance's public IP
- Caddy handles Let's Encrypt certificate automatically

---

## 5. Deployment Automation

Add a simple deploy script (`deploy.sh`) to the repo:

```bash
#!/bin/bash
set -e
cd /opt/consensus
git pull origin main
source venv/bin/activate
pip install -e ".[web]"
sudo systemctl restart consensus
echo "Deployed $(git rev-parse --short HEAD)"
```

---

## 6. Monitoring & Maintenance

- **Health check:** OCI LB pings `GET /health` every 30s
- **Logs:** `journalctl -u consensus -f`
- **Disk usage:** Monitor session DB growth, set up a cron job to purge expired sessions
- **Updates:** SSH in, run `deploy.sh`
- **Backups:** OCI provides free boot volume backups (5 backups included)

---

## 7. Implementation Order

| # | Task | Priority | Complexity |
|---|------|----------|------------|
| 1 | Session isolation (`SessionManager`) | Critical | Medium |
| 2 | Client-side API key management (BYOK) | Critical | Medium |
| 3 | Health endpoint | High | Trivial |
| 4 | Rate limiting middleware | High | Low |
| 5 | Security headers middleware | High | Low |
| 6 | CORS update for production domain | High | Low |
| 7 | Production logging (no secrets) | Medium | Low |
| 8 | Deploy script | Medium | Trivial |
| 9 | VM provisioning & config | Final | Manual |

**Tasks 1 and 2 are the only ones requiring significant code changes.** Everything else is configuration or small middleware additions.

---

## 8. Cost Summary

| Item | Cost |
|------|------|
| OCI VM (ARM A1, 2 OCPU / 12 GB) | Free |
| OCI Load Balancer | Free |
| OCI Storage (47 GB boot) | Free |
| OCI Bandwidth (10 TB/mo) | Free |
| Domain name | ~$10/year (or free with freenom/duckdns) |
| TLS certificate | Free (Let's Encrypt via Caddy) |
| LLM API costs | Paid by users (BYOK) |
| **Total** | **$0–10/year** |
