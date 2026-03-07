# Architecture

## Overview

Tessera is a DHCP failover management platform for Technitium DNS Server. It uses a **voter quorum** model: lightweight agents on infrastructure VMs continuously vote on primary server reachability, and Tessera activates/deactivates standby DHCP scopes based on consensus.

## System Design

```
┌─────────────┐     vote (HMAC-SHA256)     ┌───────────────┐
│  voter-1    │──────────────────────────── │               │
│  voter-2    │──────────────────────────── │   Tessera     │
│  voter-3    │──────────────────────────── │  (server:8780)│
│  voter-4    │──────────────────────────── │               │
│  voter-5    │──────────────────────────── └───────┬───────┘
└─────────────┘                                     │
  Each voter runs:                            ┌─────┴─────┐
  1. HTTP check (Technitium API)    ┌─────────┤  Engines   ├─────────┐
  2. DHCP check (nmap broadcast)    │         └───────────┘         │
  3. Signs & submits vote     ┌─────┴─────┐ ┌─────┴─────┐ ┌───────┴──────┐
                              │ Failover  │ │  Backup   │ │ Enforcement  │
                              │ Engine    │ │  Engine   │ │ Engine       │
                              └─────┬─────┘ └─────┬─────┘ └──────┬──────┘
                              ┌─────┴─────┐ ┌─────┴─────┐ ┌──────┴──────┐
                              │ ScopeSync │ │ VoterReg  │ │ ConfigWatch │
                              │ Engine    │ │ Engine    │ │ Engine      │
                              └─────┬─────┘ └─────┬─────┘ └──────┬──────┘
                              ┌─────┴─────────────┴──────────────┴──────┐
                              │         Technitium DHCP API             │
                              │   u1 (active)     u2 (candidate)       │
                              └────────────────────────────────────────┘
```

## Source Tree

```
src/tessera/
├── app.py                 # FastAPI app, lifespan, security middleware
├── config.py              # Pydantic Settings (env-driven)
├── deps.py                # DI: engine factory functions + require_admin
├── pages.py               # SSR routes, Vite asset resolution ([\w-]+ hashes)
├── registry.py            # Engine lifecycle registry + EngineStatus enum
├── exceptions.py          # Domain exceptions + ValidationError
├── engines/
│   ├── technitium.py      # Async HTTP client for Technitium API + TechnitiumPool
│   ├── failover.py        # Voter quorum state machine (Vote with http/dhcp_status)
│   ├── scope_sync.py      # Periodic reservation sync (active → candidates)
│   ├── backup.py          # DHCP state snapshots (CRUD, retention)
│   ├── enforcement.py     # Drift detection, auto-restore, pinned state
│   ├── voter_registry.py  # Voter registration, PSK management, bind_ip enforcement
│   └── config_watcher.py  # Hot-reload config files (voters, servers, token)
├── api/
│   ├── schemas.py         # Pydantic request/response models (VoteRequest with check fields)
│   └── v1/
│       ├── failover.py    # Vote ingestion (with source_ip + check status), quorum status
│       ├── scopes.py      # Scope CRUD, reservations
│       ├── leases.py      # Lease listing, removal, conversion
│       ├── backups.py     # Backup CRUD, restore, settings
│       ├── enforcement.py # Pin/unpin, drift check, mode, settings
│       ├── health.py      # Ping, health, auth/verify
│       ├── servers.py     # Server pool management (add/remove/promote/demote)
│       └── voters.py      # Voter registration, tokens, approve/revoke/rotate
├── templates/
│   └── page.html          # Jinja2 SSR shell (mounts Preact islands)
└── static/
    └── dist/              # Vite build output (hashed bundles)

frontend/                   # Preact + TypeScript + Vite
├── src/
│   ├── components/
│   │   └── Shell.tsx       # Layout, Nav, Modal, ConfirmDialog, AuthDialog,
│   │                       # Paginator, StaleBanner
│   ├── lib/
│   │   ├── api.ts          # Typed API client, adminRequest, auth flow,
│   │   │                   # request/adminRequest separation
│   │   └── utils.ts        # Signals, poll() with consecutiveErrors/lastUpdated,
│   │                       # toast, timeAgo, formatTime, esc
│   ├── pages/
│   │   ├── failover.tsx    # Failover dashboard, server cards, voter grid,
│   │   │                   # health check badges, transitions, quorum bar
│   │   ├── dhcp.tsx        # Scope CRUD, reservations, leases, settings
│   │   ├── protection.tsx  # Backup & enforcement, drift view, restore modal
│   │   ├── servers.tsx     # Server pool (add modal, remove, promote, demote,
│   │   │                   # correct status mapping)
│   │   └── voters.tsx      # Voter registry, token wizard (3-step),
│   │                       # health check badges, approve/revoke/delete/rotate
│   └── styles/
│       └── tessera.css     # Design system, check badges, stale banner
├── vite.config.ts
├── tsconfig.json
└── package.json

voter/
├── tessera-install-voter.sh  # Automated installer (nmap, systemd, SELinux)
├── tessera-voter.sh          # Bash voter agent (dual health checks)
├── tessera-voter.service     # systemd oneshot unit
└── tessera-voter.timer       # systemd timer (30s)
```

