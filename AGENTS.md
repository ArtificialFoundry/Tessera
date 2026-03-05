# AGENTS.md — AI Agent Operating Manual

> **Read this first.** This is your map. Update it when you change things.

## Quick Reference

```bash
uv run pytest                          # Run tests (must pass)
uv run ruff check src/ tests/          # Lint (must be clean)
uv run ruff format --check src/ tests/ # Format check
uv run mypy src/                       # Type check (strict, must pass)
uv run uvicorn tessera.app:create_app --factory --reload  # Dev server
```

**Key paths:**
- Source: `src/tessera/`
- Tests: `tests/`
- API routes: `src/tessera/api/v1/`
- DI providers: `src/tessera/deps.py`
- Config: `src/tessera/config.py` (env vars prefixed `TESSERA_`)

## What This Project Is

Tessera — a production-grade DHCP management and failover platform for Technitium DNS Server. Named from Latin "tessera" (authentication token). Built on a modular async web framework scaffold (FastAPI + Pydantic). Two registries:

- **EngineRegistry** — business-logic units with lifecycle (start → run → stop) and dependency ordering
- **ModuleRegistry** — data model groups

Everything is injected via FastAPI `Depends()`. No global singletons in endpoints.

## Architecture (10-second version)

```
Request → FastAPI Router → Depends(get_*) → Engine/Client → Technitium API → Response Model
```

- `deps.py` — `lru_cache` singletons, overridden in tests via `app.dependency_overrides`
- `exceptions.py` — `AppError` hierarchy + `TechnitiumError` (never raise bare ValueError/KeyError)
- `api/schemas.py` — Pydantic response models (all endpoints are typed)
- `registry.py` — Engine lifecycle + module tracking
- `config.py` — Pydantic Settings (env vars: `TESSERA_*`)

### Engines

| Engine | File | Purpose |
|--------|------|---------|
| `TechnitiumClient` | `engines/technitium.py` | Async HTTP client for Technitium DHCP API (primary + standby) |
| `FailoverEngine` | `engines/failover.py` | Quorum-based DHCP failover state machine (voters, rounds, transitions) |
| `ScopeSyncEngine` | `engines/scope_sync.py` | Periodic scope + reservation sync from primary to standby |
| `BackupEngine` | `engines/backup.py` | DHCP state snapshots with cron-scheduled backups, retention, restore, and diff |
| `EnforcementEngine` | `engines/enforcement.py` | Drift detection and auto-restore from pinned backup with cooldown |

## Directory Map

