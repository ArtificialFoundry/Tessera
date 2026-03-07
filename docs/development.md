# Development

## Setup

```bash
# Python backend
uv sync --all-extras

# Frontend
cd frontend && npm install
```

## Quality Checks

All must pass before committing:

```bash
uv run pytest                       # Tests
uv run ruff check src/ tests/       # Linting
uv run ruff format src/ tests/      # Formatting
uv run mypy src/                    # Type checking (strict)
```

## Frontend Build

```bash
cd frontend && npx vite build
```

Output lands in `src/tessera/static/dist/` (hashed filenames with `[\w-]+` pattern). The backend's `_build_asset_map()` auto-discovers bundles at runtime with a 5-minute TTL cache.

## Running Locally

```bash
# Backend (with hot reload)
uv run uvicorn tessera.app:create_app --factory --reload --port 8780

# Frontend dev server (separate terminal)
cd frontend && npm run dev
```

Note: requires access to Technitium instances (or mock them in tests).

## Testing

Tests use FastAPI's `TestClient` with DI overrides. Engines are mocked via `conftest.py` fixtures.

```bash
# Run specific test file
uv run pytest tests/test_failover.py -v

# With coverage
uv run pytest --cov=tessera --cov-report=term-missing
```

## Key Patterns

### Auth separation

- **Read operations:** Use `request()` — no auth required
- **Write operations:** Use `adminRequest()` — sends `Authorization: Bearer <token>`
- Backend: `require_admin` dependency on write endpoints checks `TESSERA_ADMIN_API_KEY`

### Frontend pages

Each page is a standalone Preact island — one entry point in `vite.config.ts`, one `render()` call per page. No client-side routing.

- **Signals** for global state (toasts, modals, ping, auth token)
- **`poll()`** helper with `consecutiveErrors` + `lastUpdated` tracking
- **`showConfirm()`** for destructive actions
- **`isAuthCancelled()`** guard in all catch blocks to suppress toast on auth cancel

### Voter script

- `set -euo pipefail` — strict error handling
- `|| true` on grep patterns that may not match (prevents `set -e` exit)
- Dual health checks: HTTP primary, DHCP secondary (nmap optional)
- `DHCP_TIMEOUT` separate from `CHECK_TIMEOUT` for independent tuning

## Conventions

- **Engines:** subclass `Engine`, declare `depends_on`, register in `deps.py`
- **API routes:** under `src/tessera/api/v1/`, Pydantic schemas in `schemas.py`
- **Frontend pages:** one Preact island per route in `frontend/src/pages/`
- **Signals:** use `@preact/signals` for global state, not component state
- **Types:** strict typing everywhere — mypy must pass with zero errors
- **Errors:** `ValidationError` for 400s, `AppError` hierarchy for domain errors
