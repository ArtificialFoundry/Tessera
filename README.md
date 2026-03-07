# Tessera

DHCP management and failover platform for [Technitium DNS Server](https://technitium.com/dns/).

Tessera monitors DHCP health through distributed voter agents and automatically
fails over to a standby server when consensus is reached.

## Quick Start

### 1. Deploy Tessera

```bash
git clone https://github.com/ArtificialFoundry/tessera.git
cd tessera
mkdir -p config data/backups
```

Generate your secrets:

```bash
# Technitium API token (from your Technitium dashboard)
echo "your-technitium-api-token" > config/token
chmod 600 config/token

# Admin key for Tessera write operations
openssl rand -hex 32 > config/admin-key
```

Define your DHCP servers in `config/servers.json`:

```json
[
  {"name": "dns-1", "url": "https://192.0.2.1:53443", "role": "active", "priority": 0},
  {"name": "dns-2", "url": "https://192.0.2.2:53443", "role": "candidate", "priority": 10}
]
```

Start with Docker:

```bash
docker compose up -d
```

Open `http://localhost:8780` — you should see the dashboard.

### 2. Add Voter Agents

Voters are lightweight health checkers deployed on your infrastructure VMs.
You need at least 3 voters to form a quorum.

**From the dashboard (easiest):**

1. Go to **Voters** → click **Generate Token**
2. Follow the wizard — set an optional IP restriction and expiry
3. Copy the one-time token shown at the end
4. On each voter machine, run:

```bash
curl -O https://your-tessera/voter/tessera-install-voter.sh
sudo bash tessera-install-voter.sh \
  --tessera-url http://tessera-host:8780 \
  --auto-register \
  --registration-token <paste-token-here>
```

That's it. The installer auto-detects the hostname, registers with Tessera,
receives a PSK, installs the voter script, and starts a 30-second health check timer.

**With Docker (on the voter machine):**

```bash
# After registration, you'll have a voter.conf with VOTER_NAME and VOTER_PSK
docker run -d \
  --name tessera-voter \
  --network host \
  --cap-add NET_RAW --cap-add NET_ADMIN \
  --restart unless-stopped \
  -v /path/to/voter.conf:/etc/tessera/voter.conf:ro \
  tessera-voter:latest
```

> `--network host` + capabilities are needed for the DHCP broadcast probe.

### 3. Verify

Back on the dashboard, go to **Failover** — you should see voter cards appearing
with `HTTP ✓` and `DHCP ✓` badges.

---

## How It Works

```
Voters (deployed on 3+ VMs)
  ├── HTTP probe  → Technitium API responsive?
  ├── DHCP probe  → nmap broadcast — actually serving leases?
  └── POST /api/v1/vote (HMAC-signed, every 30s)

Tessera
  ├── Collects votes, evaluates quorum
  ├── 3 consecutive DOWN rounds → failover to candidate
  ├── 5 consecutive UP rounds → failback to original
  └── Syncs scopes, backups config, enforces drift
```

**Vote logic:** overall status is `"up"` if *either* check passes. Both individual
results (`http_status`, `dhcp_status`) are shown as badges in the dashboard.

---

## Dashboard

| Page | What it does |
|------|-------------|
| **Failover** | Live voter grid, health check badges, quorum bar, transition history |
| **DHCP** | Scope management, reservations, leases |
| **Protection** | Backups, drift enforcement, restore points |
| **Servers** | Add/remove/promote/demote DHCP servers |
| **Voters** | Registry, token wizard, approve/revoke/rotate keys |

Admin actions (anything that writes) prompt for your admin key on first use.
The key is stored in your browser tab and cleared when you close it.

---

## Voter Configuration

Each voter reads `/etc/tessera/voter.conf`:

```bash
VOTER_NAME="voter-1"
VOTER_PSK="hmac-psk-hex-string"
TESSERA_URL="http://tessera-host:8780"
CHECK_TIMEOUT="5"
```

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VOTER_NAME` | Yes | — | Unique voter identifier |
| `VOTER_PSK` | Yes | — | HMAC-SHA256 signing key (hex) |
| `TESSERA_URL` | Yes | — | Tessera API base URL |
| `CHECK_TIMEOUT` | No | `5` | HTTP probe timeout (seconds) |
| `DHCP_TIMEOUT` | No | `CHECK_TIMEOUT` | DHCP broadcast probe timeout (seconds) |
| `DHCP_INTERFACE` | No | auto-detect | Network interface for DHCP probe |

> **Tip:** Increase `DHCP_TIMEOUT` on VMs where DHCP broadcasts cross VLANs
> or where CPU contention causes occasional timeouts (e.g., `DHCP_TIMEOUT="10"`).

---

## Development

```bash
uv sync --all-extras

# Backend
uv run uvicorn tessera.app:create_app --factory --reload --port 8780

# Frontend (separate terminal)
cd frontend && npm ci && npm run dev

# Quality checks
uv run pytest                           # 274 tests
uv run ruff check src/ tests/           # Lint
uv run ruff format --check src/ tests/  # Format
uv run mypy src/                        # Type check (strict)
```

---

## Documentation

| Document | Description |
|----------|-------------|
| [Architecture](docs/architecture.md) | System design, engine graph, source tree |
| [Internals](docs/internals.md) | Engine lifecycle, state machines, data flows, middleware |
| [Deployment](docs/deployment.md) | Production deployment, systemd, reverse proxy |
| [Development](docs/development.md) | Local setup, testing, conventions |
| [Improvement Plan](docs/improvement-plan.md) | Security and resilience roadmap |
| [Changelog](CHANGELOG.md) | Release history |
| [Contributing](CONTRIBUTING.md) | How to contribute |
| [Security](SECURITY.md) | Vulnerability reporting |

---

## Advanced

<details>
<summary><strong>Server configuration (servers.json)</strong></summary>

Tessera supports N DHCP servers with three roles:

| Role | Description |
|------|-------------|
| `active` | Currently serving DHCP leases |
| `candidate` | Ready to be promoted on failover (ranked by priority) |
| `observer` | Monitored for health but never promoted |

```json
[
  {"name": "dns-1", "url": "https://192.0.2.1:53443", "role": "active", "priority": 0},
  {"name": "dns-2", "url": "https://192.0.2.2:53443", "role": "candidate", "priority": 10},
  {"name": "dns-mon", "url": "https://192.0.2.4:53443", "role": "observer", "priority": 99}
]
```

Per-server tokens override the global Technitium API token:

```json
{"name": "dns-3", "url": "...", "role": "candidate", "priority": 20, "token": "per-server-token"}
```

</details>

<details>
<summary><strong>Admin authentication</strong></summary>

Set `TESSERA_ADMIN_API_KEY` in your environment or compose file:

```bash
export TESSERA_ADMIN_API_KEY=$(openssl rand -hex 32)
```

Read endpoints are unauthenticated. Write endpoints require `Authorization: Bearer <key>`:

```bash
# Read (no auth)
curl http://tessera:8780/api/v1/servers

# Write (auth required)
curl -X POST http://tessera:8780/api/v1/servers/dns-2/promote \
  -H "Authorization: Bearer <your-key>"
```

The dashboard prompts for the key on first write action and stores it in `sessionStorage`.

</details>

<details>
<summary><strong>Voter self-registration API</strong></summary>

**Generate a token:**

```bash
curl -X POST http://tessera:8780/api/v1/voters/tokens \
  -H "Authorization: Bearer <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"bind_ip": "192.168.1.0/24", "ttl": 3600}'
```

**Register a voter:**

```bash
curl -X POST http://tessera:8780/api/v1/voters/register \
  -H "Content-Type: application/json" \
  -d '{"token": "<one-time-token>", "name": "voter-4"}'
```

**Token options:**
- `bind_ip` — restrict to IP/CIDR (persists on voter record, enforced on every vote)
- `ttl` — expiry in seconds (0 = never expires)

If `TESSERA_AUTO_APPROVE_VOTERS=true`, the PSK is returned immediately.
Otherwise, approve via dashboard or `POST /api/v1/voters/{name}/approve`.

</details>

<details>
<summary><strong>Hot-reload configuration</strong></summary>

Tessera watches these files and reloads without restart:

| File | Effect |
|------|--------|
| `voters.json` | New/removed voter keys take effect immediately |
| `servers.json` | Server pool updated |
| API token file | Token rotation without restart |

Send `SIGHUP` for immediate reload: `kill -HUP $(pidof uvicorn)`

Settings that require restart: `TESSERA_PORT`, `TESSERA_HOST`, engine parameters (quorum, rounds, intervals).

</details>

<details>
<summary><strong>Manual voter setup (without registration)</strong></summary>

If you prefer manual PSK management instead of the registration API:

1. Generate a PSK: `openssl rand -hex 32`
2. Add it to `config/voters.json`:

```json
{
  "voter-1": "generated-psk-hex",
  "voter-2": "another-psk-hex"
}
```

3. Create `/etc/tessera/voter.conf` on the voter machine with the matching PSK
4. Install the voter script:

```bash
sudo cp voter/tessera-voter.sh /usr/local/bin/
sudo chmod 755 /usr/local/bin/tessera-voter.sh
sudo cp voter/tessera-voter.service voter/tessera-voter.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tessera-voter.timer
```

</details>

<details>
<summary><strong>Bare-metal Tessera (without Docker)</strong></summary>

```bash
uv sync --frozen
sudo mkdir -p /etc/tessera /var/lib/tessera/backups

TESSERA_SERVERS_FILE=/etc/tessera/servers.json \
TESSERA_ADMIN_API_KEY=$(cat config/admin-key) \
uv run uvicorn tessera.app:create_app --factory --host 0.0.0.0 --port 8780
```

</details>

<details>
<summary><strong>Environment variables reference</strong></summary>

All settings use the `TESSERA_` prefix.

| Variable | Default | Description |
|----------|---------|-------------|
| `TESSERA_SERVERS_FILE` | `/etc/tessera/servers.json` | Path to servers JSON |
| `TESSERA_API_TOKEN_FILE` | `/etc/tessera/token` | Technitium API token file |
| `TESSERA_VOTER_KEYS_FILE` | `/etc/tessera/voters.json` | Voter HMAC PSK file |
| `TESSERA_ADMIN_API_KEY` | — | Admin Bearer token |
| `TESSERA_PORT` | `8780` | HTTP port |
| `TESSERA_HOST` | `0.0.0.0` | Bind address |
| `TESSERA_QUORUM` | `3` | Minimum votes for quorum |
| `TESSERA_FAILOVER_ROUNDS` | `3` | Consecutive failed rounds before failover |
| `TESSERA_FAILBACK_ROUNDS` | `5` | Consecutive healthy rounds before failback |
| `TESSERA_VOTE_TTL` | `90` | Vote expiry (seconds) |
| `TESSERA_VOTERS` | — | Expected voter names (comma-separated) |
| `TESSERA_SYNC_INTERVAL` | `300` | Scope sync interval (seconds) |
| `TESSERA_BACKUP_DIR` | `/var/lib/tessera/backups` | Backup directory |
| `TESSERA_MAX_BACKUPS` | `50` | Max retained backups |
| `TESSERA_BACKUP_CRON_SCHEDULE` | — | Cron for auto-backup |
| `TESSERA_ENFORCEMENT_INTERVAL` | `300` | Drift check interval (seconds) |
| `TESSERA_CONFIG_RELOAD_INTERVAL` | `10` | Config watch interval (seconds) |
| `TESSERA_REGISTRATION_TOKEN_TTL` | `3600` | Default token TTL (seconds) |
| `TESSERA_PSK_GRACE_PERIOD` | `60` | PSK rotation grace period (seconds) |
| `TESSERA_AUTO_APPROVE_VOTERS` | `false` | Auto-approve registrations |
| `TESSERA_VOTER_REGISTRY_FILE` | `/var/lib/tessera/voter-registry.json` | Voter metadata |
| `TESSERA_CA_CERT_FILE` | — | Custom CA cert for Technitium HTTPS |
| `TESSERA_CORS_ORIGINS` | — | CORS allowed origins |
| `TESSERA_DEBUG` | `false` | Debug logging |

</details>

<details>
<summary><strong>API reference</strong></summary>

All endpoints under `/api/v1/`. Write endpoints require `Authorization: Bearer <admin-key>`.

| Method | Endpoint | Auth | Description |
|--------|----------|------|-------------|
| `GET` | `/ping` | — | Health check |
| `GET` | `/health` | — | Engine health |
| `POST` | `/auth/verify` | Bearer | Verify admin token |
| `GET` | `/status` | — | Failover status + voter states |
| `POST` | `/vote` | HMAC | Submit health vote |
| `GET` | `/servers` | — | List servers |
| `POST` | `/servers` | Bearer | Add server |
| `DELETE` | `/servers/{name}` | Bearer | Remove server |
| `POST` | `/servers/{name}/promote` | Bearer | Promote to active |
| `POST` | `/servers/{name}/demote` | Bearer | Demote to candidate |
| `GET` | `/scopes` | — | List DHCP scopes |
| `GET` | `/scopes/{name}` | — | Scope details |
| `POST` | `/scopes` | Bearer | Create scope |
| `PUT` | `/scopes/{name}` | Bearer | Update scope |
| `DELETE` | `/scopes/{name}` | Bearer | Delete scope |
| `POST` | `/scopes/{name}/enable` | Bearer | Enable scope |
| `POST` | `/scopes/{name}/disable` | Bearer | Disable scope |
| `GET` | `/leases` | — | All leases |
| `GET` | `/leases/{scope}` | — | Leases for scope |
| `GET` | `/backups` | — | List backups |
| `POST` | `/backups` | Bearer | Manual backup |
| `POST` | `/voters/tokens` | Bearer | Generate reg token |
| `GET` | `/voters/tokens` | — | List tokens |
| `DELETE` | `/voters/tokens/{prefix}` | Bearer | Delete token |
| `POST` | `/voters/register` | — | Register voter |
| `GET` | `/voters` | — | List voters |
| `GET` | `/voters/pending` | — | Pending voters |
| `POST` | `/voters/{name}/approve` | Bearer | Approve voter |
| `POST` | `/voters/{name}/revoke` | Bearer | Revoke voter |
| `DELETE` | `/voters/{name}` | Bearer | Delete voter |
| `POST` | `/voters/{name}/rotate-key` | Bearer | Rotate PSK |

</details>

## License

[GPLv3](LICENSE)