## Engines

All engines extend a base `Engine` class and are lifecycle-managed by the `EngineRegistry`:

| Engine | Depends On | Purpose |
|--------|-----------|---------|
| `TechnitiumPool` | — | Multi-server async DHCP API client (scopes, leases, reservations) with per-server health |
| `FailoverEngine` | `technitium` | Quorum state machine (standby → active → standby) with source_ip + dual-check tracking |
| `ScopeSyncEngine` | `technitium` | Reservation sync from active to all candidate servers |
| `BackupEngine` | `technitium` | Full DHCP state snapshots, cron-based auto-backup |
| `EnforcementEngine` | `technitium`, `backup` | Drift detection against pinned snapshot, auto-restore |
| `VoterRegistryEngine` | — | Voter registration, PSK management, bind_ip validation/enforcement |
| `ConfigWatcherEngine` | — | Hot-reload voters.json, servers.json, token file |

### Engine Status Lifecycle

```
REGISTERED → STARTING → RUNNING → STOPPED
                ↓          ↓
              FAILED    DEGRADED
```

`get_server_states()` calls `check_health_all()` on every poll so server health is always fresh (not cached from startup).

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

## Dual Health Check Design

```
Voter Agent (every 30s)
  │
  ├── 1. GET /api/v1/servers → find active server URL
  │
  ├── 2. HTTP check → curl to Technitium API
  │      200/401/403 = up, else = down
  │
  ├── 3. DHCP check → nmap broadcast-dhcp-discover
  │      Response contains active IP = up, else = down
  │      (skipped if nmap not installed)
  │
  └── 4. POST /api/v1/vote
         {voter, status, timestamp, signature,
          http_status: "up"|"down",
          dhcp_status: "up"|"down"}

  Overall: up if EITHER passes, down if BOTH fail
```

## Frontend Architecture

**SSR + Client Islands** — FastAPI/Jinja2 serves thin HTML shells per route. Each page mounts a self-contained Preact island hydrated client-side. No SPA, no client-side routing.

- **Routes:** `/failover`, `/dhcp`, `/protection`, `/servers`, `/voters`
- **State:** `@preact/signals` for global singletons (toasts, modals, ping status, auth)
- **Build:** Multi-entry Vite, hashed filenames (`[\w-]+` pattern), `_build_asset_map()` resolves at runtime
- **CSP:** `script-src 'self'` — no inline scripts
- **Auth flow:** `adminRequest()` auto-prompts AuthDialog on first write, retries on 401/503, stores token in `sessionStorage`
- **Polling:** `poll()` helper tracks `consecutiveErrors` + `lastUpdated` signals; `StaleBanner` shows after 3 failures

## Security

- **Admin auth:** Bearer token for write operations, server-side verification, sessionStorage persistence
- **Vote auth:** HMAC-SHA256 signatures with per-voter PSKs, `VOTER_NAME|STATUS|TIMESTAMP` format
- **IP binding:** `bind_ip` on registration tokens persists to voter records, enforced on every vote via `X-Forwarded-For`
- **CSP:** Strict Content-Security-Policy on all responses
- **Container:** Runs as non-root user `tessera` (UID 999)
- **No inline scripts:** All JS served from `/static/`
- **OAuth2 proxy:** External access via O2P + Keycloak SSO

## API

All endpoints under `/api/v1/`. Write endpoints require `Authorization: Bearer <admin-key>`.

| Group | Endpoints |
|-------|-----------|
| Auth | `POST /auth/verify` |
| Failover | `POST /vote`, `GET /status` |
| Servers | `GET/POST /servers`, `DELETE /servers/{name}`, `POST /servers/{name}/promote`, `POST /servers/{name}/demote` |
| Voters | `GET/POST /voters`, `GET /voters/pending`, `POST/GET/DELETE /voters/tokens`, `POST /voters/{name}/approve`, `POST /voters/{name}/revoke`, `DELETE /voters/{name}`, `POST /voters/{name}/rotate-key` |
| Scopes | `GET/POST /scopes`, `GET/PUT/DELETE /scopes/{name}`, enable/disable |
| Leases | `GET /leases`, `GET /leases/{scope}` |
| Backups | `GET/POST /backups`, `GET/DELETE /backups/{id}`, restore, settings |
| Enforcement | `GET /enforcement`, mode, pin/unpin, drift check, accept, settings |
| Health | `GET /ping`, `GET /health` |
