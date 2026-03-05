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
| `src/tessera/api/schemas.py` | Pydantic models: all request/response types |
| `src/tessera/api/v1/health.py` | `/health`, `/ping` endpoints |
| `src/tessera/api/v1/registry.py` | `/registry/engines`, `/registry/modules` |
| `src/tessera/api/v1/failover.py` | `/status`, `/vote` endpoints |
| `src/tessera/api/v1/scopes.py` | Full DHCP scope CRUD + enable/disable + reservations |
| `src/tessera/api/v1/leases.py` | Lease queries, removal, dynamic→reserved conversion |
| `src/tessera/static/index.html` | Vue 3 SPA dashboard (failover + DHCP management) |
| `tests/conftest.py` | Shared fixtures (mock_technitium, DI overrides, async client) |
| `tests/test_api_failover.py` | Failover API endpoint tests |
| `tests/test_api_scopes.py` | Scope CRUD + reservation tests |
| `tests/test_api_leases.py` | Lease query, removal, conversion tests |
| `tests/test_failover.py` | Failover engine unit tests |
| `tests/test_scope_sync.py` | Scope sync engine tests |
| `tests/test_technitium.py` | Technitium client tests |
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
- **Test coverage:** 47 tests, all passing
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
