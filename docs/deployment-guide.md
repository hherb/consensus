# Consensus Deployment Guide

**Zero-cost BYOK deployment: Cloudflare (DNS/CDN) + Oracle Cloud Free Tier (compute)**

Users bring their own LLM API keys. You pay nothing for hosting.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Oracle Cloud Account Setup](#2-oracle-cloud-account-setup)
3. [Create the VM Instance](#3-create-the-vm-instance)
4. [Connect to Your VM](#4-connect-to-your-vm)
5. [Server Base Setup](#5-server-base-setup)
6. [Install Consensus](#6-install-consensus)
7. [Configure the Application](#7-configure-the-application)
8. [Set Up Caddy (Reverse Proxy)](#8-set-up-caddy-reverse-proxy)
9. [Create the systemd Service](#9-create-the-systemd-service)
10. [Cloudflare DNS & CDN Setup](#10-cloudflare-dns--cdn-setup)
11. [Verify the Deployment](#11-verify-the-deployment)
12. [OAuth Setup (Optional)](#12-oauth-setup-optional)
13. [Deployment Script](#13-deployment-script)
14. [Monitoring & Maintenance](#14-monitoring--maintenance)
15. [Troubleshooting](#15-troubleshooting)
16. [Security Checklist](#16-security-checklist)

---

## 1. Prerequisites

You will need:

- **A Cloudflare account** (free) with a domain name registered or transferred to Cloudflare
- **An Oracle Cloud account** (free tier) — sign up at [cloud.oracle.com](https://cloud.oracle.com)
- **An SSH key pair** on your local machine (if you don't have one, run `ssh-keygen -t ed25519`)
- **Basic terminal/SSH familiarity**

---

## 2. Oracle Cloud Account Setup

### 2.1 Create Your Account

1. Go to [cloud.oracle.com](https://cloud.oracle.com) and click **Sign Up**
2. **Choose your home region carefully** — this cannot be changed later.
   Recommended: pick a **less popular region** to avoid ARM capacity shortages:
   - `ap-chuncheon-1` (South Korea)
   - `me-jeddah-1` (Saudi Arabia)
   - `ap-melbourne-1` (Australia)
   - `sa-saopaulo-1` (Brazil)
   - `eu-marseille-1` (France)
   - `ap-singapore-1` (Singapore — closer to you in Australia)
3. Complete the sign-up process (requires a credit card for verification — you will not be charged)

### 2.2 Upgrade to Pay-As-You-Go (CRITICAL)

> **This is the single most important step.** Without PAYG, Oracle will reclaim idle Always Free instances after a few days.

1. In the Oracle Cloud Console, go to **Billing & Cost Management** → **Upgrade and Manage Payment**
2. Click **Upgrade to Pay-As-You-Go**
3. Confirm your payment method

You will **not be charged** as long as you stay within Always Free limits. The upgrade simply prevents Oracle from reclaiming your VM.

### 2.3 Set a Budget Alert (Safety Net)

1. Go to **Billing & Cost Management** → **Budgets**
2. Click **Create Budget**
3. Set amount to **$1.00** (or even $0.01)
4. Add an alert rule at **100%** that emails you
5. This ensures you're notified immediately if anything moves beyond the free tier

---

## 3. Create the VM Instance

### 3.1 Create a Virtual Cloud Network (VCN)

1. Go to **Networking** → **Virtual Cloud Networks**
2. Click **Start VCN Wizard** → **Create VCN with Internet Connectivity** → **Start VCN Wizard**
3. Name it `consensus-vcn`, accept defaults, click **Next** → **Create**
4. Open the created VCN → **Public Subnet** → click the **Default Security List**
5. Click **Add Ingress Rules** and add these two rules:

   | Source CIDR | Protocol | Dest Port | Description |
   |-------------|----------|-----------|-------------|
   | `0.0.0.0/0` | TCP | 80 | HTTP |
   | `0.0.0.0/0` | TCP | 443 | HTTPS |

### 3.2 Create the Compute Instance

1. Go to **Compute** → **Instances** → **Create Instance**
2. Configure as follows:

   | Setting | Value |
   |---------|-------|
   | **Name** | `consensus` |
   | **Compartment** | (your root compartment) |
   | **Availability domain** | (any available) |
   | **Image** | **Canonical Ubuntu 24.04** (click *Change Image* to find it) |
   | **Shape** | Click *Change Shape* → **Ampere** → **VM.Standard.A1.Flex** |
   | **OCPUs** | **2** (of 4 free) |
   | **Memory** | **12 GB** (of 24 free) |
   | **VCN** | `consensus-vcn` |
   | **Subnet** | Public Subnet |
   | **Public IPv4** | Assign a public IPv4 address |
   | **SSH keys** | Paste your public key (`~/.ssh/id_ed25519.pub`) |

3. Click **Create** and wait for the instance to reach **Running** state
4. Note the **Public IP Address** displayed on the instance details page

> **If you get a "capacity not available" error:** Try a different availability domain, or wait and retry later. ARM capacity in popular regions can be tight. This is why we recommended a less popular region.

### 3.3 Reserve the Public IP (Prevent Changes)

By default the IP may change if the instance is stopped. To make it permanent:

1. Go to **Networking** → **IP Management** → **Reserved Public IPs**
2. Click **Reserve Public IP Address**
3. Name it `consensus-ip`
4. Under **IP Address Source**, select **Existing ephemeral public IP** and pick the one attached to your instance

---

## 4. Connect to Your VM

```bash
ssh ubuntu@<YOUR_VM_PUBLIC_IP>
```

If this is your first connection, type `yes` to accept the host key fingerprint.

> **Tip:** Add this to `~/.ssh/config` on your local machine for convenience:
> ```
> Host consensus
>     HostName <YOUR_VM_PUBLIC_IP>
>     User ubuntu
>     IdentityFile ~/.ssh/id_ed25519
> ```
> Then you can simply run `ssh consensus`.

---

## 5. Server Base Setup

All commands from here are run **on the VM** (via SSH).

### 5.1 System Updates

```bash
sudo apt update && sudo apt upgrade -y
```

### 5.2 Configure the Firewall (iptables)

Oracle Cloud Ubuntu images have iptables rules that block ports 80/443 even if the VCN security list allows them. You must open them:

```bash
sudo iptables -I INPUT 6 -p tcp --dport 80 -j ACCEPT
sudo iptables -I INPUT 6 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

Verify:
```bash
sudo iptables -L INPUT -n --line-numbers | grep -E '80|443'
```
You should see ACCEPT rules for ports 80 and 443.

### 5.3 Install Required System Packages

```bash
sudo apt install -y python3-pip python3-venv python3-dev git \
    debian-keyring debian-archive-keyring apt-transport-https curl
```

### 5.4 Install Caddy

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg

curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    | sudo tee /etc/apt/sources.list.d/caddy-stable.list

sudo apt update && sudo apt install -y caddy
```

Verify Caddy is installed:
```bash
caddy version
```

---

## 6. Install Consensus

### 6.1 Clone the Repository

```bash
sudo mkdir -p /opt/consensus
sudo chown ubuntu:ubuntu /opt/consensus
cd /opt/consensus
git clone https://github.com/hherb/consensus.git .
```

### 6.2 Create a Python Virtual Environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 6.3 Install Consensus with Web Dependencies

```bash
pip install -e ".[web,documents,memory,images]"
```

This installs:
- `aiohttp` — web server
- `pdfplumber` — PDF document support
- `sqlite-vec`, `numpy` — semantic memory & document embeddings
- `Pillow` — image support

### 6.4 Verify Installation

```bash
python -m consensus --web --host 127.0.0.1 --port 8080 &
curl -s http://127.0.0.1:8080/health
kill %1
```

You should see: `{"status": "ok", ...}`

### 6.5 Create Data Directories

```bash
mkdir -p /opt/consensus/data/sessions
mkdir -p /opt/consensus/logs
```

---

## 7. Configure the Application

### 7.1 Create the Environment File

```bash
cat > /opt/consensus/.env << 'EOF'
# === REQUIRED ===
CONSENSUS_ENV=production
CONSENSUS_BASE_URL=https://yourdomain.com
CONSENSUS_ALLOWED_ORIGINS=https://yourdomain.com
CONSENSUS_SESSION_DIR=/opt/consensus/data/sessions

# === OPTIONAL: OAuth providers ===
# Uncomment and fill in to enable OAuth login (see Section 12)
# CONSENSUS_GITHUB_CLIENT_ID=
# CONSENSUS_GITHUB_CLIENT_SECRET=
# CONSENSUS_GOOGLE_CLIENT_ID=
# CONSENSUS_GOOGLE_CLIENT_SECRET=
# CONSENSUS_LINKEDIN_CLIENT_ID=
# CONSENSUS_LINKEDIN_CLIENT_SECRET=

# === OPTIONAL: Server tuning ===
# CONSENSUS_MAX_SESSIONS=100
# CONSENSUS_SESSION_TTL=86400
# CONSENSUS_RATE_LIMIT=120
EOF
```

> **Replace `yourdomain.com`** with your actual domain name in both `CONSENSUS_BASE_URL` and `CONSENSUS_ALLOWED_ORIGINS`.

### 7.2 Protect the Environment File

```bash
chmod 600 /opt/consensus/.env
```

---

## 8. Set Up Caddy (Reverse Proxy)

### 8.1 Important: Disable Caddy's Auto-HTTPS

Since Cloudflare will handle TLS termination (HTTPS between the browser and Cloudflare), Caddy needs to handle the Cloudflare-to-origin connection. There are two options:

**Option A: HTTP-only origin (simpler, uses Cloudflare's "Flexible" or "Full" SSL)**

**Option B: HTTPS origin with Cloudflare Origin Certificate (recommended, uses "Full (Strict)" SSL)**

We'll set up **Option B** for maximum security.

### 8.2 Generate a Cloudflare Origin Certificate

1. In Cloudflare Dashboard → your domain → **SSL/TLS** → **Origin Server**
2. Click **Create Certificate**
3. Keep defaults (RSA 2048, 15 years, covers `yourdomain.com` and `*.yourdomain.com`)
4. Click **Create**
5. **Copy the Origin Certificate** (PEM) and **Private Key** — you will only see the private key once!

Save them on the server:

```bash
sudo mkdir -p /etc/caddy/certs

sudo tee /etc/caddy/certs/cloudflare-origin.pem << 'CERT'
-----BEGIN CERTIFICATE-----
(paste your origin certificate here)
-----END CERTIFICATE-----
CERT

sudo tee /etc/caddy/certs/cloudflare-origin-key.pem << 'KEY'
-----BEGIN PRIVATE KEY-----
(paste your private key here)
-----END PRIVATE KEY-----
KEY

sudo chmod 600 /etc/caddy/certs/cloudflare-origin-key.pem
sudo chown caddy:caddy /etc/caddy/certs/*
```

### 8.3 Write the Caddyfile

```bash
sudo tee /etc/caddy/Caddyfile << 'EOF'
yourdomain.com {
    # Use Cloudflare Origin Certificate (no Let's Encrypt needed)
    tls /etc/caddy/certs/cloudflare-origin.pem /etc/caddy/certs/cloudflare-origin-key.pem

    # Serve static files directly (faster than proxying)
    handle /static/* {
        root * /opt/consensus/consensus
        file_server
    }

    # Proxy everything else to the Consensus server
    handle {
        reverse_proxy localhost:8080
    }

    # Security headers (defense in depth — Consensus also sets these)
    header {
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy strict-origin-when-cross-origin
        -Server
    }

    # Access log
    log {
        output file /var/log/caddy/access.log {
            roll_size 50MiB
            roll_keep 3
        }
    }
}
EOF
```

> **Replace `yourdomain.com`** with your actual domain.

### 8.4 Create Log Directory and Restart Caddy

```bash
sudo mkdir -p /var/log/caddy
sudo chown caddy:caddy /var/log/caddy

# Validate the config
sudo caddy validate --config /etc/caddy/Caddyfile

# Restart Caddy
sudo systemctl restart caddy
sudo systemctl enable caddy
```

---

## 9. Create the systemd Service

### 9.1 Write the Service File

```bash
sudo tee /etc/systemd/system/consensus.service << 'EOF'
[Unit]
Description=Consensus Discussion Platform
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
Group=ubuntu
WorkingDirectory=/opt/consensus
EnvironmentFile=/opt/consensus/.env
ExecStart=/opt/consensus/venv/bin/python -m consensus --web --multi-user --host 127.0.0.1 --port 8080
Restart=always
RestartSec=5

# Resource limits
MemoryMax=8G
TasksMax=256

# Security hardening
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/opt/consensus/data /opt/consensus/logs /tmp
PrivateTmp=yes

[Install]
WantedBy=multi-user.target
EOF
```

### 9.2 Enable and Start the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable consensus
sudo systemctl start consensus
```

### 9.3 Verify It's Running

```bash
# Check service status
sudo systemctl status consensus

# Check the health endpoint
curl -s http://127.0.0.1:8080/health

# Watch logs (Ctrl+C to stop)
journalctl -u consensus -f
```

You should see `{"status": "ok", ...}` from the health check.

---

## 10. Cloudflare DNS & CDN Setup

### 10.1 Point Your Domain to the VM

1. Go to the **Cloudflare Dashboard** → select your domain
2. Go to **DNS** → **Records**
3. Add an **A record**:

   | Type | Name | Content | Proxy status | TTL |
   |------|------|---------|-------------|-----|
   | A | `@` | `<YOUR_VM_PUBLIC_IP>` | **Proxied** (orange cloud) | Auto |

4. If you also want `www`:

   | Type | Name | Content | Proxy status | TTL |
   |------|------|---------|-------------|-----|
   | CNAME | `www` | `yourdomain.com` | **Proxied** | Auto |

> The **orange cloud (Proxied)** means traffic goes through Cloudflare's network. This gives you free CDN caching, DDoS protection, and hides your server's real IP.

### 10.2 Configure SSL/TLS

1. Go to **SSL/TLS** → **Overview**
2. Set encryption mode to **Full (strict)**

   This means:
   - Browser ↔ Cloudflare: encrypted (Cloudflare's certificate)
   - Cloudflare ↔ Your server: encrypted (Cloudflare Origin Certificate from Step 8.2)

### 10.3 Enable Recommended Cloudflare Settings

Go to **SSL/TLS** → **Edge Certificates**:
- **Always Use HTTPS**: ON
- **Minimum TLS Version**: TLS 1.2
- **Automatic HTTPS Rewrites**: ON

Go to **Speed** → **Optimization**:
- **Auto Minify**: check CSS and JS
- **Brotli**: ON

Go to **Caching** → **Configuration**:
- **Browser Cache TTL**: Respect Existing Headers (or 4 hours)

Go to **Security** → **Settings**:
- **Security Level**: Medium
- **Challenge Passage**: 30 minutes
- **Browser Integrity Check**: ON

### 10.4 Cache Rules (Optional but Recommended)

Create a cache rule for static assets:

1. Go to **Caching** → **Cache Rules** → **Create Rule**
2. Name: `Cache static assets`
3. When: `URI Path starts with /static/`
4. Then: **Eligible for cache**, Edge TTL = 1 day, Browser TTL = 4 hours
5. **Deploy**

API requests (`/api/*`, `/auth/*`) are not cached by default since they return `application/json`, which is fine.

---

## 11. Verify the Deployment

### 11.1 Quick Checks (from your local machine)

```bash
# Health check through Cloudflare
curl -s https://yourdomain.com/health

# Check HTTP->HTTPS redirect
curl -sI http://yourdomain.com | head -5

# Check security headers
curl -sI https://yourdomain.com | grep -iE 'x-frame|x-content|referrer|server|cf-ray'
```

Expected results:
- Health endpoint returns `{"status": "ok", ...}`
- HTTP redirects to HTTPS (301/302)
- Security headers present, `cf-ray` header confirms Cloudflare proxying
- No `Server` header leaking backend info

### 11.2 Full Test

1. Open `https://yourdomain.com` in your browser
2. You should see the Consensus registration/login page (multi-user mode)
3. Register an account
4. Add an LLM provider (e.g., OpenRouter) with your own API key
5. Create a discussion and verify AI responses work

---

## 12. OAuth Setup (Optional)

OAuth allows users to log in with GitHub, Google, etc. instead of email/password.

### 12.1 GitHub OAuth

1. Go to [github.com/settings/developers](https://github.com/settings/developers)
2. Click **New OAuth App**
3. Fill in:
   - **Application name**: Consensus
   - **Homepage URL**: `https://yourdomain.com`
   - **Authorization callback URL**: `https://yourdomain.com/auth/callback/github`
4. Click **Register Application**
5. Copy the **Client ID** and generate a **Client Secret**
6. Add to `/opt/consensus/.env`:
   ```
   CONSENSUS_GITHUB_CLIENT_ID=your_client_id
   CONSENSUS_GITHUB_CLIENT_SECRET=your_client_secret
   ```
7. Restart: `sudo systemctl restart consensus`

### 12.2 Google OAuth

1. Go to [console.cloud.google.com/apis/credentials](https://console.cloud.google.com/apis/credentials)
2. Create a project (or select an existing one)
3. Click **Create Credentials** → **OAuth client ID**
4. Application type: **Web application**
5. Add Authorized redirect URI: `https://yourdomain.com/auth/callback/google`
6. Copy the **Client ID** and **Client Secret**
7. Add to `/opt/consensus/.env`:
   ```
   CONSENSUS_GOOGLE_CLIENT_ID=your_client_id
   CONSENSUS_GOOGLE_CLIENT_SECRET=your_client_secret
   ```
8. Restart: `sudo systemctl restart consensus`

---

## 13. Deployment Script

Create a script for easy updates:

```bash
cat > /opt/consensus/deploy.sh << 'SCRIPT'
#!/bin/bash
set -e

echo "=== Deploying Consensus ==="
cd /opt/consensus

# Pull latest code
git pull origin main

# Update dependencies
source venv/bin/activate
pip install -e ".[web,documents,memory,images]"

# Restart the service (migrations run automatically on startup)
sudo systemctl restart consensus

# Wait for startup and verify
sleep 3
if curl -sf http://127.0.0.1:8080/health > /dev/null; then
    echo "=== Deploy successful: $(git rev-parse --short HEAD) ==="
else
    echo "=== DEPLOY FAILED — checking logs ==="
    journalctl -u consensus --no-pager -n 20
    exit 1
fi
SCRIPT

chmod +x /opt/consensus/deploy.sh
```

To deploy updates:
```bash
ssh consensus
cd /opt/consensus && ./deploy.sh
```

---

## 14. Monitoring & Maintenance

### 14.1 Service Logs

```bash
# Follow live logs
journalctl -u consensus -f

# Last 100 lines
journalctl -u consensus -n 100 --no-pager

# Logs since last boot
journalctl -u consensus -b

# Caddy access logs
sudo tail -f /var/log/caddy/access.log
```

### 14.2 Disk Usage

```bash
# Overall disk usage
df -h /

# Session database sizes
du -sh /opt/consensus/data/sessions/

# Count active session DBs
ls /opt/consensus/data/sessions/*.db 2>/dev/null | wc -l
```

Sessions auto-expire after 24 hours of inactivity (the `SessionManager` handles cleanup).

### 14.3 Automatic Security Updates

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

Select **Yes** to automatically install security updates.

### 14.4 Optional: Cron Job for Session Cleanup

Sessions are cleaned up automatically by the app, but as a safety net you can add a cron job to remove stale DB files older than 3 days:

```bash
(crontab -l 2>/dev/null; echo "0 4 * * * find /opt/consensus/data/sessions -name '*.db' -mtime +3 -delete") | crontab -
```

### 14.5 Resource Monitoring

```bash
# Memory usage
free -h

# CPU usage
top -bn1 | head -20

# Open connections
ss -tuln | grep -E '80|443|8080'
```

---

## 15. Troubleshooting

### "Out of capacity" when creating the VM

ARM instances in popular regions fill up fast. Solutions:
- Try different availability domains within your region
- Try creating at off-peak hours (early morning US time)
- If persistent, submit a service limit increase request
- Last resort: use the x86 Always Free shapes (VM.Standard.E2.1.Micro — 1 GB RAM, much more limited)

### VM created but site not reachable

Check in this order:
1. **iptables**: `sudo iptables -L INPUT -n | grep -E '80|443'` — must show ACCEPT rules
2. **VCN Security List**: check ingress rules in Oracle Console include ports 80, 443
3. **Caddy running**: `sudo systemctl status caddy`
4. **Consensus running**: `sudo systemctl status consensus`
5. **Local test**: `curl http://127.0.0.1:8080/health` (SSH into VM first)

### Caddy won't start

```bash
# Check config syntax
sudo caddy validate --config /etc/caddy/Caddyfile

# Check logs
journalctl -u caddy -n 50 --no-pager
```

Common causes:
- Typo in domain name in Caddyfile
- Origin certificate files not readable by caddy user
- Port 80/443 already bound by another process (`sudo ss -tuln | grep ':80\|:443'`)

### Consensus starts but BYOK doesn't work

- Check `CONSENSUS_BASE_URL` and `CONSENSUS_ALLOWED_ORIGINS` in `.env` match your actual domain (including `https://`)
- Check browser console for CORS errors
- Verify the `X-API-Keys` header is being sent (browser DevTools → Network tab)

### Cloudflare shows 502 or 521 errors

- **502 Bad Gateway**: Caddy is running but Consensus isn't. Check `sudo systemctl status consensus`
- **521 Web Server Is Down**: Nothing is listening on the origin. Check both Caddy and iptables

### Instance reclaimed / stopped by Oracle

This happens if you didn't upgrade to PAYG. If your instance disappears:
1. Upgrade to PAYG immediately
2. Recreate the instance
3. Restore from boot volume backup if you had one

---

## 16. Security Checklist

Before going live, verify:

- [ ] PAYG upgrade completed (prevents instance reclamation)
- [ ] Budget alert set at $1 or less
- [ ] `.env` file has `chmod 600`
- [ ] `CONSENSUS_ENV=production` is set
- [ ] `CONSENSUS_BASE_URL` matches your actual domain
- [ ] `CONSENSUS_ALLOWED_ORIGINS` matches your actual domain
- [ ] Cloudflare SSL mode is **Full (strict)**
- [ ] Cloudflare "Always Use HTTPS" is **ON**
- [ ] Origin certificate key file is `chmod 600`
- [ ] No API keys are stored on the server
- [ ] VM SSH uses key-based auth (no password auth)
- [ ] iptables only opens ports 22, 80, 443
- [ ] `unattended-upgrades` is enabled
- [ ] Health endpoint responds at `https://yourdomain.com/health`
- [ ] Test registration, login, and a BYOK discussion end-to-end

---

## Cost Summary

| Item | Cost |
|------|------|
| Oracle Cloud VM (ARM A1, 2 OCPU / 12 GB) | **Free** |
| Oracle Cloud Storage (47 GB boot volume) | **Free** |
| Oracle Cloud Bandwidth (10 TB/month) | **Free** |
| Cloudflare DNS + CDN + DDoS protection | **Free** |
| Cloudflare SSL/TLS (origin certificate) | **Free** |
| Domain name (via Cloudflare Registrar) | Already purchased |
| LLM API costs | **Paid by users (BYOK)** |
| **Total ongoing cost** | **$0/month** |
