# Architecture

## Overview

Tessera is a DHCP failover management platform for Technitium DNS Server. It uses a **voter quorum** model: lightweight agents on infrastructure VMs continuously vote on primary server reachability, and Tessera activates/deactivates standby DHCP scopes based on consensus.

## System Design

```
┌─────────────┐     vote (HMAC-SHA256)     ┌───────────────┐
│  voter-1    │──────────────────────────── │               │
│  voter-2    │──────────────────────────── │   Tessera     │
│  voter-3    │──────────────────────────── │   (server:8780)   │
│  voter-4    │──────────────────────────── │               │
│  voter-5    │──────────────────────────── └───────┬───────┘
└─────────────┘                                     │
                                              ┌─────┴─────┐
                                    ┌─────────┤  Engines   ├─────────┐
                                    │         └───────────┘         │
                              ┌─────┴─────┐ ┌─────┴─────┐ ┌───────┴──────┐
                              │ Failover  │ │  Backup   │ │ Enforcement  │
                              │ Engine    │ │  Engine   │ │ Engine       │
                              └─────┬─────┘ └─────┬─────┘ └──────┬──────┘
                                    │             │              │
                              ┌─────┴─────────────┴──────────────┴──────┐
                              │         Technitium DHCP API             │
                              │   u1 (primary)    u2 (standby)         │
                              └────────────────────────────────────────┘
```

## Source Tree

```
src/tessera/
├── app.py                 # FastAPI app, lifespan, security middleware
├── config.py              # Pydantic Settings (env-driven)
├── deps.py                # DI: engine factory functions
├── pages.py               # SSR routes, Vite asset resolution
├── registry.py            # Engine lifecycle registry
├── exceptions.py          # Domain exceptions
├── engines/
│   ├── technitium.py      # Async HTTP client for Technitium API
│   ├── failover.py        # Voter quorum state machine
│   ├── scope_sync.py      # Periodic reservation sync (primary → standby)
│   ├── backup.py          # DHCP state snapshots (CRUD, retention)
│   └── enforcement.py     # Drift detection, auto-restore, pinned state
├── api/
│   ├── schemas.py         # Pydantic response/request models
│   └── v1/
│       ├── failover.py    # Vote ingestion, status, quorum
│       ├── scopes.py      # Scope CRUD, reservations
│       ├── leases.py      # Lease listing, removal, conversion
│       ├── backups.py     # Backup CRUD, restore, settings
│       ├── enforcement.py # Pin/unpin, drift check, mode, settings
│       └── health.py      # Health check
├── templates/
│   └── page.html          # Jinja2 SSR shell (mounts Preact islands)
└── static/
    ├── css/tessera.css     # Design system
    └── dist/               # Vite build output (hashed bundles)

frontend/                   # Preact + TypeScript + Vite
├── src/
│   ├── components/Shell.tsx  # Layout, nav, modal, toast, paginator
│   ├── lib/api.ts            # Typed API client
│   ├── lib/utils.ts          # Signals, polling, formatters
│   ├── pages/failover.tsx    # Failover dashboard
│   ├── pages/dhcp.tsx        # Scope management
│   ├── pages/protection.tsx  # Backup & enforcement
│   └── styles/tessera.css    # Shared styles (source)
├── vite.config.ts
├── tsconfig.json
└── package.json

voter/
├── tessera-voter.sh       # Bash voter agent (curl + openssl)
├── tessera-voter.service  # systemd oneshot unit
└── tessera-voter.timer    # systemd timer (30s)
```

## Engines

All engines extend a base `Engine` class and are lifecycle-managed by the `EngineRegistry`:

| Engine | Depends On | Purpose |
|--------|-----------|---------|
| `TechnitiumClient` | — | Async DHCP API client (scopes, leases, reservations) |
| `FailoverEngine` | `technitium` | Quorum state machine (standby → active → standby) |
| `ScopeSyncEngine` | `technitium` | Reservation sync from primary to standby |
| `BackupEngine` | `technitium` | Full DHCP state snapshots, cron-based auto-backup |
| `EnforcementEngine` | `technitium`, `backup` | Drift detection against pinned snapshot, auto-restore |

## Failover State Machine

```
                    quorum lost
  STANDBY ────────────────────────── ACTIVE
     ▲                                  │
     │         quorum restored          │
     └──────────────────────────────────┘

  Transitions require consecutive rounds:
  - Failover: 3 rounds (~90s) of quorum loss
  - Failback: 5 rounds (~150s) of quorum restoration
```

## Frontend Architecture

**SSR + Client Islands** — FastAPI/Jinja2 serves thin HTML shells per route. Each page mounts a self-contained Preact island hydrated client-side. No SPA, no client-side routing.

- **Routes:** `/` (failover), `/dhcp` (scopes), `/protection` (backup/enforcement)
- **State:** `@preact/signals` for global singletons (toasts, modals, ping status)
- **Build:** Multi-entry Vite, hashed filenames, `_build_asset_map()` resolves at runtime
- **CSP:** `script-src 'self'` — no inline scripts
- **Bundle size:** ~83KB uncompressed, ~24KB gzipped

## Security

- HMAC-SHA256 vote signatures with per-voter PSKs
- Strict CSP headers on all responses
- No inline scripts; all JS served from `/static/`
- OAuth2 Proxy (Keycloak) on external-facing route (`dhcp.example.com`)
- Container runs as non-root user `tessera` (UID 999)

## API

All endpoints under `/api/v1/`. List endpoints support `offset`/`limit` pagination with `PaginationMeta` responses.

| Group | Endpoints |
|-------|-----------|
| Failover | `POST /vote`, `GET /status` |
| Scopes | `GET /scopes`, `GET/POST/PUT/DELETE /scopes/{name}`, reservations |
| Leases | `GET /leases`, `GET /leases/{scope}` |
| Backups | `GET/POST /backups`, `GET/DELETE /backups/{id}`, `POST /backups/{id}/restore`, settings |
| Enforcement | `GET /enforcement`, `PUT /enforcement/mode`, pin/unpin, drift check, accept, settings |
| Health | `GET /health` |
