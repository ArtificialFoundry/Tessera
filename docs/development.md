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
uv run pytest                       # 99 tests
uv run ruff check src/ tests/       # linting
uv run ruff format src/ tests/      # formatting
uv run mypy src/                    # type checking
```

## Frontend Build

```bash
cd frontend && npx vite build
```

Output lands in `src/tessera/static/dist/` (hashed filenames). The backend's `_build_asset_map()` auto-discovers bundles at runtime.

## Running Locally

```bash
uv run uvicorn tessera.app:app --reload --port 8780
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

## Conventions

- **Commits:** always use `-sS` flags (GPG sign + DCO sign-off)
- **Engines:** subclass `Engine`, declare `depends_on`, register in `deps.py`
- **API routes:** under `src/tessera/api/v1/`, Pydantic schemas in `schemas.py`
- **Frontend pages:** one Preact island per route in `frontend/src/pages/`
- **Signals:** use `@preact/signals` for global state, not component state
- **Types:** strict typing everywhere — mypy must pass with zero errors