| Path | Purpose |
|---|---|
| `src/tessera/__init__.py` | Package root, version docstring |
| `src/tessera/__main__.py` | `python -m tessera` entry point |
| `src/tessera/app.py` | `create_app()` factory + lifespan (starts all engines) |
| `src/tessera/config.py` | `Settings` (pydantic-settings, `TESSERA_*` env vars) |
| `src/tessera/database.py` | SQLAlchemy engine, session factory, `Base` (unused currently) |
| `src/tessera/deps.py` | FastAPI DI providers (`get_technitium_client`, `get_failover_engine`, etc.) |
| `src/tessera/exceptions.py` | `AppError`, `TechnitiumError` hierarchy |
| `src/tessera/registry.py` | `EngineRegistry`, `ModuleRegistry`, `Engine` base class |
| `src/tessera/engines/technitium.py` | Technitium DHCP API client (scopes, reservations, leases) |
| `src/tessera/engines/failover.py` | Failover state machine (quorum, HMAC voter auth, transitions) |
| `src/tessera/engines/scope_sync.py` | Periodic scope sync engine (primary → standby) |
| `src/tessera/engines/backup.py` | DHCP state backup engine (snapshots, retention, restore, diff) |
| `src/tessera/engines/enforcement.py` | Drift detection and auto-enforcement from pinned backup |
| `src/tessera/api/schemas.py` | Pydantic models: all request/response types |
| `src/tessera/api/v1/health.py` | `/health`, `/ping` endpoints |
| `src/tessera/api/v1/registry.py` | `/registry/engines`, `/registry/modules` |
| `src/tessera/api/v1/failover.py` | `/status`, `/vote` endpoints |
| `src/tessera/api/v1/scopes.py` | Full DHCP scope CRUD + enable/disable + reservations |
| `src/tessera/api/v1/leases.py` | Lease queries, removal, dynamic→reserved conversion |
| `src/tessera/api/v1/backups.py` | Backup CRUD, restore (dry-run + apply) |
| `src/tessera/api/v1/enforcement.py` | Enforcement status, mode, pin/unpin, drift check |
| `src/tessera/pages.py` | SSR page routes — resolves Vite-hashed assets, serves thin HTML shells |
| `src/tessera/templates/page.html` | Single Jinja2 template — mounts per-page Preact bundle |
| `src/tessera/static/dist/` | Vite build output (hashed JS/CSS, shared Preact chunk) |
| `src/tessera/static/css/tessera.css` | Source CSS (dark theme design system) — bundled by Vite |
| `frontend/` | Vite + Preact + TypeScript frontend project |
| `frontend/src/lib/api.ts` | Typed API client for all backend endpoints |
| `frontend/src/lib/utils.ts` | Toast system, polling, time formatting utilities |
| `frontend/src/components/Shell.tsx` | Shell layout, Nav, Modal, ConfirmDialog (signal-based) |
| `frontend/src/pages/failover.tsx` | Failover dashboard (server panel, voters, quorum, timeline) |
| `frontend/src/pages/dhcp.tsx` | DHCP management (scopes grid, detail modal, leases, reservations) |
| `frontend/src/pages/protection.tsx` | Protection (backups, enforcement, drift dual-perspective view) |
| `tests/conftest.py` | Shared fixtures (mock_technitium, DI overrides, async client) |
| `tests/test_api_failover.py` | Failover API endpoint tests |
| `tests/test_api_scopes.py` | Scope CRUD + reservation tests |
| `tests/test_api_leases.py` | Lease query, removal, conversion tests |
| `tests/test_failover.py` | Failover engine unit tests |
| `tests/test_scope_sync.py` | Scope sync engine tests |
| `tests/test_technitium.py` | Technitium client tests |
| `tests/test_backup.py` | Backup engine tests (create, list, delete, restore, retention) |
| `tests/test_enforcement.py` | Enforcement engine tests (pin, mode, drift, auto-restore) |
| `tests/test_api_backups.py` | Backup API endpoint tests |
| `tests/test_api_enforcement.py` | Enforcement API endpoint tests |
| `Dockerfile` | Multi-stage build (uv, non-root) |
| `.pre-commit-config.yaml` | ruff + mypy hooks |

## API Endpoints

### Health
| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Engine health aggregate |
| GET | `/ping` | Liveness probe |

### Failover
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/status` | Full failover status (state, quorum, voters, transitions, config) |
| POST | `/api/v1/vote` | Submit voter ballot (HMAC-SHA256 authenticated) |

### DHCP Scopes
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/scopes` | List all scopes |
| POST | `/api/v1/scopes` | Create scope |
| GET | `/api/v1/scopes/{name}` | Get scope detail (settings, reservations) |
| PUT | `/api/v1/scopes/{name}` | Update scope settings |
| DELETE | `/api/v1/scopes/{name}` | Delete scope |
| POST | `/api/v1/scopes/{name}/enable` | Enable scope |
| POST | `/api/v1/scopes/{name}/disable` | Disable scope |
| POST | `/api/v1/scopes/{name}/reservations` | Add reservation |
| DELETE | `/api/v1/scopes/{name}/reservations/{mac}` | Remove reservation |

### Leases
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/leases` | All leases grouped by scope |
| GET | `/api/v1/leases/{scope}` | Leases for scope (IP-range filtered) |
| DELETE | `/api/v1/leases/{scope}/{address}` | Remove lease |
| POST | `/api/v1/leases/{scope}/{address}/convert` | Convert dynamic → reserved |

### Registry
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/registry/engines` | List registered engines |
| GET | `/api/v1/registry/modules` | List registered modules |

### Backups
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/backups` | List all backups |
| POST | `/api/v1/backups` | Create backup |
| GET | `/api/v1/backups/{id}` | Get backup detail |
| DELETE | `/api/v1/backups/{id}` | Delete backup |
| POST | `/api/v1/backups/{id}/restore` | Restore backup (dry_run or apply) |
| GET | `/api/v1/backups/settings` | Get backup settings |
| PUT | `/api/v1/backups/settings` | Update backup settings (cron, max, auto) |

### Enforcement
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/enforcement` | Enforcement status (mode, pin, history) |
| POST | `/api/v1/enforcement/mode` | Set mode (off/monitor/enforce) |
| POST | `/api/v1/enforcement/pin` | Pin a backup as desired state |
| POST | `/api/v1/enforcement/unpin` | Unpin and disable enforcement |
| POST | `/api/v1/enforcement/check` | Manual drift check |
| PUT | `/api/v1/enforcement/settings` | Update settings (interval, cooldown, etc.) |
| POST | `/api/v1/enforcement/accept` | Accept drift (snapshot + pin current state) |

