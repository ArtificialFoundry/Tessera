"""Engine and module registries.

The **module registry** tracks data models (SQLAlchemy tables).
The **engine registry** tracks business-logic units with lifecycle hooks
(start, stop, health-check) and dependency-ordered startup.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Any

from tessera.exceptions import (
    DependencyError,
    DuplicateEntryError,
    NotFoundError,
)

if TYPE_CHECKING:
    from typing import Any as DeclarativeBase

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ModuleDescriptor:
    """Describes a registered data module.

    Attributes:
        name: Unique module identifier.
        version: Semantic version string.
        models: Tuple of ORM model classes belonging to this module.
        description: Human-readable summary.
    """

    name: str
    version: str
    models: tuple[type[DeclarativeBase], ...]
    description: str = ""


class ModuleRegistry:
    """Registry of data modules (groups of related SQL tables)."""

    def __init__(self) -> None:
        self._modules: dict[str, ModuleDescriptor] = {}

    def register(self, descriptor: ModuleDescriptor) -> None:
        """Register a data module.

        Args:
            descriptor: The module to register.

        Raises:
            DuplicateEntryError: If a module with the same name exists.
        """
        if descriptor.name in self._modules:
            raise DuplicateEntryError("Module", descriptor.name)
        self._modules[descriptor.name] = descriptor
        logger.info(
            "Module registered: %s v%s (%d models)",
            descriptor.name,
            descriptor.version,
            len(descriptor.models),
        )

    def get(self, name: str) -> ModuleDescriptor:
        """Look up a module by name.

        Args:
            name: The module identifier.

        Returns:
            The matching descriptor.

        Raises:
            NotFoundError: If no module with *name* exists.
        """
        try:
            return self._modules[name]
        except KeyError:
            raise NotFoundError("Module", name) from None

    @property
    def all(self) -> dict[str, ModuleDescriptor]:
        """Return a shallow copy of all registered modules."""
        return dict(self._modules)


class EngineStatus(StrEnum):
    """Lifecycle status of an engine."""

    REGISTERED = auto()
    STARTING = auto()
    RUNNING = auto()
    DEGRADED = auto()
    STOPPED = auto()
    FAILED = auto()


@dataclass(slots=True)
class EngineHealth:
    """Point-in-time health snapshot of an engine.

    Attributes:
        status: Current lifecycle status.
        message: Human-readable status note.
        details: Arbitrary key/value diagnostic data.
        last_checked: Unix timestamp of last health evaluation.
    """

    status: EngineStatus = EngineStatus.REGISTERED
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    last_checked: float = 0.0


class Engine:
    """Abstract base for business-logic engines.

    Subclass and override lifecycle hooks as needed.
    """

    name: str = "unnamed"
    version: str = "0.0.0"
    description: str = ""
    depends_on: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.health = EngineHealth()

    async def start(self) -> None:
        """Called once during application startup."""

    async def stop(self) -> None:
        """Called once during application shutdown."""

    async def check_health(self) -> EngineHealth:
        """Return current health."""
        return self.health

    def get_metrics(self) -> dict[str, Any]:
        """Return operational metrics."""
        return {}


class EngineRegistry:
    """Registry of engines with dependency-ordered lifecycle management."""

    def __init__(self) -> None:
        self._engines: dict[str, Engine] = {}

    def register(self, engine: Engine) -> None:
        """Register an engine.

        Args:
            engine: The engine instance to register.

        Raises:
            DuplicateEntryError: If an engine with the same name exists.
        """
        if engine.name in self._engines:
            raise DuplicateEntryError("Engine", engine.name)
        self._engines[engine.name] = engine
        engine.health.status = EngineStatus.REGISTERED
        logger.info("Engine registered: %s v%s", engine.name, engine.version)

    def get(self, name: str) -> Engine:
        """Look up an engine by name.

        Args:
            name: The engine identifier.

        Returns:
            The matching engine instance.

        Raises:
            NotFoundError: If no engine with *name* exists.
        """
        try:
            return self._engines[name]
        except KeyError:
            raise NotFoundError("Engine", name) from None

    @property
    def all(self) -> dict[str, Engine]:
        """Return a shallow copy of all registered engines."""
        return dict(self._engines)

    async def start_all(self) -> None:
        """Start every engine, respecting ``depends_on`` order.

        Raises:
            DependencyError: If an engine depends on an unregistered engine.
        """
        started: set[str] = set()
        for engine in self._engines.values():
            await self._start_engine(engine, started)

    async def _start_engine(self, engine: Engine, started: set[str]) -> None:
        """Recursively start *engine* after its dependencies."""
        if engine.name in started:
            return
        for dep in engine.depends_on:
            if dep not in self._engines:
                raise DependencyError(engine.name, dep)
            await self._start_engine(self._engines[dep], started)

        engine.health.status = EngineStatus.STARTING
        try:
            await engine.start()
            engine.health.status = EngineStatus.RUNNING
            started.add(engine.name)
            logger.info("Engine started: %s", engine.name)
        except Exception:
            engine.health.status = EngineStatus.FAILED
            logger.exception("Engine failed to start: %s", engine.name)
            raise

    async def stop_all(self) -> None:
        """Stop all engines in reverse registration order."""
        for name in reversed(list(self._engines)):
            engine = self._engines[name]
            try:
                await engine.stop()
                engine.health.status = EngineStatus.STOPPED
                logger.info("Engine stopped: %s", name)
            except Exception:
                logger.exception("Error stopping engine: %s", name)

    async def health_check(self) -> dict[str, EngineHealth]:
        """Run health checks on every engine.

        Returns:
            Mapping of engine name to its health snapshot.
        """
        now = time.time()
        results: dict[str, EngineHealth] = {}
        for name, engine in self._engines.items():
            try:
                h = await engine.check_health()
                h.last_checked = now
                results[name] = h
            except Exception:
                logger.exception("Health check failed: %s", name)
                results[name] = EngineHealth(
                    status=EngineStatus.DEGRADED,
                    message="Health check threw an exception",
                    last_checked=now,
                )
        return results
