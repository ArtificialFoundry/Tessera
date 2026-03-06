"""Application factory and lifespan management."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from tessera.deps import get_engine_registry, get_settings
from tessera.engines.failover import FailoverEngine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Generator

# ---------------------------------------------------------------------------
# Request-ID context var
# ---------------------------------------------------------------------------
request_id_ctx: ContextVar[str] = ContextVar("request_id", default="")

STATIC_DIR = Path(__file__).parent / "static"

# Security headers applied to every response
_SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-XSS-Protection": "0",
}


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

class _RequestIdFilter(logging.Filter):
    """Inject ``request_id`` into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get("")
        return True


def _configure_logging(*, debug: bool = False) -> None:
    """Set up structured JSON logging (or human-readable in debug mode)."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)

    # Remove any existing handlers
    root.handlers.clear()

    handler = logging.StreamHandler()
    handler.addFilter(_RequestIdFilter())

    if debug:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] "
                "[%(request_id)s] %(message)s",
            )
        )
    else:
        from pythonjsonlogger.json import JsonFormatter

        handler.setFormatter(
            JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(request_id)s %(message)s",
                rename_fields={"asctime": "timestamp", "levelname": "level"},
            )
        )

    root.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)


@contextmanager
def _suppress_import() -> Generator[None]:
    """Silence import-time logging until we configure it ourselves."""
    yield


# Eagerly configure with defaults; overwritten in create_app once settings load.
_configure_logging(debug=False)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Start all engines on boot, stop them on shutdown."""
    registry = get_engine_registry()
    logger.info("Starting engines...")
    await registry.start_all()

    # Populate failover engine with scope names from primary
    try:
        from tessera.engines.technitium import TechnitiumClient

        primary = registry.get("technitium")
        failover = registry.get("failover")
        if isinstance(primary, TechnitiumClient) and isinstance(
            failover, FailoverEngine
        ):
            scopes = await primary.list_scopes()
            names = [s["name"] for s in scopes if s.get("name")]
            failover.set_scope_names(names)
            logger.info("Failover managing %d scopes: %s", len(names), names)
    except Exception:
        logger.exception("Failed to load scope names for failover engine")

    logger.info("All engines started.")
    yield
    logger.info("Stopping engines...")
    await registry.stop_all()
    logger.info("All engines stopped.")


def create_app() -> FastAPI:
    """Build and return a configured FastAPI application."""
    settings = get_settings()

    # Reconfigure logging with actual settings
    _configure_logging(debug=settings.debug)

    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    from tessera.api.v1 import router as v1_router
    from tessera.pages import router as pages_router

    app.include_router(v1_router, prefix="/api/v1")
    app.include_router(pages_router)

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Any],
    ) -> Response:
        """Generate a request ID, store in context, and add response header."""
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_ctx.set(rid)
        try:
            response: Response = await call_next(request)
        finally:
            request_id_ctx.reset(token)
        response.headers["X-Request-ID"] = rid
        return response

    @app.middleware("http")
    async def security_headers_middleware(
        request: Request,
        call_next: Callable[[Request], Any],
    ) -> Response:
        """Attach security headers to every response."""
        response: Response = await call_next(request)
        for header, value in _SECURITY_HEADERS.items():
            response.headers[header] = value
        # Static assets get aggressive cache
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=86400, immutable"
        return response

    # Serve static assets (CSS, JS)
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app
