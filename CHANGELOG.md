# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added

- **Admin authentication** — Bearer token auth for all write/admin API operations
  - `TESSERA_ADMIN_API_KEY` environment variable
  - `POST /api/v1/auth/verify` endpoint for token validation
  - Client-side auth dialog with server-side verification
  - Token stored in `sessionStorage` (cleared on tab close)
  - 🔓 Logout button in nav bar
  - `adminRequest()` wrapper: auto-prompts on first write, retries on 401/503
  - Read operations remain unauthenticated

- **Dual health checks** — voters perform both HTTP and DHCP checks
  - HTTP check (primary): `curl` to Technitium API
  - DHCP check (secondary): `nmap broadcast-dhcp-discover` for actual lease verification
  - Both `http_status` and `dhcp_status` sent with each vote
  - Overall vote = `"up"` if either check passes
  - Per-check pill badges (`HTTP ✓` / `DHCP ✗`) on failover and voters pages
  - Contextual explanations for mixed states in voter detail modal
  - `DHCP_TIMEOUT` config for independent nmap probe timeout
  - Backward-compatible: old voters without check fields still work

- **Voter IP binding** — `bind_ip` field on registration tokens and voter records
  - Accepts IPv4, IPv6, and CIDR notation (e.g., `192.168.1.0/24`, `fd00::/64`)
  - Restriction persists from token → voter record → enforced on every vote
  - Validated via Python `ipaddress` module
  - Displayed in voters table "Bind" column

- **Token wizard** — 3-step modal for generating registration tokens
  - Step 1: Configure (bind_ip with inline IPv4/IPv6/CIDR validation, TTL with human-readable preview)
  - Step 2: Review summary before generating
  - Step 3: One-time token display with copy button and next-steps instructions

- **Server management UI** — add/remove servers from the dashboard
  - Add Server modal (name, URL, role, priority, per-server API token)
  - Remove button on candidate/observer server cards with confirmation
  - `POST /api/v1/servers` and `DELETE /api/v1/servers/{name}` endpoints wired

- **Stale data banner** — warning after 3 consecutive API poll failures
  - Shown on failover, servers, and voters pages
  - `poll()` utility now tracks `consecutiveErrors` and `lastUpdated` signals

- **Server health status mapping** — correct display of engine statuses
  - `running` → Healthy (green), `degraded` → Degraded (yellow), `registered` → Pending (yellow)
  - Previously all non-`"healthy"/"ok"` statuses showed as offline

- **Signature verification badge** in failover voter detail modal
  - Shows Verified ✓ / Unverified / Failed ✗ per voter

### Changed

- Voter script now always runs both HTTP and DHCP checks (removed `CHECK_METHOD` config)
- Voter script fetches active server dynamically from `GET /api/v1/servers` instead of using config
- Voter submits `"down"` vote with target `no-active-server` when no active server found (was silent exit)
- `get_server_states()` now async — calls `check_health_all()` on every status poll so standby health is always fresh
- Registration token modal rewritten from inline form to wizard-style UX
- `cancelAuth()` now properly rejects the promise (was silently nulling resolver, causing hangs)

### Fixed

- Blank page on Vite hashes with hyphens — regex `\w+` → `[\w-]+` in `pages.py`
- Hardcoded `192.0.2.x` placeholder IPs in failover and DHCP pages → dynamic API data
- Text clipping across multiple CSS elements (removed `overflow: hidden`, added proper text handling)
- Empty voters page — added migration to seed registry from existing voter keys on first start
- Standby server perpetually showing "Checking…" — pool health checks were never called for non-active servers
- Auth cancel button hung forever — `cancelAuth()` silently set `_authResolve = null` without rejecting promise
- Voter script crash with `set -euo pipefail` — `grep -oP` returning exit code 1 on non-match killed script before fallback pattern could try
- Modal close button overlapping title text

## [0.1.0] - Unreleased

### Added

- Initial Tessera DHCP management platform with FastAPI backend
- Voter quorum failover engine with HMAC-SHA256 signed votes
- Scope sync engine — periodic reservation sync from primary to standby
- Backup engine — scheduled DHCP config snapshots with cron and retention policy
- Enforcement engine — drift detection and automatic rollback
- Multi-server pool (`TechnitiumPool`) with runtime promotion/demotion
- Vite + Preact island architecture for the web dashboard
- Modal dialogs, toast notifications, confirm dialogs, keyboard navigation
- DHCP proxy API — typed REST endpoints for scopes, leases, and reservations
- Pagination for backups, leases, transitions, and drift history
- DHCP verification: voter probes + server-side vote cross-validation
- Authenticated voter API with per-voter rate limiting
- Voter self-registration with auto-approve and PSK grace period
- Config watcher engine — live reload of voter keys, server list, and tokens
- Automated voter installer script with full edge-case handling
- Structured JSON logging with request-ID propagation
- Systemd service and timer units
- Docker multi-stage build with compose and secrets support
- `response_model` on all API endpoint decorators
- OpenAPI tag descriptions for all router groups
- `X-API-Version: v1` response header on all requests
- `TESSERA_CORS_ORIGINS` setting for optional CORS middleware
- `TESSERA_CA_CERT_FILE` setting for custom CA trust in httpx clients
- Asset map cache with TTL (5 min) instead of unbounded `lru_cache`
- GPLv3 license
- `THIRD_PARTY_NOTICES.md` with all dependency credits
- Comprehensive installation, architecture, and development documentation

### Changed

- Rewritten architecture and deployment docs
- Enforce mode auto-restore indicators; hide manual restore buttons when active

### Fixed

- Bare `RuntimeError` and `assert isinstance` in dependency injection
- Scope sync standby client start, `ipAddress` field, logging config
- Lease filtering by scope IP range (Technitium ignores name param)
- Multi-stage Dockerfile and compose secrets mounts

### Security

- Security headers on all responses (CSP, X-Frame-Options, etc.)
- HMAC-SHA256 vote authentication with replay protection
- Rate limiting on voter endpoints
