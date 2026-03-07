# Internals

Deep dive into Tessera's internal design, data flows, and implementation details.

## Application Lifecycle

### Startup sequence

```
create_app()
  ├── Load Settings (env vars + files)
  ├── Configure logging (JSON or human-readable)
  ├── Add middleware stack:
  │   ├── RequestSizeLimitMiddleware (1 MB max)
  │   ├── CORSMiddleware (if TESSERA_CORS_ORIGINS set)
  │   ├── Security headers middleware
  │   └── Request-ID middleware
  ├── Register API routers (/api/v1/*, page routes)
  ├── Register exception handlers
  └── Mount /static file server
```

On lifespan start:

```
lifespan()
  ├── get_engine_registry()         ← builds all engines + wiring
  │   ├── Load API token from file
  │   ├── TechnitiumPool.from_servers() ← create HTTP clients
  │   ├── Register active client as "technitium" engine
  │   ├── Create FailoverEngine (quorum params)
  │   ├── Create ScopeSyncEngine
  │   ├── Create BackupEngine (with SettingsStore)
  │   ├── Create EnforcementEngine
  │   ├── Create VoterRegistryEngine
  │   │   └── Wire: on_keys_changed → failover.update_voter_keys
  │   └── Create ConfigWatcherEngine
  │       └── Wire: pool, failover, voter_registry refs
  ├── pool.start_all()              ← initialize all httpx clients
  │   └── check_health_all()        ← immediate health check
  ├── registry.start_all()          ← dependency-ordered engine start
  │   ├── technitium (active client)
  │   ├── failover
  │   ├── scope_sync
  │   ├── backup
  │   ├── enforcement
  │   ├── voter_registry
  │   │   └── start(): load registry, seed from voters.json if empty
  │   └── config_watcher
  │       └── start(): spawn polling task, register SIGHUP handler
  └── Load scope names into failover engine
```

### Shutdown sequence

```
lifespan() exit
  ├── registry.stop_all()    ← reverse order
  ├── Wait up to 10s for in-flight async tasks
  └── pool.stop_all()        ← close all httpx clients
```

## Engine System

### Base class

All engines extend `Engine` from `registry.py`:

```python
class Engine:
    name: str           # Unique identifier
    version: str        # Semantic version
    description: str    # Human-readable
    depends_on: tuple[str, ...]  # Other engine names

    async def start() -> None
    async def stop() -> None
    async def check_health() -> EngineHealth
    def get_metrics() -> dict[str, Any]
```

### Engine registry

`EngineRegistry` manages lifecycle:

- **Registration:** validates no duplicates, sets status → `REGISTERED`
- **Startup:** topological sort via `depends_on`, recursive `_start_engine()`
- **Shutdown:** reverse registration order
- **Health check:** polls all engines, catches exceptions → `DEGRADED`

### Status lifecycle

```
REGISTERED → STARTING → RUNNING ─→ STOPPED
                │          │
                ↓          ↓
              FAILED    DEGRADED
```

### Engine dependency graph

```
technitium (TechnitiumClient — active server)
    ↑
    ├── failover (FailoverEngine)
    ├── scope_sync (ScopeSyncEngine)
    ├── backup (BackupEngine)
    │       ↑
    │       └── enforcement (EnforcementEngine)
    │
voter_registry (VoterRegistryEngine) — no deps
config_watcher (ConfigWatcherEngine) — no deps, but holds refs to pool/failover/registry
```

## TechnitiumPool

Multi-server management layer wrapping individual `TechnitiumClient` instances.

### Client creation

`TechnitiumPool.from_servers(servers, global_token, ca_cert_file)`:
- Creates one `TechnitiumClient` per `DhcpServer` config
- Per-server `token` overrides the global token
- Groups by role: `active`, `candidates[]`, `observers[]`

### Health checking

`check_health_all()` is called:
- On startup (after `start_all()`)
- On every `get_server_states()` call (triggered by UI poll every 10s)

This ensures standby server health is always fresh, not stale from startup.

### Server operations

| Method | What it does |
|--------|-------------|
| `get_active()` | Returns the current active client |
| `get_candidates()` | Returns all candidate clients |
| `get_all()` | Returns all clients (active + candidates + observers) |
| `promote(name)` | Swap candidate → active, old active → candidate |
| `demote(name)` | Move active → candidate |
| `add_server(config)` | Create new client, add to pool |
| `remove_server(name)` | Stop and remove client from pool |
| `get_server_states()` | Health check all, return name/url/role/status/priority |

### Technitium API client

`TechnitiumClient` wraps the Technitium REST API via `httpx.AsyncClient`:

