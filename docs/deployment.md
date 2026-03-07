# Deployment

## Prerequisites

- Docker with BuildKit
- Technitium DNS Server instances (2+) with DHCP enabled
- API token with DHCP management permissions
- Admin API key for Tessera write operations

## Container Build

```bash
docker build --network host -t tessera:latest .
```

The Dockerfile uses a 3-stage build:
1. **Node stage** — installs frontend deps, runs `vite build`
2. **Python stage** — installs Python deps via `uv`
3. **Runtime stage** — slim Debian, non-root `tessera` user (UID 999)

## Docker Compose

```yaml
name: tessera
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
      - TESSERA_ADMIN_API_KEY=<your-admin-key>
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
```

Generate the admin API key:

```bash
openssl rand -hex 32
```

## Config Files

### `config/servers.json`

```json
[
  {"name": "dns-1", "url": "https://192.0.2.1:53443", "role": "active", "priority": 0},
  {"name": "dns-2", "url": "https://192.0.2.2:53443", "role": "candidate", "priority": 10}
]
```

Optional per-server token override:

```json
{"name": "dns-3", "url": "https://192.0.2.3:53443", "role": "candidate", "priority": 20, "token": "per-server-token"}
```

### `config/token`

Technitium API token (plaintext, chmod 600).

### `config/voters.json`

Maps voter names to HMAC-SHA256 pre-shared keys:

```json
{
  "voter-1": "hex-psk-1...",
  "voter-2": "hex-psk-2..."
}
```

Generate PSKs: `openssl rand -hex 32`

## Voter Agent Deployment

### Docker (recommended)

Create `voter.conf`:

```bash
VOTER_NAME="voter-1"
VOTER_PSK="<hex-psk>"
TESSERA_URL="http://tessera-host:8780"
CHECK_TIMEOUT="5"
DHCP_TIMEOUT="5"
```

Using Docker Compose (from repo root):

```bash
cp /path/to/voter.conf voter/voter.conf
docker compose -f voter/docker-compose.yml up -d
```

Or standalone:

```bash
docker build -f voter/Dockerfile -t tessera-voter:latest .

docker run -d \
  --name tessera-voter \
  --network host \
  --cap-add NET_RAW \
  --cap-add NET_ADMIN \
  --restart unless-stopped \
  -v $(pwd)/voter.conf:/etc/tessera/voter.conf:ro \
  -e VOTER_INTERVAL=30 \
  tessera-voter:latest
```

**Requirements:**
- `--network host` — needed for DHCP broadcast on the host's network interface
- `NET_RAW` + `NET_ADMIN` — needed for nmap raw packet DHCP probe
- Container runs as root (single-purpose, sends raw packets)

Check logs:

```bash
docker logs -f tessera-voter
```

### Automated bare-metal install

On each voter VM:

```bash
sudo ./voter/tessera-install-voter.sh \
  --tessera-url http://tessera-host:8780 \
  --auto-register --registration-token <token>
```

### Manual install

```bash
# Copy files
sudo cp voter/tessera-voter.sh /usr/local/bin/tessera-voter.sh
sudo chmod +x /usr/local/bin/tessera-voter.sh
sudo cp voter/tessera-voter.service voter/tessera-voter.timer /etc/systemd/system/

# Install nmap for DHCP broadcast probe
sudo dnf install -y nmap   # RHEL/AlmaLinux
sudo apt install -y nmap   # Debian/Ubuntu

# Create config
sudo mkdir -p /etc/tessera
sudo tee /etc/tessera/voter.conf <<EOF
VOTER_NAME="$(hostname -s)"
VOTER_PSK="<psk-from-registration>"
TESSERA_URL="http://tessera-host:8780"
CHECK_TIMEOUT="5"
DHCP_TIMEOUT="5"
EOF
sudo chmod 600 /etc/tessera/voter.conf

# Enable timer
sudo systemctl daemon-reload
sudo systemctl enable --now tessera-voter.timer
```

### Voter config reference

| Variable | Default | Description |
|----------|---------|-------------|
| `VOTER_NAME` | *(required)* | Voter identifier |
| `VOTER_PSK` | *(required)* | HMAC-SHA256 pre-shared key (hex) |
| `TESSERA_URL` | *(required)* | Tessera API base URL |
| `CHECK_TIMEOUT` | `5` | HTTP check timeout in seconds |
| `DHCP_TIMEOUT` | `CHECK_TIMEOUT` | nmap DHCP broadcast probe timeout in seconds |
| `DHCP_INTERFACE` | *(auto-detect)* | Network interface for DHCP broadcast probe |

**Tuning `DHCP_TIMEOUT`:** Increase on hosts where DHCP broadcast responses must traverse VLANs or routers, or on busy VMs with CPU contention. Typical values: 5s (same subnet), 10s (cross-VLAN).

### Voter health check flow

1. `GET /api/v1/servers` → find active server URL
2. HTTP check: `curl` to Technitium API (200/401/403 = up)
3. DHCP check: `nmap broadcast-dhcp-discover` (active IP in response = up)
4. Sign: `VOTER_NAME|STATUS|TIMESTAMP` with HMAC-SHA256
5. `POST /api/v1/vote` with `http_status`, `dhcp_status`, and overall `status`

Overall: `"up"` if either check passes. Both individual results are reported for UI badges.

## External Access

Recommended: Traefik + OAuth2 Proxy (Keycloak SSO):

```
Client → tessera.example.com → Traefik (TLS)
  → OAuth2 Proxy → Keycloak auth
  → Tessera container (port 8780)
```

Admin write operations additionally require the Bearer token (entered in the dashboard).

## Volumes

| Path | Purpose | Permissions |
|------|---------|-------------|
| `/run/secrets/` | Token, voters.json, servers.json | Read-only mount |
| `/var/lib/tessera/backups` | Backup JSON files | `999:999` (tessera user) |
| `/var/lib/tessera/voter-registry.json` | Voter registration metadata | `999:999` |
