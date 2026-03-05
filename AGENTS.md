# AGENTS.md — AI Agent Operating Manual

> **Read this first.** This is your map. Update it when you change things.

## Quick Reference

```bash
uv run pytest                          # Run tests (must pass)
uv run ruff check src/ tests/          # Lint (must be clean)
uv run ruff format --check src/ tests/ # Format check
uv run mypy src/                       # Type check (strict, must pass)
uv run uvicorn skeleton.app:create_app --factory --reload  # Dev server
```

**Key paths:**
- Source: `src/skeleton/`
- Tests: `tests/`
- API routes: `src/skeleton/api/v1/`
- DI providers: `src/skeleton/deps.py`
- Config: `src/skeleton/config.py` (env vars prefixed `SKELETON_`)

## What This Project Is

A modular async web framework scaffold (FastAPI + SQLAlchemy + Redis). Two registries:

- **EngineRegistry** — business-logic units with lifecycle (start → run → stop) and dependency ordering
- **ModuleRegistry** — data model groups (SQLAlchemy tables)

Everything is injected via FastAPI `Depends()`. No global singletons in endpoints.

## Architecture (10-second version)

```
Request → FastAPI Router → Depends(get_*_registry) → Registry → Response Model
```

- `deps.py` — `lru_cache` singletons, overridden in tests via `app.dependency_overrides`
- `exceptions.py` — `AppError` hierarchy (never raise bare ValueError/KeyError)
- `api/schemas.py` — Pydantic response models (all endpoints are typed)
- `registry.py` — Engine lifecycle + module tracking
- `database.py` — async session factory + context manager (commit/rollback)

## Directory Map

| Path | Purpose |
|---|---|
| `src/skeleton/__init__.py` | Package root, version docstring |
| `src/skeleton/__main__.py` | `python -m skeleton` entry point |
| `src/skeleton/app.py` | `create_app()` factory + lifespan |
| `src/skeleton/config.py` | `Settings` (pydantic-settings) |
| `src/skeleton/database.py` | SQLAlchemy engine, session factory, `Base` |
| `src/skeleton/deps.py` | FastAPI DI providers |
| `src/skeleton/exceptions.py` | Exception hierarchy |
| `src/skeleton/registry.py` | `EngineRegistry`, `ModuleRegistry`, `Engine` base |
| `src/skeleton/api/schemas.py` | Pydantic response models |
| `src/skeleton/api/v1/health.py` | `/health`, `/ping` endpoints |
| `src/skeleton/api/v1/registry.py` | `/registry/engines`, `/registry/modules` |
| `tests/conftest.py` | Shared fixtures (registries, async client with DI overrides) |
| `migrations/env.py` | Alembic async migration env |

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
- No `unittest.mock.patch` for registry/settings — use DI overrides
- `mock.patch` is OK for external I/O (database sessions, HTTP calls)
- Test names describe behavior: `test_returns_empty_when_no_matches`

## How to Add Things

### New Engine
1. Subclass `Engine` in a new module under `src/skeleton/`
2. Set `name`, `version`, `description`, `depends_on`
3. Override `start()`, `stop()`, `check_health()`, `get_metrics()`
4. Register in app lifespan or a setup function
5. Add tests in `tests/test_<name>.py`
6. **Update this file:** add to Directory Map + Changelog

### New API Endpoint
1. Create router in `src/skeleton/api/v1/<name>.py`
2. Add Pydantic response models to `api/schemas.py`
3. Use `Depends()` for any shared state
4. Include router in `api/v1/__init__.py`
5. Add tests using the `client` fixture
6. **Update this file:** add to Directory Map + Changelog

### New Data Module
1. Define SQLAlchemy models (inherit from `database.Base`)
2. Create a `ModuleDescriptor` and register it
3. Generate Alembic migration: `uv run alembic revision --autogenerate -m "..."`
4. Add tests
5. **Update this file:** add to Directory Map + Changelog

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

Keep sections **short and scannable**. This file must fit in a single context window read. If a section grows beyond ~20 lines, extract it to `docs/` and link to it.

## Current State

- **Status:** Scaffold complete — no business logic engines yet
- **Test coverage:** ~95% (45+ tests)
- **Known issues:** None
- **Planned:** First real engine, API auth, Docker Compose for dev stack

## Known Issues Resolved

| Date | Issue | Fix |
|---|---|---|
| 2026-02-27 | Global singletons + mock.patch in tests | Refactored to FastAPI DI |
| 2026-02-27 | Raw dict returns on endpoints | Added Pydantic response models |
| 2026-02-27 | Bare ValueError/KeyError | Custom exception hierarchy |
| 2026-02-27 | 9 ruff violations | Fixed all (TCH imports, unused vars, sort) |
| 2026-02-27 | Missing Dockerfile, pre-commit | Added both |

## Changelog

### 2026-02-27 — Production-Grade Refactor
- Added `exceptions.py` — `AppError` hierarchy with structured data
- Added `deps.py` — FastAPI DI providers (replace global singletons)
- Added `api/schemas.py` — Pydantic response models for all endpoints
- Refactored `database.py` — session factory injected, not global
- Rewrote all tests to use DI overrides instead of `mock.patch`
- Added `Dockerfile` (multi-stage, uv, non-root), `.dockerignore`
- Added `.pre-commit-config.yaml` (ruff + mypy hooks)
- Added `README.md`, `docs/` (architecture, development, deployment)
- Added `AGENTS.md` (this file)
- Fixed all ruff + mypy violations
- Google-style docstrings on all public APIs