- SSL verification disabled (self-signed certs common)
- Optional custom CA cert via `TESSERA_CA_CERT_FILE`
- All requests include `token` query parameter
- Warns once per URL about TLS verification being disabled

Key operations: `list_scopes`, `get_scope`, `set_scope`, `get_leases`, `add_reservation`, `remove_reservation`, `enable_scope`, `disable_scope`

## Failover Engine

### State machine

Two states: `STANDBY` (normal) and `ACTIVE` (failover in progress).

```
STANDBY: All scopes on primary, candidate scopes disabled
  │
  │ (quorum says DOWN for failover_rounds consecutive rounds)
  ↓
ACTIVE: Candidate scopes enabled, serving DHCP
  │
  │ (quorum says UP for failback_rounds consecutive rounds)
  ↓
STANDBY: Candidate scopes disabled, primary resumed
```

### Vote processing

`submit_vote(voter, status, timestamp, signature, source_ip, http_status, dhcp_status)`:

1. **Authenticate:** verify HMAC-SHA256 signature against voter's PSK
   - Tries current PSK first, then grace-period old PSK (for key rotation)
2. **Rate limit:** per-voter, configurable window
3. **IP check:** if voter has `bind_ip`, verify `source_ip` matches (supports CIDR)
4. **Store vote:** update `_votes[voter]` with new `Vote` dataclass
5. **Server-side verification:** if pool has an active client, cross-check reported status
6. **Evaluate quorum:** count non-expired votes, check if threshold met

### Quorum evaluation

Every vote triggers `_evaluate()`:

1. Count unexpired votes where `status == DOWN`
2. If `down_count >= quorum` → increment `_consecutive_down`
3. If `_consecutive_down >= failover_rounds` → trigger failover
4. If `down_count < quorum` → increment `_consecutive_up`
5. If `_consecutive_up >= failback_rounds` → trigger failback

### Failover/failback actions

- **Failover:** `pool.promote(best_candidate)` → enables candidate DHCP scopes
- **Failback:** `pool.demote(active)` → disables candidate scopes, restores original

All transitions recorded as `TransitionEvent` with timestamp and reason.

### Source IP extraction

Vote endpoint extracts source IP for `bind_ip` enforcement:
1. `X-Forwarded-For` header (first IP in chain) — for reverse proxy setups
2. `request.client.host` — direct connection fallback

## Voter Registry Engine

### Data model

```python
@dataclass
class VoterRecord:
    name: str
    psk: str                    # Current HMAC PSK (hex)
    status: str                 # "pending" | "approved" | "revoked"
    registered_at: float        # Unix timestamp
    approved_at: float          # Unix timestamp (0 if pending)
    source_ip: str              # IP that registered
    bind_ip: str                # IP/CIDR restriction (empty = unrestricted)
    last_vote_at: float         # Last vote timestamp
    old_psk: str                # Previous PSK during rotation grace period
    old_psk_expires_at: float   # When old PSK stops being accepted
```

### Registration flow

```
Admin generates token (POST /api/v1/voters/tokens)
  → RegistrationToken stored in reg_tokens_file
  → Optional: bind_ip, ttl

Voter registers (POST /api/v1/voters/register)
  → Validate token (not expired, not used, IP matches bind_ip)
  → Generate random PSK (32 bytes hex)
  → Create VoterRecord (status: "pending" or "approved")
  → bind_ip inherited from token → VoterRecord
  → Mark token as used
  → If auto_approve: return PSK immediately
  → Else: return pending, admin must approve

Admin approves (POST /api/v1/voters/{name}/approve)
  → VoterRecord.status = "approved"
  → Trigger on_keys_changed callback → failover engine gets new key map
```

### bind_ip enforcement

`validate_bind_ip(ip_str)`: validates via Python `ipaddress` module
- Accepts: `192.168.1.1`, `fd00::1`, `192.168.1.0/24`, `fd00::/64`
- Returns: normalized string
- Raises: `ValueError` on invalid input → API returns 400

`ip_matches_bind(source_ip, bind_ip)`:
- Single IP: exact match
- CIDR: `ipaddress.ip_address(source) in ipaddress.ip_network(bind)`

### PSK rotation

`rotate_key(voter_name)`:
1. Generate new PSK
2. Move current PSK → `old_psk`, set `old_psk_expires_at = now + grace_period`
3. Both old and new PSK accepted during grace period
4. After expiry, only new PSK works

### Persistence

All voter data stored in two files:
- `voter_keys_file` (`voters.json`): simple `{name: psk}` map — consumed by failover engine
- `voter_registry_file` (`voter-registry.json`): full `VoterRecord` metadata

On `start()`: if registry is empty but `voters.json` has entries, seeds registry from keys (migration path).

## Scope Sync Engine

