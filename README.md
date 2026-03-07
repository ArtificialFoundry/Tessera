# Tessera

DHCP management and failover platform for [Technitium DNS Server](https://technitium.com/dns/).

Tessera monitors active DHCP health through a distributed voter quorum and
automatically fails over to a candidate server when consensus is reached. It also
provides scope synchronisation, configuration backup, drift enforcement, hot-reloadable
configuration, voter self-registration, and a web dashboard.

## Features

- **Multi-server DHCP** — supports N servers with `active`, `candidate`, and `observer` roles
- **Voter Quorum Failover** — distributed health voting with server-side cross-validation
- **Dual Health Checks** — HTTP API probe + DHCP broadcast probe with per-check UI badges
- **Scope Sync Engine** — periodic reservation sync from active to all candidate servers
- **Backup Engine** — scheduled DHCP config snapshots with cron and retention policy
- **Enforcement Engine** — drift detection and automatic rollback
- **Admin Authentication** — Bearer token auth for write operations; client-side token dialog with server-side verification
- **Authenticated Voter API** — HMAC-SHA256 signed votes with per-voter rate limiting
- **Voter Self-Registration** — one-time tokens with optional IP binding (IPv4/IPv6/CIDR), auto-approve, PSK rotation with grace periods
- **Hot-Reload Config** — voter keys, server list, and API token reload without restart
- **DHCP Proxy API** — typed REST endpoints for scopes, leases, and reservations
- **Web Dashboard** — Preact SPA with real-time failover status, health check badges, stale data warnings, and DHCP management

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
        │  └────┬────┘ └─────┴─────┘ │
        │  ┌────┴────┐ ┌───────────┐ │
        │  │VoterReg │ │ConfigWatch│ │
        │  │ Engine  │ │  Engine   │ │
        │  └─────────┘ └───────────┘ │
        └──────┬──────────────┬──────┘
               │              │
        ┌──────▼──────┐ ┌────▼───────┐ ┌────────────┐
        │   Active    │ │ Candidate  │ │  Observer   │
        │ Technitium  │ │ Technitium │ │ Technitium  │
        └─────────────┘ └────────────┘ └────────────┘

    Voters (voter-1 through voter-N)
       │
       ├──→ HTTP check (Technitium API ping)
       ├──→ DHCP check (nmap broadcast-dhcp-discover)
       └──→ POST /api/v1/vote (HMAC-signed, every 30s)
              includes http_status + dhcp_status
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
  "voter-3": "hmac-psk-for-voter-3"
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

Generate an admin API key:

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
      - TESSERA_ADMIN_API_KEY=<your-admin-key-here>
      - TESSERA_PORT=8780
      - TESSERA_VOTERS=voter-1,voter-2,voter-3
      - TESSERA_QUORUM=2
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
TESSERA_ADMIN_API_KEY=$(openssl rand -hex 32) \
uv run uvicorn tessera.app:create_app --factory --host 0.0.0.0 --port 8780
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

## Admin Authentication

Tessera uses a Bearer token for all write/admin operations. Read operations (listing voters, servers, status, scopes) are unauthenticated.

### Setup

Set `TESSERA_ADMIN_API_KEY` in your environment or compose file:

```bash
export TESSERA_ADMIN_API_KEY=$(openssl rand -hex 32)
```

### Web UI

The dashboard prompts for the admin token when you first perform a write action (promote, demote, add/remove server, approve voter, etc.). The token is stored in `sessionStorage` and cleared when the tab closes. A 🔓 Logout button appears in the nav bar when authenticated.

The token is verified server-side via `POST /api/v1/auth/verify` before being accepted.

### API usage

```bash
# Read (no auth required)
curl -s http://tessera:8780/api/v1/servers

# Write (Bearer token required)
curl -X POST http://tessera:8780/api/v1/servers/dns-2/promote \
  -H "Authorization: Bearer <your-admin-key>"
```

---

## Multi-Server DHCP

Tessera supports N DHCP servers with three roles:

| Role | Description |
|------|-------------|
| `active` | The server currently serving DHCP leases |
| `candidate` | Ready to be promoted on failover (ranked by priority) |
| `observer` | Monitored for health but never promoted |

Configure servers via `TESSERA_SERVERS_FILE` (path to JSON file):

```json
[
  {"name": "dns-1", "url": "https://192.0.2.1:53443", "role": "active", "priority": 0},
  {"name": "dns-2", "url": "https://192.0.2.2:53443", "role": "candidate", "priority": 10},
  {"name": "dns-3", "url": "https://192.0.2.3:53443", "role": "candidate", "priority": 20},
  {"name": "dns-mon", "url": "https://192.0.2.4:53443", "role": "observer", "priority": 99}
]
```

Servers can optionally include a per-server `token` field to override the global Technitium API token:

```json
{"name": "dns-3", "url": "https://192.0.2.3:53443", "role": "candidate", "priority": 20, "token": "per-server-api-token"}
```

### Server management API

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/v1/servers` | — | List all servers with roles and health |
| `POST` | `/api/v1/servers` | Bearer | Add a new server to the pool |
| `DELETE` | `/api/v1/servers/{name}` | Bearer | Remove a server from the pool |
| `POST` | `/api/v1/servers/{name}/promote` | Bearer | Promote a candidate to active |
| `POST` | `/api/v1/servers/{name}/demote` | Bearer | Demote a server to candidate |

### Failover behavior

1. Voters report the active server as DOWN
2. Quorum reached for `failover_rounds` consecutive rounds
3. Tessera promotes the highest-priority candidate to active
4. All candidate scopes are enabled
5. When active recovers, failback reverses the promotion

---

## Dual Health Checks

Voters perform two independent health checks on the active DHCP server:

| Check | Method | What it proves | Requires |
|-------|--------|---------------|----------|
| **HTTP** (primary) | `curl` to Technitium API | Management API is responsive | curl |
| **DHCP** (secondary) | `nmap broadcast-dhcp-discover` | Server is actually issuing DHCP leases | nmap, root |

Both check results are submitted with each vote:

```json
{
  "voter": "voter-1",
  "status": "up",
  "timestamp": 1741380000,
  "signature": "...",
  "http_status": "up",
  "dhcp_status": "down"
}
```

**Overall vote logic:** `"up"` if either check passes, `"down"` only if both fail.

### UI badges

The dashboard shows per-check pill badges on voter cards:

- `HTTP ✓` `DHCP ✓` — fully healthy
- `HTTP ✓` `DHCP ✗` — API up but DHCP probe failed (may not be serving leases)
- `HTTP ✗` `DHCP ✓` — management plane down but DHCP still serving
- `HTTP ✗` `DHCP ✗` — server unreachable

Badges appear on:
- **Failover page** — voter cards and voter detail modal
- **Voters page** — live status column per voter row

The voter detail modal on the failover page also shows contextual explanations for mixed states.

### Backward compatibility

The `http_status` and `dhcp_status` fields are optional. Voters running older scripts (without dual checks) still work — badges simply don't appear.

---

## Voter Setup

Voters are lightweight bash agents deployed on infrastructure VMs. Each voter
independently checks active DHCP health and submits a signed vote to Tessera
every 30 seconds.

### Docker (recommended)

Create a `voter.conf`:

```bash
VOTER_NAME="voter-1"
VOTER_PSK="<hex-psk-from-registration>"
TESSERA_URL="http://tessera-host:8780"
CHECK_TIMEOUT="5"
DHCP_TIMEOUT="5"
```

Run with Docker Compose:

```bash
cd voter
cp /path/to/your/voter.conf ./voter.conf
docker compose up -d
```

Or run directly:

```bash
docker build -f voter/Dockerfile -t tessera-voter:latest .

docker run -d \
  --name tessera-voter \
  --network host \
  --cap-add NET_RAW \
  --cap-add NET_ADMIN \
  --restart unless-stopped \
  -v /path/to/voter.conf:/etc/tessera/voter.conf:ro \
  -e VOTER_INTERVAL=30 \
  tessera-voter:latest
```

> **Note:** `--network host` and `NET_RAW`/`NET_ADMIN` capabilities are required for the
> DHCP broadcast probe (nmap). The container runs as root to send raw packets.

### Bare metal — quick install

```bash
sudo ./voter/tessera-install-voter.sh \
  --tessera-url http://192.0.2.10:8780 \
  --active-ip 192.0.2.1
```

### Bare metal — auto-register with one-time token

```bash
sudo ./voter/tessera-install-voter.sh \
  --tessera-url http://192.0.2.10:8780 \
  --auto-register --registration-token abc123...
```

The installer will:
- Auto-detect the hostname as voter name
- Generate a new HMAC PSK (or receive one via registration)
- Install `nmap` for DHCP broadcast probing
- Create `/etc/tessera/voter.conf`
- Install the voter script to `/usr/local/bin/tessera-voter.sh`
- Set up systemd timer (30s) or cron fallback (1min)
- Handle SELinux contexts on RHEL/AlmaLinux
- Validate connectivity and submit a test vote

### Voter configuration

The voter reads `/etc/tessera/voter.conf`:

```bash
VOTER_NAME="voter-1"
VOTER_PSK="hmac-psk-hex-string"
TESSERA_URL="http://192.0.2.10:8780"
CHECK_TIMEOUT="5"
DHCP_TIMEOUT="10"
DHCP_INTERFACE="eth0"
```

| Variable | Default | Description |
|----------|---------|-------------|
| `VOTER_NAME` | *(required)* | Voter identifier |
| `VOTER_PSK` | *(required)* | HMAC-SHA256 pre-shared key (hex) |
| `TESSERA_URL` | *(required)* | Tessera API base URL |
| `CHECK_TIMEOUT` | `5` | HTTP check timeout in seconds |
| `DHCP_TIMEOUT` | `CHECK_TIMEOUT` | nmap DHCP broadcast probe timeout in seconds |
| `DHCP_INTERFACE` | *(auto-detect)* | Network interface for DHCP broadcast probe |

**`DHCP_TIMEOUT`** — increase this on hosts where the DHCP broadcast response must traverse VLANs or where CPU contention causes occasional timeouts (e.g., `DHCP_TIMEOUT="10"` for busy VMs).

### How voting works

1. Voter fetches the active server from `GET /api/v1/servers`
2. **HTTP check:** `curl` to Technitium API — expects 200/401/403
3. **DHCP check:** `nmap broadcast-dhcp-discover` — looks for the active server's IP in the response
4. Signs `VOTER_NAME|STATUS|TIMESTAMP` with HMAC-SHA256
5. `POST /api/v1/vote` with both check results

If the Tessera API returns no active server, the voter submits a `"down"` vote with target `no-active-server`.

---

## Voter Self-Registration

New voters can register via API without manual config edits.

### Registration flow

1. Admin generates a one-time token via the dashboard wizard or API
2. Admin gives token to new host
3. Host runs installer with `--auto-register --registration-token <token>`
4. If `TESSERA_AUTO_APPROVE_VOTERS=true`: PSK returned immediately
5. Otherwise: registration pending, admin approves via dashboard

### Token options

- **`bind_ip`** — restrict the token to a specific source IP, IPv6 address, or CIDR range (e.g., `192.168.1.0/24`, `fd00::/64`). The restriction persists on the voter record and is enforced on every subsequent vote submission.
- **`ttl`** — token expiry in seconds (0 = never expires)

```bash
# Generate a token restricted to a subnet
curl -X POST http://tessera:8780/api/v1/voters/tokens \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"bind_ip": "192.168.1.0/24", "ttl": 3600}'
```

### Registration API

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `POST` | `/api/v1/voters/tokens` | Bearer | Generate one-time registration token |
| `GET` | `/api/v1/voters/tokens` | — | List all registration tokens |
| `DELETE` | `/api/v1/voters/tokens/{prefix}` | Bearer | Delete a registration token |
| `POST` | `/api/v1/voters/register` | — | Register with a one-time token |
| `GET` | `/api/v1/voters` | — | List all registered voters |
| `GET` | `/api/v1/voters/pending` | — | List pending registrations |
| `POST` | `/api/v1/voters/{name}/approve` | Bearer | Approve a pending voter |
| `POST` | `/api/v1/voters/{name}/revoke` | Bearer | Revoke a voter |
| `DELETE` | `/api/v1/voters/{name}` | Bearer | Permanently delete a voter |
| `POST` | `/api/v1/voters/{name}/rotate-key` | Bearer | Rotate PSK with grace period |

---

## Hot-Reload Configuration

Tessera watches config files for changes and reloads without restart.

| File | Effect |
|------|--------|
| `voters.json` | New/removed voters take effect immediately |
| `servers.json` | New/removed DHCP servers (roles via API) |
| API token file | Token rotation without restart |

What requires restart: `TESSERA_PORT`, `TESSERA_HOST`, engine parameters (quorum, rounds, etc.).

Send `SIGHUP` for immediate reload:

```bash
kill -HUP $(pidof uvicorn)
```

---

## Web Dashboard

The Preact-based dashboard provides five pages:

| Page | Path | Purpose |
|------|------|---------|
| **Failover** | `/failover` | Server status, voter grid with health check badges, quorum bar, transitions |
| **DHCP** | `/dhcp` | Scope CRUD, reservations, leases, scope settings |
| **Protection** | `/protection` | Backups, drift enforcement, restore, drift history |
| **Servers** | `/servers` | Server pool management (add/remove/promote/demote) |
| **Voters** | `/voters` | Voter registry, token wizard, approve/revoke/delete/rotate |

### Safety features

- **Confirm dialogs** on all destructive actions (promote, demote, remove, delete, revoke)
- **Stale data banner** — appears after 3 consecutive API poll failures
- **Auth dialog** — prompts for admin token on first write action, verifies server-side
- **Connection indicator** — green/red dot in nav bar (ping every 10s)
- **Signature verification badge** — voter detail modal shows HMAC verification status

---

## Configuration Reference

All settings use the `TESSERA_` prefix.

| Variable | Default | Description |
|----------|---------|-------------|
| `TESSERA_SERVERS_FILE` | `/etc/tessera/servers.json` | Path to servers JSON file |
| `TESSERA_API_TOKEN_FILE` | `/etc/tessera/token` | Path to Technitium API token file |
| `TESSERA_VOTER_KEYS_FILE` | `/etc/tessera/voters.json` | Path to voter HMAC PSK JSON file |
| `TESSERA_ADMIN_API_KEY` | *(empty)* | Admin Bearer token for write operations |
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
| `TESSERA_VOTER_REGISTRY_FILE` | `/var/lib/tessera/voter-registry.json` | Voter metadata store |
| `TESSERA_CA_CERT_FILE` | *(empty)* | Custom CA cert for Technitium HTTPS |
| `TESSERA_CORS_ORIGINS` | *(empty)* | CORS allowed origins |
| `TESSERA_DEBUG` | `false` | Enable debug logging |

---

## API Reference

All endpoints under `/api/v1/`. Write endpoints require `Authorization: Bearer <admin-key>`.

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/api/v1/ping` | — | Health check |
| `GET` | `/api/v1/health` | — | Aggregate engine health |
| `POST` | `/api/v1/auth/verify` | Bearer | Verify admin token |
| `GET` | `/api/v1/status` | — | Failover status, voter states, transitions |
| `POST` | `/api/v1/vote` | HMAC | Submit a voter health check |
| `GET` | `/api/v1/servers` | — | List all DHCP servers |
| `POST` | `/api/v1/servers` | Bearer | Add a server to the pool |
| `DELETE` | `/api/v1/servers/{name}` | Bearer | Remove a server from the pool |
| `POST` | `/api/v1/servers/{name}/promote` | Bearer | Promote to active |
| `POST` | `/api/v1/servers/{name}/demote` | Bearer | Demote to candidate |
| `GET` | `/api/v1/scopes` | — | List all DHCP scopes |
| `GET` | `/api/v1/scopes/{name}` | — | Get scope details |
| `POST` | `/api/v1/scopes` | Bearer | Create a scope |
| `PUT` | `/api/v1/scopes/{name}` | Bearer | Update a scope |
| `DELETE` | `/api/v1/scopes/{name}` | Bearer | Delete a scope |
| `POST` | `/api/v1/scopes/{name}/enable` | Bearer | Enable a scope |
| `POST` | `/api/v1/scopes/{name}/disable` | Bearer | Disable a scope |
| `GET` | `/api/v1/leases` | — | List all leases |
| `GET` | `/api/v1/leases/{scope}` | — | List leases for a scope |
| `GET` | `/api/v1/backups` | — | List config backups |
| `POST` | `/api/v1/backups` | Bearer | Create a manual backup |
| `POST` | `/api/v1/voters/tokens` | Bearer | Generate registration token |
| `GET` | `/api/v1/voters/tokens` | — | List registration tokens |
| `DELETE` | `/api/v1/voters/tokens/{prefix}` | Bearer | Delete a token |
| `POST` | `/api/v1/voters/register` | — | Register a new voter |
| `GET` | `/api/v1/voters` | — | List all voters |
| `GET` | `/api/v1/voters/pending` | — | List pending voters |
| `POST` | `/api/v1/voters/{name}/approve` | Bearer | Approve pending voter |
| `POST` | `/api/v1/voters/{name}/revoke` | Bearer | Revoke a voter |
| `DELETE` | `/api/v1/voters/{name}` | Bearer | Delete a voter |
| `POST` | `/api/v1/voters/{name}/rotate-key` | Bearer | Rotate voter PSK |

---

## Development

```bash
uv run pytest                          # Tests
uv run ruff check src/ tests/          # Lint
uv run ruff format --check src/ tests/ # Format check
uv run mypy src/                       # Type check (strict)
```

### Project structure

```
src/tessera/
├── app.py              # FastAPI factory + lifespan
├── config.py           # Pydantic settings (env vars)
├── deps.py             # FastAPI DI providers
├── exceptions.py       # AppError hierarchy + ValidationError
├── pages.py            # Frontend SPA page serving
├── registry.py         # Engine lifecycle registry
├── api/
│   ├── schemas.py      # Pydantic request/response models
│   └── v1/
│       ├── backups.py      # Backup endpoints
│       ├── enforcement.py  # Enforcement endpoints
│       ├── failover.py     # Vote + status endpoints
│       ├── health.py       # Ping + health + auth verify
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
│   └── voter_registry.py   # Voter registration + PSK + bind_ip
├── static/             # Built frontend assets
└── templates/
    └── page.html       # SPA shell template

frontend/               # Preact + TypeScript + Vite
├── src/
│   ├── components/Shell.tsx  # Layout, nav, modal, confirm, auth dialog, stale banner
│   ├── lib/api.ts            # Typed API client + admin auth flow
│   ├── lib/utils.ts          # Signals, polling (with error tracking), formatters
│   ├── pages/
│   │   ├── failover.tsx      # Failover dashboard + health check badges
│   │   ├── dhcp.tsx          # Scope management
│   │   ├── protection.tsx    # Backup & enforcement
│   │   ├── servers.tsx       # Server pool (add/remove/promote/demote)
│   │   └── voters.tsx        # Voter registry + token wizard
│   └── styles/tessera.css    # Design system

voter/
├── Dockerfile                # Alpine-based voter container
├── docker-compose.yml        # Compose for containerised voter
├── entrypoint.sh             # Loop entrypoint (interval-based)
├── tessera-install-voter.sh  # Automated bare-metal installer
├── tessera-voter.sh          # Voter agent (dual health checks)
├── tessera-voter.service     # systemd oneshot unit
└── tessera-voter.timer       # systemd timer (30s)
```

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | System design, engine graph, source tree |
| [Internals](docs/internals.md) | Deep dive: lifecycle, state machines, data flows, middleware |
| [Deployment](docs/deployment.md) | Docker, bare-metal, voter agent setup |
| [Development](docs/development.md) | Local setup, testing, conventions |
| [Changelog](CHANGELOG.md) | Release history |
| [Contributing](CONTRIBUTING.md) | How to contribute |
| [Security](SECURITY.md) | Vulnerability reporting |

## License

[GPLv3](LICENSE)
