# Tessera

DHCP management and failover platform for [Technitium DNS Server](https://technitium.com/dns/).

Tessera monitors primary DHCP health through a distributed voter quorum and
automatically fails over to a standby server when consensus is reached. It also
provides scope synchronisation, configuration backup, drift enforcement, and a
web dashboard.

## Features

- **Voter Quorum Failover** — distributed health voting with server-side cross-validation
- **Scope Sync Engine** — periodic reservation sync from primary to standby
- **Backup Engine** — scheduled DHCP config snapshots with cron and retention policy
- **Enforcement Engine** — drift detection and automatic rollback
- **Authenticated Voter API** — HMAC-SHA256 signed votes with per-voter rate limiting
- **DHCP Proxy API** — typed REST endpoints for scopes, leases, and reservations
- **Web Dashboard** — Preact SPA with real-time failover status and DHCP management

## Architecture

```
                  ┌──────────┐
                  │  Traefik │ ← TLS termination
                  └────┬─────┘
                       │
                  ┌────▼─────┐
                  │   O2P    │ ← SSO (Keycloak)
                  └────┬─────┘
                       │
        ┌──────────────▼──────────────┐
        │          Tessera            │
        │  ┌─────────┐ ┌───────────┐ │
        │  │Failover │ │ ScopeSync │ │
        │  │ Engine  │ │  Engine   │ │
        │  └────┬────┘ └─────┬─────┘ │
        │  ┌────┴────┐ ┌─────┴─────┐ │
        │  │ Backup  │ │Enforcement│ │
        │  │ Engine  │ │  Engine   │ │
        │  └─────────┘ └───────────┘ │
        └──────┬──────────────┬──────┘
               │              │
        ┌──────▼──────┐ ┌────▼───────┐
        │  Primary    │ │  Standby   │
        │ Technitium  │ │ Technitium │
        └─────────────┘ └────────────┘

    Voters (voter-1 through voter-5)
       │
       └──→ POST /api/v1/vote (HMAC-signed, every 30s)
```

## Requirements

- Python ≥ 3.12
- Node.js ≥ 22 (frontend build only)
- [uv](https://docs.astral.sh/uv/) package manager
- Two Technitium DNS Server instances (primary + standby)

---

## Installation

### 1. Docker (recommended)

```bash
git clone https://gitea.example.com/ArtificialFoundry/tessera.git
cd tessera
```

Create the config directory:

```bash
mkdir -p config data/backups
```

Write the Technitium API token:

```bash
echo "your-technitium-api-token" > config/token
chmod 600 config/token
```

Write the voter keys file (`config/voters.json`):

```json
{
  "voter-1": "hmac-psk-for-voter-1",
  "voter-2": "hmac-psk-for-voter-2",
  "voter-3": "hmac-psk-for-voter-3",
  "voter-4": "hmac-psk-for-voter-4",
  "voter-5": "hmac-psk-for-voter-5"
}
```

Generate voter PSKs with:

```bash
openssl rand -hex 32
```

Create `compose.yml`:

```yaml
name: tessera
networks:
  net-tessera:
    name: net-tessera

services:
  app:
    build: .
    image: tessera:latest
    container_name: tessera
    restart: unless-stopped
    environment:
      - TESSERA_PRIMARY_URL=https://primary-dns:53443
      - TESSERA_STANDBY_URL=https://standby-dns:53443
      - TESSERA_API_TOKEN_FILE=/run/secrets/token
      - TESSERA_VOTER_KEYS_FILE=/run/secrets/voters.json
      - TESSERA_PORT=8780
      - TESSERA_VOTERS=voter-1,voter-2,voter-3,voter-4,voter-5
      - TESSERA_QUORUM=3
      - TESSERA_FAILOVER_ROUNDS=3
      - TESSERA_FAILBACK_ROUNDS=5
      - TESSERA_VOTE_TTL=90
      - TESSERA_SYNC_INTERVAL=300
      - TESSERA_BACKUP_DIR=/var/lib/tessera/backups
      - TESSERA_MAX_BACKUPS=50
      - TESSERA_BACKUP_CRON_SCHEDULE=0 */6 * * *
      - TESSERA_ENFORCEMENT_INTERVAL=300
    ports:
      - "8780:8780"
    volumes:
      - ./config/token:/run/secrets/token:ro
      - ./config/voters.json:/run/secrets/voters.json:ro
      - ./data/backups:/var/lib/tessera/backups
    networks:
      - net-tessera
```

Build and start:

```bash
docker compose build
docker compose up -d
```

Verify:

```bash
curl -s http://localhost:8780/api/v1/ping
# {"status":"ok"}
```

### 2. Bare metal / systemd

```bash
git clone https://gitea.example.com/ArtificialFoundry/tessera.git
cd tessera
uv sync --frozen
```

Create config files:

```bash
sudo mkdir -p /etc/tessera /var/lib/tessera/backups
sudo cp config/token /etc/tessera/token
sudo cp config/voters.json /etc/tessera/voters.json
sudo chmod 600 /etc/tessera/token
```

Run directly:

```bash
TESSERA_PRIMARY_URL=https://primary-dns:53443 \
TESSERA_STANDBY_URL=https://standby-dns:53443 \
uv run uvicorn tessera.app:create_app --factory --host 0.0.0.0 --port 8780
```

Or create a systemd service (`/etc/systemd/system/tessera.service`):

```ini
[Unit]
Description=Tessera DHCP failover platform
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=tessera
Group=tessera
WorkingDirectory=/opt/tessera
ExecStart=/opt/tessera/.venv/bin/uvicorn tessera.app:create_app --factory --host 0.0.0.0 --port 8780
Restart=on-failure
RestartSec=5
EnvironmentFile=/etc/tessera/tessera.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo useradd -r -s /usr/sbin/nologin tessera
sudo chown -R tessera:tessera /var/lib/tessera
sudo systemctl daemon-reload
sudo systemctl enable --now tessera
```

### 3. Development

```bash
git clone https://gitea.example.com/ArtificialFoundry/tessera.git
cd tessera
uv sync --all-extras

# Backend
uv run uvicorn tessera.app:create_app --factory --reload --port 8780

# Frontend (separate terminal)
cd frontend
npm ci
npm run dev
```

---

## Voter Setup

Voters are lightweight agents deployed on infrastructure VMs. Each voter
independently checks primary DHCP health and submits a signed vote to Tessera
every 30 seconds.

### Quick install (recommended)

One command per host — handles dependencies, config, systemd, SELinux, and validation:

```bash
sudo ./voter/tessera-install-voter.sh \
  --tessera-url http://192.0.2.10:8780 \
  --primary-ip 192.0.2.1
```

The installer will:
- Auto-detect the hostname as voter name
- Generate a new HMAC PSK (printed for you to add to `voters.json`)
- Install `nmap` for real DHCP probing
- Create `/etc/tessera/voter.conf`
- Install the voter script to `/usr/local/bin/`
- Set up systemd timer (30s) or cron fallback (1min)
- Handle SELinux contexts on RHEL/AlmaLinux
- Check firewall rules and warn if restrictive
- Validate connectivity and submit a test vote

Supports RHEL/AlmaLinux, Debian/Ubuntu, Alpine, openSUSE, and any system
with systemd or cron.

#### Full options

```bash
sudo ./voter/tessera-install-voter.sh \
  --name voter-2    \
  --tessera-url http://192.0.2.10:8780 \
  --primary-ip 192.0.2.1 \
  --psk "$(openssl rand -hex 32)" \
  --check-method both \
  --interface eth0
```

Run `./voter/tessera-install-voter.sh --help` for all options.

#### Idempotent re-runs

Safe to re-run — updates config and restarts services without duplicating anything:

```bash
# Update Tessera URL
sudo ./voter/tessera-install-voter.sh \
  --tessera-url http://new-tessera-host:8780 \
  --primary-ip 192.0.2.1
```

#### Uninstall

```bash
sudo ./voter/tessera-install-voter.sh --uninstall
```

### Manual install

If you prefer to set things up by hand:

```bash
# 1. Copy files
sudo cp voter/tessera-voter.sh /usr/local/bin/tessera-voter.sh
sudo chmod +x /usr/local/bin/tessera-voter.sh
sudo cp voter/tessera-voter.service voter/tessera-voter.timer /etc/systemd/system/

# 2. Configure
sudo mkdir -p /etc/tessera
sudo tee /etc/tessera/voter.conf <<EOF
VOTER_NAME="$(hostname -s)"
VOTER_PSK="$(openssl rand -hex 32)"
TESSERA_URL="http://192.0.2.10:8780"
PRIMARY_IP="192.0.2.1"
PRIMARY_PORT=53443
CHECK_TIMEOUT=5
CHECK_METHOD="dhcp"
DHCP_INTERFACE=""
EOF
sudo chmod 600 /etc/tessera/voter.conf

# 3. Enable
sudo systemctl daemon-reload
sudo systemctl enable --now tessera-voter.timer
```

### DHCP probe mode

By default, voters use `nmap --script broadcast-dhcp-discover` to send a real
DHCP DISCOVER and verify the primary server responds with a DHCP OFFER. This
proves the DHCP service is alive — not just the web API.

The installer handles nmap installation automatically. For manual installs:

```bash
sudo dnf install nmap    # RHEL/AlmaLinux
sudo apt install nmap    # Debian/Ubuntu
```

| Method | What it checks | Requires |
|--------|---------------|----------|
| `dhcp` (default) | Actual DHCP OFFER from primary | nmap + root |
| `http` | Technitium web API responds | curl |
| `both` | Both DHCP and HTTP must pass | nmap + curl + root |

If nmap isn't installed and `CHECK_METHOD="dhcp"`, the voter falls back to HTTP
automatically.

---

## Configuration Reference

All settings use the `TESSERA_` prefix and can be set via environment variables,
a `.env` file, or an environment file for systemd.

| Variable | Default | Description |
|----------|---------|-------------|
| `TESSERA_PRIMARY_URL` | `https://192.0.2.1:53443` | Primary Technitium server URL |
| `TESSERA_STANDBY_URL` | `https://192.0.2.2:53443` | Standby Technitium server URL |
| `TESSERA_API_TOKEN_FILE` | `/etc/tessera/token` | Path to Technitium API token file |
| `TESSERA_VOTER_KEYS_FILE` | `/etc/tessera/voters.json` | Path to voter HMAC PSK JSON file |
| `TESSERA_PORT` | `8780` | HTTP listen port |
| `TESSERA_HOST` | `0.0.0.0` | HTTP bind address |
| `TESSERA_QUORUM` | `3` | Minimum votes for quorum |
| `TESSERA_FAILOVER_ROUNDS` | `3` | Consecutive failed rounds before failover |
| `TESSERA_FAILBACK_ROUNDS` | `5` | Consecutive healthy rounds before failback |
| `TESSERA_VOTE_TTL` | `90` | Vote expiry in seconds |
| `TESSERA_VOTERS` | *(comma-separated)* | Expected voter names |
| `TESSERA_SYNC_INTERVAL` | `300` | Scope sync interval in seconds |
| `TESSERA_BACKUP_DIR` | `/var/lib/tessera/backups` | Backup storage directory |
| `TESSERA_MAX_BACKUPS` | `50` | Maximum retained backups |
| `TESSERA_BACKUP_CRON_SCHEDULE` | *(empty)* | Cron expression for auto-backup (e.g. `0 */6 * * *`) |
| `TESSERA_ENFORCEMENT_INTERVAL` | `300` | Drift enforcement check interval in seconds |
| `TESSERA_DEBUG` | `false` | Enable debug logging (human-readable format) |

---

## API

All endpoints are under `/api/v1/`. Full OpenAPI docs available at `/docs` when running.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/ping` | Health check |
| `GET` | `/api/v1/status` | Failover status, voter states, engine health |
| `POST` | `/api/v1/vote` | Submit a voter health check (HMAC-signed) |
| `GET` | `/api/v1/scopes` | List all DHCP scopes |
| `GET` | `/api/v1/scopes/{name}` | Get scope details |
| `POST` | `/api/v1/scopes/{name}/enable` | Enable a scope |
| `POST` | `/api/v1/scopes/{name}/disable` | Disable a scope |
| `GET` | `/api/v1/leases` | List leases for all scopes |
| `GET` | `/api/v1/leases/{scope}` | List leases for a specific scope |
| `GET` | `/api/v1/backups` | List config backups |
| `GET` | `/api/v1/backups/{id}` | Get backup details |
| `POST` | `/api/v1/backups` | Create a manual backup |

---

## Development

```bash
uv run pytest                          # Tests (must pass)
uv run ruff check src/ tests/          # Lint (must be clean)
uv run ruff format --check src/ tests/ # Format check
uv run mypy src/                       # Type check (strict)
```

### Project structure

```
src/tessera/
├── app.py              # FastAPI factory + lifespan
├── config.py           # Pydantic settings (env vars)
├── database.py         # Database placeholder
├── deps.py             # FastAPI DI providers
├── exceptions.py       # AppError hierarchy
├── pages.py            # Frontend SPA page serving
├── registry.py         # Engine lifecycle registry
├── api/
│   ├── schemas.py      # Pydantic response models
│   └── v1/
│       ├── backups.py  # Backup endpoints
│       ├── failover.py # Vote + status endpoints
│       ├── health.py   # Ping endpoint
│       ├── leases.py   # Lease endpoints
│       └── scopes.py   # Scope CRUD endpoints
├── engines/
│   ├── backup.py       # Scheduled backup + retention
│   ├── enforcement.py  # Drift detection + rollback
│   ├── failover.py     # Quorum voting + failover logic
│   ├── scope_sync.py   # Primary→standby reservation sync
│   └── technitium.py   # Technitium API client
├── static/             # Built frontend assets
└── templates/
    └── page.html       # SPA shell template

voter/
├── tessera-install-voter.sh # Automated installer (recommended)
├── tessera-voter.sh         # Voter agent script
├── tessera-voter.service    # systemd oneshot unit
└── tessera-voter.timer      # systemd timer (30s)
```

## License

[GPLv3](LICENSE)