Runs on a configurable interval (default: 300s). Background `asyncio.Task`.

### Sync algorithm

1. Fetch all scopes from active server
2. For each candidate server:
   - Fetch candidate's scopes
   - For each scope that exists on both:
     - Diff reservations (by MAC address)
     - Add missing reservations to candidate
     - Remove extra reservations from candidate
     - Sync scope settings (excluding server-specific fields: `serverAddress`, `serverHostName`, `enabled`)

Server-specific fields are in `SERVER_SPECIFIC_FIELDS` frozenset — these differ between active and candidate by design and are never synced.

## Backup Engine

### Snapshot format

Each backup is a JSON file in `TESSERA_BACKUP_DIR`:

```json
{
  "backup_id": "20260307-181500",
  "created_at": 1741370100.0,
  "source": "https://172.21.192.1:53443",
  "description": "Scheduled backup",
  "scope_count": 5,
  "reservation_count": 42,
  "scopes": [
    {
      "name": "helium",
      "enabled": true,
      "settings": { ... },
      "reservations": [ ... ]
    }
  ]
}
```

### Scheduling

Two modes:
1. **Cron:** `TESSERA_BACKUP_CRON_SCHEDULE` (e.g., `0 */6 * * *`) — uses `croniter`
2. **Interval:** `TESSERA_AUTO_BACKUP_INTERVAL` (seconds) — simple timer fallback

### Retention

`TESSERA_MAX_BACKUPS` (default: 50). Oldest backups pruned after each new backup.

### Settings persistence

Backup settings (cron schedule, retention count) stored in `SettingsStore` (`engine-settings.json`) — survives container rebuilds since it lives in the backup volume.

## Enforcement Engine

### Modes

| Mode | Behavior |
|------|----------|
| `off` | No drift checking |
| `monitor` | Detect + log drift, no action |
| `enforce` | Detect + auto-restore to pinned backup |

### Drift detection

1. Load pinned backup snapshot
2. Fetch live state from active server
3. For each scope: diff settings and reservations
4. Generate `DriftEvent` with detailed change list and human-readable summary
5. If mode = `enforce`: restore pinned state via Technitium API
6. Exponential backoff with jitter on repeated drift (avoids restore storms)

### Deduplication

Consecutive identical drifts are collapsed — `DriftEvent.count` incremented, `last_seen` updated. Prevents log spam from persistent manual changes.

## Config Watcher Engine

Polls config files every `config_reload_interval` seconds (default: 10s). Uses mtime comparison — no filesystem notification dependency (works with Docker bind mounts).

### Watched files

| File | On change |
|------|-----------|
| `servers_file` | Parse JSON, call `pool.update_servers()` |
| `token_file` | Read new token, update all pool clients |
| `reg_tokens_file` | Reload registration token store in voter registry |

### SIGHUP support

Registers `SIGHUP` handler on start. Sending `kill -HUP <pid>` forces immediate reload of all watched files without waiting for the next poll cycle.

## Middleware Stack

Applied in order (outermost first):

1. **RequestSizeLimitMiddleware** — rejects bodies > 1 MB with 413
   - Checks `Content-Length` header early
   - Also counts streamed bytes for chunked transfers
2. **CORSMiddleware** — only if `TESSERA_CORS_ORIGINS` is configured
3. **Security headers** — applied to every response:
   - `Content-Security-Policy`: `default-src 'self'`, `script-src 'self'`, etc.
   - `X-Content-Type-Options: nosniff`
   - `X-Frame-Options: DENY`
   - `Referrer-Policy: strict-origin-when-cross-origin`
   - `Permissions-Policy: camera=(), microphone=(), geolocation=()`
   - Static assets: `Cache-Control: public, max-age=86400, immutable`
4. **Request-ID** — generates UUID hex, stores in `ContextVar`, adds to response header
   - Respects incoming `X-Request-ID` header
   - Injected into all log records via `_RequestIdFilter`

## Error Handling

### Exception hierarchy

```
AppError (base)
├── RegistryError
│   ├── DuplicateEntryError      → 400
│   └── NotFoundError            → 404
├── AuthenticationError          → 401
├── TechnitiumError              → 502
├── ScopeSyncError               → 500
├── RateLimitError               → 429 (with Retry-After header)
├── RegistrationError            → 400
├── ValidationError              → 400
├── ServiceUnavailableError      → 503
├── EngineStartupError           → 500
├── DependencyError              → 500
└── (default AppError)           → 500
```

All errors return structured JSON:
```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid CIDR notation",
    "detail": null
  }
}
```

### ErrorCode enum

