# Tessera

DHCP management and failover platform for [Technitium DNS Server](https://technitium.com/dns/).

Tessera monitors active DHCP health through a distributed voter quorum and
automatically fails over to a candidate server when consensus is reached. It also
provides scope synchronisation, configuration backup, drift enforcement, hot-reloadable
configuration, voter self-registration, and a web dashboard.

## Features

- **Multi-server DHCP** — supports N servers with `active`, `candidate`, and `observer` roles
- **Voter Quorum Failover** — distributed health voting with server-side cross-validation
- **Scope Sync Engine** — periodic reservation sync from active to all candidate servers
- **Backup Engine** — scheduled DHCP config snapshots with cron and retention policy
- **Enforcement Engine** — drift detection and automatic rollback
- **Authenticated Voter API** — HMAC-SHA256 signed votes with per-voter rate limiting
- **Voter Self-Registration** — one-time tokens, auto-approve, PSK rotation with grace periods
- **Hot-Reload Config** — voter keys, server list, and API token reload without restart
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
        │  └────┬────┘ └─────┬─────┘ │
        │  ┌────┴────┐ ┌─────┴─────┐ │
        │  │VoterReg │ │ ConfigWatch│ │
        │  │ Engine  │ │  Engine   │ │
        │  └─────────┘ └───────────┘ │
        └──────┬──────────────┬──────┘
               │              │
        ┌──────▼──────┐ ┌────▼───────┐ ┌────────────┐
        │   Active    │ │ Candidate  │ │  Observer   │
        │ Technitium  │ │ Technitium │ │ Technitium  │
        └─────────────┘ └────────────┘ └────────────┘

    Voters (voter-1 through voter-5)
       │
       └──→ POST /api/v1/vote (HMAC-signed, every 30s)
```

## Requirements

- Python ≥ 3.12
- Node.js ≥ 22 (frontend build only)
- [uv](https://docs.astral.sh/uv/) package manager
- Two or more Technitium DNS Server instances

---

## Installation

### 1. Docker (recommended)

```bash
git clone https://github.com/ArtificialFoundry/tessera.git
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

Write the servers file (`config/servers.json`):

```json
[
  {"name": "dns-1", "url": "https://192.0.2.1:53443", "role": "active", "priority": 0},
  {"name": "dns-2", "url": "https://192.0.2.2:53443", "role": "candidate", "priority": 10}
]
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
      - TESSERA_SERVERS_FILE=/run/secrets/servers.json
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
      - TESSERA_CONFIG_RELOAD_INTERVAL=10
    ports:
      - "8780:8780"
    volumes:
      - ./config/token:/run/secrets/token:ro
      - ./config/voters.json:/run/secrets/voters.json:ro
      - ./config/servers.json:/run/secrets/servers.json:ro
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
git clone https://github.com/ArtificialFoundry/tessera.git
cd tessera
uv sync --frozen
```

Create config files:

```bash
sudo mkdir -p /etc/tessera /var/lib/tessera/backups
sudo cp config/token /etc/tessera/token
sudo cp config/voters.json /etc/tessera/voters.json
sudo cp config/servers.json /etc/tessera/servers.json
sudo chmod 600 /etc/tessera/token
```

Run directly:

```bash
TESSERA_SERVERS_FILE=/etc/tessera/servers.json \
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
git clone https://github.com/ArtificialFoundry/tessera.git
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

## Multi-Server DHCP

Tessera supports N DHCP servers with three roles:

| Role | Description |
|------|-------------|
| `active` | The server currently serving DHCP leases |
| `candidate` | Ready to be promoted on failover (ranked by priority) |
| `observer` | Monitored for health but never promoted |

Configure servers via `TESSERA_SERVERS` (JSON string) or `TESSERA_SERVERS_FILE` (path to JSON file):

```json
[
  {"name": "dns-1", "url": "https://192.0.2.1:53443", "role": "active", "priority": 0},
  {"name": "dns-2", "url": "https://192.0.2.2:53443", "role": "candidate", "priority": 10},
  {"name": "dns-3", "url": "https://192.0.2.3:53443", "role": "candidate", "priority": 20},
  {"name": "dns-mon", "url": "https://192.0.2.4:53443", "role": "observer", "priority": 99}
]
```

**Backward compatibility:** `TESSERA_PRIMARY_URL` and `TESSERA_STANDBY_URL` still work but are deprecated. They auto-create a 2-server config with a deprecation warning.

### Failover behavior

1. Voters report the active server as DOWN
2. Quorum reached for `failover_rounds` consecutive rounds
3. Tessera promotes the highest-priority candidate to active
4. All candidate scopes are enabled
5. When active recovers, failback reverses the promotion

### Server management API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/servers` | List all servers with roles and health |
| `POST` | `/api/v1/servers/{name}/promote` | Promote a candidate to active |
| `POST` | `/api/v1/servers/{name}/demote` | Demote a server to candidate |

---

## Hot-Reload Configuration

Tessera watches config files for changes and reloads without restart.

### What's hot-reloadable

| File | Effect |
|------|--------|
| `voters.json` | New/removed voters take effect immediately |
| `servers.json` | New/removed DHCP servers (roles via API) |
| API token file | Token rotation without restart |

### What requires restart

- `TESSERA_PORT`, `TESSERA_HOST` — bind address changes
- Engine parameters (quorum, failover rounds, etc.)

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TESSERA_CONFIG_RELOAD_INTERVAL` | `10` | Seconds between file change checks |

Send `SIGHUP` to trigger an immediate reload:

```bash
kill -HUP $(pidof uvicorn)
```

---

## Voter Self-Registration

New voters can register via API without manual config edits.

### Registration flow

1. Admin generates a one-time token: `POST /api/v1/voters/tokens`
2. Admin gives token to new host (Ansible, cloud-init, SSH, etc.)
3. Host runs installer: `--auto-register --registration-token <token>`
4. If `TESSERA_AUTO_APPROVE_VOTERS=true`: PSK returned immediately
5. If `TESSERA_AUTO_APPROVE_VOTERS=false`: registration pending, admin approves

### One-time registration tokens

```bash
# Generate a token
curl -X POST http://tessera:8780/api/v1/voters/tokens \
  -H "Content-Type: application/json" \
  -d '{"bind_ip": "192.0.2.11", "ttl": 3600}'

# Token is consumed on first use — cannot be reused
```

### PSK rotation

Rotate a voter's PSK with a grace period where both old and new keys are accepted:

```bash
curl -X POST http://tessera:8780/api/v1/voters/voter-2/rotate-key
# {"voter_name": "voter-2", "new_psk": "...", "grace_period": 60}
```

### Registration API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/v1/voters/tokens` | Generate one-time registration token |
| `GET` | `/api/v1/voters/tokens` | List all registration tokens |
| `POST` | `/api/v1/voters/register` | Register with a one-time token |
| `GET` | `/api/v1/voters` | List all registered voters |
| `GET` | `/api/v1/voters/pending` | List pending registrations |
| `POST` | `/api/v1/voters/{name}/approve` | Approve a pending voter |
| `POST` | `/api/v1/voters/{name}/revoke` | Revoke a voter |
| `DELETE` | `/api/v1/voters/{name}` | Delete (revoke) a voter |
| `POST` | `/api/v1/voters/{name}/rotate-key` | Rotate PSK with grace period |

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TESSERA_REGISTRATION_TOKEN` | *(empty)* | Static registration token (bootstrap fallback) |
| `TESSERA_REGISTRATION_TOKEN_FILE` | `/etc/tessera/registration-token` | Path to registration token file |
| `TESSERA_AUTO_APPROVE_VOTERS` | `false` | Auto-approve voter registrations |
| `TESSERA_VOTER_REGISTRY_FILE` | `/var/lib/tessera/voter-registry.json` | Voter metadata store |
| `TESSERA_REGISTRATION_TOKEN_TTL` | `3600` | Default TTL for generated tokens (seconds) |
| `TESSERA_PSK_GRACE_PERIOD` | `60` | Seconds both old and new PSK are valid after rotation |

---

## Voter Setup

Voters are lightweight agents deployed on infrastructure VMs. Each voter
independently checks active DHCP health and submits a signed vote to Tessera
every 30 seconds.

### Quick install (recommended)

One command per host — handles dependencies, config, systemd, SELinux, and validation:

```bash
sudo ./voter/tessera-install-voter.sh \
  --tessera-url http://192.0.2.10:8780 \
  --active-ip 192.0.2.1
```

### Auto-register with one-time token

```bash
sudo ./voter/tessera-install-voter.sh \
  --tessera-url http://192.0.2.10:8780 \
  --active-ip 192.0.2.1 \
  --auto-register --registration-token abc123...
```

### Rotate PSK

```bash
sudo ./voter/tessera-install-voter.sh \
  --tessera-url http://192.0.2.10:8780 \
  --active-ip 192.0.2.1 \
  --rotate-key
```

The installer will:
- Auto-detect the hostname as voter name
- Generate a new HMAC PSK (or receive one via registration)
- Install `nmap` for real DHCP probing
- Create `/etc/tessera/voter.conf`
- Install the voter script to `/usr/local/bin/`
- Set up systemd timer (30s) or cron fallback (1min)
- Handle SELinux contexts on RHEL/AlmaLinux
- Check firewall rules and warn if restrictive
- Validate connectivity and submit a test vote

Supports RHEL/AlmaLinux, Debian/Ubuntu, Alpine, openSUSE, and any system
with systemd or cron.

Run `./voter/tessera-install-voter.sh --help` for all options.

### DHCP probe mode

By default, voters use `nmap --script broadcast-dhcp-discover` to send a real
DHCP DISCOVER and verify the active server responds with a DHCP OFFER.

| Method | What it checks | Requires |
|--------|---------------|----------|
| `dhcp` (default) | Actual DHCP OFFER from active | nmap + root |
| `http` | Technitium web API responds | curl |
| `both` | Both DHCP and HTTP must pass | nmap + curl + root |

---

## Configuration Reference

All settings use the `TESSERA_` prefix and can be set via environment variables,
a `.env` file, or an environment file for systemd.

| Variable | Default | Description |
|----------|---------|-------------|
| `TESSERA_SERVERS` | *(empty)* | JSON string of server configs |
| `TESSERA_SERVERS_FILE` | `/etc/tessera/servers.json` | Path to servers JSON file |
| `TESSERA_PRIMARY_URL` | *(deprecated)* | Legacy active server URL |
| `TESSERA_STANDBY_URL` | *(deprecated)* | Legacy candidate server URL |
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
| `TESSERA_BACKUP_CRON_SCHEDULE` | *(empty)* | Cron expression for auto-backup |
| `TESSERA_ENFORCEMENT_INTERVAL` | `300` | Drift enforcement check interval |
| `TESSERA_CONFIG_RELOAD_INTERVAL` | `10` | Config file watch interval |
| `TESSERA_REGISTRATION_TOKEN_TTL` | `3600` | Default registration token TTL |
| `TESSERA_PSK_GRACE_PERIOD` | `60` | PSK rotation grace period |
| `TESSERA_AUTO_APPROVE_VOTERS` | `false` | Auto-approve voter registrations |
| `TESSERA_DEBUG` | `false` | Enable debug logging |

---

## API

All endpoints are under `/api/v1/`.

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/v1/ping` | Health check |
| `GET` | `/api/v1/health` | Aggregate engine health |
| `GET` | `/api/v1/status` | Failover status and voter states |
| `POST` | `/api/v1/vote` | Submit a voter health check (HMAC-signed) |
| `GET` | `/api/v1/servers` | List all DHCP servers |
| `POST` | `/api/v1/servers/{name}/promote` | Promote to active |
| `POST` | `/api/v1/servers/{name}/demote` | Demote to candidate |
| `GET` | `/api/v1/scopes` | List all DHCP scopes |
| `GET` | `/api/v1/scopes/{name}` | Get scope details |
| `POST` | `/api/v1/scopes/{name}/enable` | Enable a scope |
| `POST` | `/api/v1/scopes/{name}/disable` | Disable a scope |
| `GET` | `/api/v1/leases` | List all leases |
| `GET` | `/api/v1/leases/{scope}` | List leases for a scope |
| `GET` | `/api/v1/backups` | List config backups |
| `POST` | `/api/v1/backups` | Create a manual backup |
| `POST` | `/api/v1/voters/tokens` | Generate registration token |
| `GET` | `/api/v1/voters/tokens` | List registration tokens |
| `POST` | `/api/v1/voters/register` | Register a new voter |
| `GET` | `/api/v1/voters` | List all voters |
| `POST` | `/api/v1/voters/{name}/approve` | Approve pending voter |
| `POST` | `/api/v1/voters/{name}/revoke` | Revoke a voter |
| `POST` | `/api/v1/voters/{name}/rotate-key` | Rotate voter PSK |

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
├── deps.py             # FastAPI DI providers
├── exceptions.py       # AppError hierarchy
├── pages.py            # Frontend SPA page serving
├── registry.py         # Engine lifecycle registry
├── api/
│   ├── schemas.py      # Pydantic response models
│   └── v1/
│       ├── backups.py      # Backup endpoints
│       ├── enforcement.py  # Enforcement endpoints
│       ├── failover.py     # Vote + status endpoints
│       ├── health.py       # Ping + health endpoints
│       ├── leases.py       # Lease endpoints
│       ├── scopes.py       # Scope CRUD endpoints
│       ├── servers.py      # Server management endpoints
│       └── voters.py       # Voter registration endpoints
├── engines/
│   ├── backup.py           # Scheduled backup + retention
│   ├── config_watcher.py   # Hot-reload config files
│   ├── enforcement.py      # Drift detection + rollback
│   ├── failover.py         # Quorum voting + failover logic
│   ├── scope_sync.py       # Active→candidate reservation sync
│   ├── technitium.py       # Technitium client + multi-server pool
│   └── voter_registry.py   # Voter registration + PSK management
├── static/             # Built frontend assets
└── templates/
    └── page.html       # SPA shell template

voter/
├── tessera-install-voter.sh # Automated installer
├── tessera-voter.sh         # Voter agent script
├── tessera-voter.service    # systemd oneshot unit
└── tessera-voter.timer      # systemd timer (30s)
```

## License

[GPLv3](LICENSE)
