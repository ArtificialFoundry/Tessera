"""Tests for engine registry."""

from __future__ import annotations

import pytest

from tessera.exceptions import NotFoundError
from tessera.registry import EngineHealth, EngineRegistry, EngineStatus


class FakeEngine:
    """Minimal engine for testing."""

    name: str = "fake"
    version: str = "1.0.0"
    description: str = "Fake engine"
    depends_on: tuple[str, ...] = ()

    def __init__(self, name: str = "fake") -> None:
        self.name = name
        self.health = EngineHealth()
        self._started = False
        self._stopped = False

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._stopped = True

    async def check_health(self) -> EngineHealth:
        self.health.status = EngineStatus.RUNNING
        self.health.message = "ok"
        return self.health


class FailingEngine(FakeEngine):
    """Engine whose health check throws."""

    async def check_health(self) -> EngineHealth:
        msg = "boom"
        raise RuntimeError(msg)


class DependentEngine(FakeEngine):
    """Engine that depends on another."""

    depends_on: tuple[str, ...] = ("fake",)

    def __init__(self) -> None:
        super().__init__("dependent")


class TestEngineRegistry:
    """Tests for EngineRegistry."""

    def test_register_and_get(self) -> None:
        reg = EngineRegistry()
        e = FakeEngine()
        reg.register(e)
        assert reg.get("fake") is e

    def test_get_not_found(self) -> None:
        reg = EngineRegistry()
        with pytest.raises(NotFoundError):
            reg.get("nope")

    def test_register_duplicate(self) -> None:
        reg = EngineRegistry()
        reg.register(FakeEngine())
        with pytest.raises(Exception, match="already"):
            reg.register(FakeEngine())

    def test_all_engines(self) -> None:
        reg = EngineRegistry()
        reg.register(FakeEngine("a"))
        reg.register(FakeEngine("b"))
        assert "a" in reg.all
        assert "b" in reg.all


@pytest.mark.asyncio
class TestRegistryLifecycle:
    """Tests for engine start/stop ordering."""

    async def test_start_all(self) -> None:
        reg = EngineRegistry()
        e = FakeEngine()
        reg.register(e)
        await reg.start_all()
        assert e._started

    async def test_stop_all(self) -> None:
        reg = EngineRegistry()
        e = FakeEngine()
        reg.register(e)
        await reg.start_all()
        await reg.stop_all()
        assert e._stopped

    async def test_dependency_order(self) -> None:
        reg = EngineRegistry()
        dep = DependentEngine()
        base = FakeEngine()
        # Register dependent first — start should still work
        reg.register(dep)
        reg.register(base)
        await reg.start_all()
        assert base._started
        assert dep._started


@pytest.mark.asyncio
class TestHealthCheck:
    """Tests for registry health check aggregation."""

    async def test_health_check(self) -> None:
        reg = EngineRegistry()
        reg.register(FakeEngine("a"))
        reg.register(FakeEngine("b"))
        results = await reg.health_check()
        assert "a" in results
        assert "b" in results
        assert results["a"].status == EngineStatus.RUNNING
        assert results["a"].last_checked > 0

    async def test_health_check_failure(self) -> None:
        reg = EngineRegistry()
        reg.register(FailingEngine("bad"))
        results = await reg.health_check()
        assert results["bad"].status == EngineStatus.DEGRADED
        assert results["bad"].last_checked > 0


class TestEngineHealth:
    """Tests for EngineHealth dataclass."""

    def test_defaults(self) -> None:
        h = EngineHealth()
        assert h.status == EngineStatus.REGISTERED
        assert h.message == ""
        assert h.last_checked == 0.0
        assert h.details == {}
