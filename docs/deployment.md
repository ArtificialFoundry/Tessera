# Deployment

## Prerequisites

- Docker with BuildKit
- Technitium DNS Server on primary (u1) and standby (u2)
- API token with DHCP management permissions
- Voter PSKs generated for each infrastructure VM

## Container

```bash
docker build --network host -t tessera:latest .
```

The Dockerfile uses a 3-stage build:
1. **Node stage** — installs frontend deps, runs `vite build`
2. **Python stage** — installs Python deps via `uv`
3. **Runtime stage** — slim Debian, non-root `tessera` user (UID 999)

## Run

```bash
docker run -d \
  --name tessera \
  --network host \
  --restart unless-stopped \
  -v /opt/tessera/config:/etc/tessera:ro \
  -v /var/lib/tessera/backups:/var/lib/tessera/backups \
  -e TESSERA_PRIMARY_URL=https://192.0.2.1:53443 \
  -e TESSERA_STANDBY_URL=https://192.0.2.2:53443 \
  -e TESSERA_API_TOKEN_FILE=/etc/tessera/token \
  -e TESSERA_VOTER_KEYS_FILE=/etc/tessera/voters.json \
  -e TESSERA_QUORUM=3 \
  -e TESSERA_FAILOVER_ROUNDS=3 \
  -e TESSERA_FAILBACK_ROUNDS=5 \
  -e TESSERA_VOTE_TTL=90 \
  -e TESSERA_SYNC_INTERVAL=300 \
  -e TESSERA_VOTERS=voter-1,voter-2,voter-3,voter-4,voter-5 \
  -e TESSERA_BACKUP_DIR=/var/lib/tessera/backups \
  -e TESSERA_MAX_BACKUPS=50 \
  -e "TESSERA_BACKUP_CRON_SCHEDULE=0 */6 * * *" \
  -e TESSERA_ENFORCEMENT_INTERVAL=300 \
  tessera:latest
```

## Configuration

All config via environment variables (Pydantic Settings):

| Variable | Default | Description |
|----------|---------|-------------|
| `TESSERA_PRIMARY_URL` | — | Primary Technitium HTTPS URL |
| `TESSERA_STANDBY_URL` | — | Standby Technitium HTTPS URL |
| `TESSERA_API_TOKEN_FILE` | — | Path to API token file |
| `TESSERA_VOTER_KEYS_FILE` | — | Path to voter fingerprints JSON |
| `TESSERA_QUORUM` | `3` | Minimum votes for quorum |
| `TESSERA_FAILOVER_ROUNDS` | `3` | Consecutive down rounds before failover |
| `TESSERA_FAILBACK_ROUNDS` | `5` | Consecutive up rounds before failback |
| `TESSERA_VOTE_TTL` | `90` | Vote expiry in seconds |
| `TESSERA_SYNC_INTERVAL` | `300` | Scope sync interval in seconds |
| `TESSERA_VOTERS` | — | Comma-separated voter names |
| `TESSERA_BACKUP_DIR` | `/var/lib/tessera/backups` | Backup storage path |
| `TESSERA_MAX_BACKUPS` | `50` | Maximum retained backups |
| `TESSERA_BACKUP_CRON_SCHEDULE` | `""` | Cron expression for auto-backup |
| `TESSERA_ENFORCEMENT_INTERVAL` | `300` | Drift check interval in seconds |

## Config Files

### `/etc/tessera/token`

Technitium API token (plaintext, 644 permissions).

### `/etc/tessera/voters.json`

Maps voter names to HMAC-SHA256 fingerprints:

```json
{
  "voter-1": "d233604405fd7b64...",
  "voter-2": "db4cea97ba3e81e1...",
  "voter-3": "2ca1877b7c5d6c54...",
  "voter-4": "0eb6b0aeea86ba1d...",
  "voter-5": "25d097932a2ca632..."
}
```

## Voter Agent Deployment

Deploy `voter/tessera-voter.sh`, `.service`, `.timer` to each voter VM:

```bash
# On each voter VM:
cp tessera-voter.sh /usr/local/bin/
cp tessera-voter.service tessera-voter.timer /etc/systemd/system/
echo "VOTER_NAME=voter-1" > /etc/tessera/voter.env
echo "PSK=<voter-psk>" >> /etc/tessera/voter.env
echo "TESSERA_URL=http://192.0.2.2:8780" >> /etc/tessera/voter.env
systemctl daemon-reload
systemctl enable --now tessera-voter.timer
```

## Volumes

| Path | Purpose | Permissions |
|------|---------|-------------|
| `/opt/tessera/config` → `/etc/tessera` | Token + voter keys | `644`, read-only mount |
| `/var/lib/tessera/backups` | Backup JSON files | `999:999` (tessera user) |

## External Access

Tessera is exposed via Traefik on `dhcp.example.com` with OAuth2 Proxy (Keycloak):

```
Client → dhcp.example.com → Traefik (frontend-1)
  → OAuth2 Proxy → Keycloak auth
  → u2 VPN IP (192.0.2.20:8780)
```