## Coding Standards

**Non-negotiable — all code must pass ruff + mypy strict with zero errors.**

- `from __future__ import annotations` — first line, every `.py` file
- Type hints on every function signature and non-obvious variable
- Google-style docstrings on public classes and non-trivial functions
- Custom exceptions from `exceptions.py` (never bare `ValueError`/`KeyError`)
- FastAPI `Depends()` for all shared state (no `mock.patch` in tests)
- Pydantic response models on all endpoints (no `dict[str, Any]` returns)
- `ruff` rules: E, W, F, I, N, UP, B, SIM, TCH, RUF, PTH, RET, ARG, ERA, TID
- Imports: use `TYPE_CHECKING` blocks for type-only imports

## Testing Patterns

- Fixtures provide fresh `EngineRegistry` / `ModuleRegistry` per test
- `client` fixture creates a FastAPI app with `dependency_overrides` → isolated tests
- `mock_technitium` fixture provides `AsyncMock(spec=TechnitiumClient)` with all methods pre-mocked
- No `unittest.mock.patch` for registry/settings — use DI overrides
- `mock.patch` is OK for external I/O (database sessions, HTTP calls)
- Test names describe behavior: `test_returns_empty_when_no_matches`

## How to Add Things

### New Engine
1. Subclass `Engine` in `src/tessera/engines/<name>.py`
2. Set `name`, `version`, `description`, `depends_on`
3. Override `start()`, `stop()`, `check_health()`, `get_metrics()`
4. Register in app lifespan or a setup function
5. Add tests in `tests/test_<name>.py`
6. **Update this file:** add to Directory Map, Engines table, + Changelog

### New API Endpoint
1. Create router in `src/tessera/api/v1/<name>.py`
2. Add Pydantic response models to `api/schemas.py`
3. Use `Depends()` for any shared state
4. Include router in `api/v1/__init__.py`
5. Add tests using the `client` fixture
6. **Update this file:** add to Directory Map, API Endpoints, + Changelog

### New Dependency
1. Add to `pyproject.toml` under `[project.dependencies]` or `[project.optional-dependencies.dev]`
2. Run `uv sync`
3. **Update this file:** note in Changelog

## Self-Updating Protocol

**Every time you modify this codebase, update this file:**

1. **Added a file?** → Add it to the Directory Map table
2. **Changed architecture?** → Update the Architecture section
3. **Fixed a bug?** → Add to Known Issues Resolved
4. **Added a dependency?** → Note in Changelog
5. **Made a design decision?** → Document rationale in Changelog

## Current State

- **Status:** Production — deployed as Docker container (192.0.2.2), Docker container `tessera`, port 8780
- **Test coverage:** 90 tests, all passing
- **Lint:** ruff clean, mypy strict clean
- **Known issues:** None
- **Deployment:** `network_mode: host`, only accessible via VPN (LAN port 8780 firewalled)

## Known Issues Resolved

| Date | Issue | Fix |
|---|---|---|
| 2026-02-27 | Global singletons + mock.patch in tests | Refactored to FastAPI DI |
| 2026-02-27 | Raw dict returns on endpoints | Added Pydantic response models |
| 2026-02-27 | Bare ValueError/KeyError | Custom exception hierarchy |
| 2026-03-05 | Standby httpx client never started | ScopeSyncEngine explicitly starts/stops standby client |
| 2026-03-05 | `addReservedLease` expects `ipAddress` not `address` | Fixed field name in TechnitiumClient |
| 2026-03-05 | Technitium leases API ignores scope `name` param | Client-side IP range filtering in leases endpoint |
| 2026-03-05 | Unknown scope returns 502 instead of 404 | Check error message for "not found" pattern |
| 2026-03-05 | httpx logs leak API tokens in URLs | Set httpx logger to WARNING level |

## Design Decisions

| Decision | Rationale |
|----------|-----------|
| HMAC-SHA256 per-voter PSK | Each voter gets unique key; signs `voter\|status\|timestamp`; 60s freshness window |
| No SQLAlchemy dependency | Project doesn't need a database; `Base` alias kept for skeleton compatibility |
| Standby client not in engine registry | Would cause name conflict with primary `technitium`; managed by ScopeSyncEngine |
| Client-side lease filtering | Technitium's `/api/dhcp/leases/list` returns ALL leases regardless of `name` param |
| Composite lease→reservation conversion | No native Technitium endpoint; find MAC from lease list, then `addReservedLease` |
| `scopes/set` for create and update | Technitium has no separate `create` endpoint; `set` creates if scope doesn't exist |
| Port 8780 LAN-firewalled | Only accessible via VPN `wt0`; nft drops LAN traffic to 8780 |

