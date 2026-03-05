"""Application factory and lifespan management."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from tessera.deps import get_engine_registry, get_settings

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    """Start all engines on boot, stop them on shutdown."""
    registry = get_engine_registry()
    logger.info("Starting engines...")
    await registry.start_all()
    logger.info("All engines started.")
    yield
    logger.info("Stopping engines...")
    await registry.stop_all()
    logger.info("All engines stopped.")


def create_app() -> FastAPI:
    """Build and return a configured FastAPI application."""
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    from tessera.api.v1 import router as v1_router

    app.include_router(v1_router, prefix="/api/v1")

    # Serve static files (Vue SPA dashboard)
    if STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app