Machine-readable codes: `INTERNAL_ERROR`, `AUTHENTICATION_FAILED`, `RATE_LIMITED`, `NOT_FOUND`, `TECHNITIUM_ERROR`, `BACKUP_ERROR`, `ENFORCEMENT_ERROR`, `REGISTRATION_ERROR`, `SCOPE_SYNC_ERROR`, `VALIDATION_ERROR`, `PAYLOAD_TOO_LARGE`

## Frontend Architecture

### SSR + Client Islands

No SPA routing. FastAPI/Jinja2 serves a thin HTML shell per route (`page.html`). Each page mounts a standalone Preact island. No shared client-side router.

### Asset resolution

`_build_asset_map()` in `pages.py`:
- Scans `static/dist/` for JS files matching `^(\w+)\.[\w-]+\.js$`
- Maps entry name → `{js, css, chunks}` paths
- Cached with 5-minute TTL (rebuilds on new deploy)
- Vite generates content-hashed filenames (hashes may contain hyphens)

### Template context

Each page route calls `_ctx(request, tab_name)` which provides:
- `active_tab`: highlights current nav item
- `js`: page-specific bundle path
- `css`: shared CSS path
- `chunk_js`: shared Preact runtime chunk

### Signal-based state

Global singletons via `@preact/signals`:
- `adminToken`: current admin Bearer token (sessionStorage-backed)
- `authPromptOpen`: whether AuthDialog is showing
- `toasts`: toast notification queue
- `pingOk`: API reachability (polled every 10s)

### Auth flow

```
User clicks write action
  → adminRequest(method, url, body)
    → Has adminToken? → Yes → send with Authorization header
    │                    → 401/503 response? → clear token, re-prompt once
    → No → requestAuth() → show AuthDialog
      → User enters token → POST /api/v1/auth/verify
        → 200? → store in sessionStorage, resolve promise
        → 401? → show error, keep dialog open
        → Cancel? → reject promise with ApiError(0), suppress toast
```

### Polling pattern

`poll(fn, interval)` returns `{data, loading, error, consecutiveErrors, lastUpdated}` signals:
- On success: reset `consecutiveErrors`, update `lastUpdated`
- On failure: increment `consecutiveErrors`
- `StaleBanner` shows after `consecutiveErrors >= 3`

### Admin auth cancellation

`cancelAuth()` must reject the pending promise (not silently null it). Otherwise `adminRequest()` hangs forever — the button stays disabled and the UI is stuck. The rejection uses `ApiError(0, "Authentication cancelled")` and all catch blocks check `isAuthCancelled(e)` to suppress toasts.

## Settings Store

File-backed JSON key-value store for engine settings that must survive container rebuilds.

- Stored at `$TESSERA_BACKUP_DIR/engine-settings.json`
- Atomic writes: write to `.tmp` → rename
- Thread-safe: uses `threading.Lock`
- Sections: `backup` (schedule, retention), `enforcement` (mode, pinned backup)

## Voter Script Internals

### Config parsing

Reads `/etc/tessera/voter.conf` line-by-line, strips quotes, exports known variables via `case` statement. Unknown keys are silently ignored.

### Active server discovery

`GET /api/v1/servers` → parse JSON for `role: "active"`. Two regex patterns tried (JSON field order varies):
1. `"role"..."active"..."url"..."<value>"`
2. `"url"..."<value>"..."role"..."active"`

`|| true` on `grep -oP` prevents `set -euo pipefail` from killing the script on no-match.

### HMAC signing

```bash
printf '%s|%s|%s' "$VOTER_NAME" "$STATUS" "$TIMESTAMP" \
    | openssl dgst -sha256 -hmac "$VOTER_PSK" -hex
```

### Interface auto-detection

For DHCP broadcast probe, detects default interface:
1. `ip route show default | awk '{print $5}'` (Linux)
2. `route -n get default | awk '/interface:/{print $2}'` (macOS)

### Container differences

The `voter/Dockerfile` patches `sudo nmap` → `nmap` via `sed` at build time. The container runs as root with `NET_RAW` + `NET_ADMIN` capabilities and `--network host`.

## Logging

### Structured JSON (production)

Uses `python-json-logger`. Every log entry includes:
- `timestamp`, `level`, `name` (logger), `request_id`, `message`

### Human-readable (debug)

`TESSERA_DEBUG=true` switches to plain-text format with timestamps.

### Request-ID propagation

`ContextVar("request_id")` set per-request in middleware. `_RequestIdFilter` injects it into every log record. Passed through via `X-Request-ID` response header.

### Log levels by component

| Logger | Default level |
|--------|--------------|
| `tessera.*` | INFO |
| `httpx` | WARNING (suppressed) |
| Root | INFO (DEBUG if `TESSERA_DEBUG=true`) |