## Changelog

### 2026-03-05 — Vite + Preact Island Architecture (MPA v2)
- **Replaced vanilla JS + Jinja2 templates with Vite + Preact + TypeScript**
- **Multi-entry build**: Separate entry points per page (failover, dhcp, protection) — each page loads only its own code
- **Shared chunk**: Preact runtime + signals + Shell component extracted as `chunks/tessera.[hash].js` (~26KB, 10KB gzipped) — cached across pages
- **Total payload**: ~83KB uncompressed, ~24KB gzipped for all 3 pages + shared code
- **Signal-based state**: `@preact/signals` for toasts, ping status, modals (no React context overhead)
- **Typed API client**: `frontend/src/lib/api.ts` with full type definitions for all endpoints
- **Dual-perspective drift view**: Protection page shows both "What Changed" (human perspective) and "Restore Plan" (restore perspective) tabs in drift results and drift history detail modal
- **Drift log deduplication**: Consecutive identical drift events (same backup_id, action, changes) increment `count` and update `last_seen` instead of creating new history entries. `DriftEvent` gains `count: int = 1` and `last_seen: float` fields. UI shows `×N` badge with last-seen timestamp.
- **Build pipeline**: `frontend/package.json` → `npx vite build` → output to `src/tessera/static/dist/`
- **Dockerfile**: 3-stage build (Node frontend → Python deps → slim runtime), `chmod -R a+rX /app` for non-root container
- **`pages.py` rewritten**: `_build_asset_map()` scans `dist/` for hashed filenames, injects into single `page.html` template
- **Removed**: Old `base.html`, `failover.html`, `dhcp.html`, `protection.html` templates; old `common.js`, `failover.js`, `dhcp.js`, `protection.js`; `vue.global.prod.js`
- **New dep**: `preact`, `@preact/signals`, `@preact/preset-vite`, `vite`, `typescript` (build-time only)
- All checks green: 90 tests ✅ ruff ✅ mypy ✅ tsc ✅

### 2026-03-05 — MPA Rewrite (SPA → Multi-Page Architecture)
- **Dropped Vue.js CDN entirely** — zero framework dependencies, vanilla JS only
- **Server-rendered pages** via Jinja2 templates: `/failover`, `/dhcp`, `/protection` (+ `/` → failover)
- **Separate static assets**: `static/css/tessera.css`, `static/js/{common,failover,dhcp,protection}.js` — independently cacheable
- **New `pages.py`**: FastAPI route handler for all HTML pages using `Jinja2Templates`
- **Updated `app.py`**: Pages router + static mount at `/static` (no more catch-all SPA mount)
- **New dep**: `jinja2>=3.1`
- **Benefits**: No CDN dependency (offline-capable), smaller per-page JS payload, standard HTTP caching, no client-side routing complexity, faster initial render
- **Old `index.html` SPA kept in `static/` but no longer served** — can be removed later

### 2026-03-05 — Settings, Cron Backups, Drift Detail Modal
- **Cron-based backup scheduling**: Replaced interval-based auto-backup with `croniter` cron expressions. New dep: `croniter>=6.0` (+ `types-croniter` for mypy). Config: `TESSERA_BACKUP_CRON_SCHEDULE` env var. Falls back to legacy `TESSERA_AUTO_BACKUP_INTERVAL` if no cron set.
- **Runtime settings API**: `GET/PUT /api/v1/backups/settings` (cron_schedule, max_backups, auto_enabled, next_run), `PUT /api/v1/enforcement/settings` (check_interval, backup_on_pin, auto_restore_cooldown, max_history)
- **Accept drift endpoint**: `POST /api/v1/enforcement/accept` — snapshots live state, creates backup, pins as new desired state
- **EnforcementEngine new settings**: `backup_on_pin` (auto-backup before pin), `auto_restore_cooldown` (min seconds between auto-restores, prevents thrashing), `max_history` (configurable drift history cap)
- **`pin_backup()` is now async** — tests updated accordingly
- **DriftEvent**: Added `change_count` field
- **UI Protection tab**: Settings section redesigned as two side-by-side cards (Backup + Drift Detection) matching existing design language. Cron input with mono font + helper text, next-run display. Drift history items clickable → drift detail modal with full change table + Restore/Accept actions.
- **JS syntax validation**: Added Node.js syntax check to deploy workflow after a missing `}` broke the UI in a prior deploy.
- All checks green: 88 tests ✅ ruff ✅ mypy ✅

