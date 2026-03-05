# Tessera

DHCP management and failover platform for Technitium DNS Server.

## Features

- **DHCP Failover Engine** — Voter quorum-based primary/standby failover with hysteresis
- **Scope Sync Engine** — Periodic reservation sync from primary to standby
- **Authenticated Voter API** — HMAC-SHA256 signed vote submissions
- **DHCP Proxy API** — Clean REST endpoints for Technitium DHCP management
- **Web Dashboard** — Glassmorphism Vue 3 SPA with failover status and DHCP management

## Quick Start

```bash
uv sync
TESSERA_PRIMARY_URL=https://192.0.2.1:53443 \
TESSERA_STANDBY_URL=https://192.0.2.2:53443 \
TESSERA_API_TOKEN_FILE=/etc/tessera/token \
uv run uvicorn tessera.app:create_app --factory --host 0.0.0.0 --port 8780
```

## Development

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src/ tests/
uv run mypy src/
```

## Configuration

All settings via environment variables prefixed `TESSERA_`. See `src/tessera/config.py`.