### 2026-03-05 — Backup & Enforcement Engines
- **BackupEngine** (`engines/backup.py`): Full DHCP state snapshots (all scope settings + reservations) as timestamped JSON files. Configurable retention (`max_backups`), auto-backup interval, restore with dry-run diff.
- **EnforcementEngine** (`engines/enforcement.py`): Pin any backup as desired state. Three modes: OFF / MONITOR (detect + log drift) / ENFORCE (detect + auto-restore). Periodic drift checks, capped history (50 events).
- **API routes**: `/api/v1/backups` (CRUD + restore), `/api/v1/enforcement` (status, mode, pin/unpin, manual drift check)
- **Config**: New settings `backup_dir`, `max_backups`, `auto_backup_interval`, `backup_cron_schedule`, `enforcement_interval`
- **Tests**: 41 new tests (12 backup engine, 11 enforcement engine, 8 backup API, 10 enforcement API) — 88 total passing
- All checks green: pytest ✅ ruff ✅ mypy ✅

### 2026-03-05 — Failover Engine Fix + UI Redesign
- **Failover fix:** `evaluate_quorum()` made async; added `_activate_standby_scopes()` / `_deactivate_standby_scopes()` — scopes on standby now actually enable/disable on state transitions
- **Failover fix:** `set_standby_client()` / `set_scope_names()` added to FailoverEngine; wired in `deps.py` (variable ordering fix) and `app.py` lifespan (auto-discovers scope names from primary)
- **API fix:** `api/v1/failover.py` — `await` on now-async `evaluate_quorum()` calls
- **Tests:** 3 test methods made async to match engine changes; 47 tests still passing
- **UI redesign:** Server status panel (primary/standby cards with health, breathing glow on active server, failover flow indicator), fade-up animations, drifting orbs, heartbeat voter indicators, timeline transitions
- **UI: Semantic voter labels** — "up"/"down" → "Primary OK" (green) / "Primary Unreachable" (orange) / "Offline" (red for stale voters); applied consistently across metrics, voter cards, detail modals
- **UI: Scope action drawer** — hover reveals a slide-down toolbar below the card (not overlapping content)
- **UI: Metric labels** — "Up Votes"→"Primary OK", "Down Votes"→"Primary Unreachable", "Consec. Down"→"Rounds Unreachable", "Consec. Up"→"Rounds Healthy"
- Reformatted `engines/technitium.py` (ruff)

### 2026-03-05 — Full DHCP Management
- Added scope CRUD (create/update/delete), enable/disable endpoints
- Added lease removal and dynamic→reserved conversion
- Added `ScopeCreateRequest` schema
- Added `delete_scope`, `remove_lease` to TechnitiumClient
- Frontend: tabbed scope modal (Overview/Settings/Reservations/Leases)
- Frontend: create scope modal, enable/disable + delete on scope cards
- Frontend: settings editor with toggles for all Technitium options
- Frontend: drawer→modal migration, toast notifications, confirm dialogs
- Fixed lease filtering (Technitium ignores name param)
- Fixed scope 404 detection
- Tests: 47 total (was 36), covering all new endpoints + edge cases
- Updated AGENTS.md per self-updating protocol

### 2026-03-05 — Initial Tessera Build
- Three engines: TechnitiumClient, FailoverEngine, ScopeSyncEngine
- HMAC-authenticated voter API
- Full Technitium DHCP proxy (scopes, reservations, leases)
- Vue 3 SPA dashboard (failover + DHCP management)
- Deployed as Docker container, port 8780
- 36 tests passing, ruff + mypy clean

### 2026-02-27 — Production-Grade Refactor (Skeleton)
- Added `exceptions.py` — `AppError` hierarchy with structured data
- Added `deps.py` — FastAPI DI providers (replace global singletons)
- Added `api/schemas.py` — Pydantic response models for all endpoints
- Refactored `database.py` — session factory injected, not global
- Rewrote all tests to use DI overrides instead of `mock.patch`
- Added `Dockerfile` (multi-stage, uv, non-root), `.dockerignore`
- Added `.pre-commit-config.yaml` (ruff + mypy hooks)
