"""Tests for config watcher engine."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from tessera.engines.config_watcher import ConfigWatcherEngine
from tessera.registry import EngineStatus

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def tmp_servers_file(tmp_path: Path) -> Path:
    """Create a temporary servers JSON file."""
    f = tmp_path / "servers.json"
    f.write_text(json.dumps([]))
    return f


@pytest.fixture
def tmp_token_file(tmp_path: Path) -> Path:
    """Create a temporary token file."""
    f = tmp_path / "token"
    f.write_text("test-token-123")
    return f


@pytest.fixture
def tmp_reg_tokens_file(tmp_path: Path) -> Path:
    """Create a temporary registration tokens file."""
    f = tmp_path / "reg_tokens.json"
    f.write_text(json.dumps({}))
    return f


class TestConfigWatcherInit:
    """Tests for ConfigWatcherEngine initialization."""

    def test_default_init(self) -> None:
        engine = ConfigWatcherEngine()
        assert engine.name == "config_watcher"
        assert engine._check_interval == 10
        assert engine._reload_count == 0
        assert engine._consecutive_parse_errors == 0

    def test_init_with_paths(
        self,
        tmp_servers_file: Path,
        tmp_token_file: Path,
        tmp_reg_tokens_file: Path,
    ) -> None:
        engine = ConfigWatcherEngine(
            servers_file=tmp_servers_file,
            token_file=tmp_token_file,
            reg_tokens_file=tmp_reg_tokens_file,
            check_interval=30,
        )
        assert engine._servers_file == tmp_servers_file
        assert engine._token_file == tmp_token_file
        assert engine._check_interval == 30


class TestWatchedPaths:
    """Tests for _watched_paths method."""

    def test_no_paths(self) -> None:
        engine = ConfigWatcherEngine()
        assert engine._watched_paths() == []

    def test_all_paths(
        self,
        tmp_servers_file: Path,
        tmp_token_file: Path,
        tmp_reg_tokens_file: Path,
    ) -> None:
        engine = ConfigWatcherEngine(
            servers_file=tmp_servers_file,
            token_file=tmp_token_file,
            reg_tokens_file=tmp_reg_tokens_file,
        )
        paths = engine._watched_paths()
        assert len(paths) == 3


class TestGetMtime:
    """Tests for _get_mtime static method."""

    def test_existing_file(self, tmp_token_file: Path) -> None:
        mtime = ConfigWatcherEngine._get_mtime(tmp_token_file)
        assert mtime > 0

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        mtime = ConfigWatcherEngine._get_mtime(tmp_path / "nope")
        assert mtime == 0.0


class TestSetters:
    """Tests for engine setter methods."""

    def test_set_failover_engine(self) -> None:
        engine = ConfigWatcherEngine()
        mock = MagicMock()
        engine.set_failover_engine(mock)
        assert engine._failover_engine is mock

    def test_set_pool(self) -> None:
        engine = ConfigWatcherEngine()
        mock = MagicMock()
        engine.set_pool(mock)
        assert engine._pool is mock

    def test_set_voter_registry(self) -> None:
        engine = ConfigWatcherEngine()
        mock = MagicMock()
        engine.set_voter_registry(mock)
        assert engine._voter_registry is mock


@pytest.mark.asyncio
class TestCheckAllFiles:
    """Tests for file change detection."""

    async def test_detects_change(self, tmp_token_file: Path) -> None:
        engine = ConfigWatcherEngine(token_file=tmp_token_file)
        # Record initial mtime
        engine._mtimes[str(tmp_token_file)] = 0.0

        pool_mock = MagicMock()
        pool_mock.get_all.return_value = []
        engine.set_pool(pool_mock)

        await engine._check_all_files()
        # Should have detected the change and reloaded
        assert engine._reload_count == 1
        assert engine._consecutive_parse_errors == 0

    async def test_no_change(self, tmp_token_file: Path) -> None:
        engine = ConfigWatcherEngine(token_file=tmp_token_file)
        # Record correct mtime
        engine._mtimes[str(tmp_token_file)] = tmp_token_file.stat().st_mtime
        await engine._check_all_files()
        assert engine._reload_count == 0


@pytest.mark.asyncio
class TestReloadFile:
    """Tests for config file reload logic."""

    async def test_reload_token(self, tmp_token_file: Path) -> None:
        engine = ConfigWatcherEngine(token_file=tmp_token_file)
        client = MagicMock()
        client._token = "old"
        pool = MagicMock()
        pool.get_all.return_value = [client]
        engine.set_pool(pool)

        await engine._reload_file(tmp_token_file)
        assert client._token == "test-token-123"
        assert engine._reload_count == 1

    async def test_reload_servers_valid(self, tmp_servers_file: Path) -> None:
        tmp_servers_file.write_text(
            json.dumps(
                [
                    {
                        "name": "s1",
                        "url": "https://1.2.3.4:53443",
                        "role": "active",
                    }
                ]
            )
        )
        engine = ConfigWatcherEngine(servers_file=tmp_servers_file)
        pool = MagicMock()
        pool.update_servers.return_value = ["added s1"]
        pool.get_client.return_value = None
        engine.set_pool(pool)

        await engine._reload_file(tmp_servers_file)
        assert engine._reload_count == 1
        pool.update_servers.assert_called_once()

    async def test_reload_servers_parse_error(self, tmp_servers_file: Path) -> None:
        tmp_servers_file.write_text("{bad json")
        engine = ConfigWatcherEngine(servers_file=tmp_servers_file)
        await engine._reload_file(tmp_servers_file)
        assert engine._consecutive_parse_errors == 1
        assert "Parse error" in engine._last_error

    async def test_reload_servers_not_array(self, tmp_servers_file: Path) -> None:
        tmp_servers_file.write_text(json.dumps({"not": "array"}))
        engine = ConfigWatcherEngine(servers_file=tmp_servers_file)
        pool = MagicMock()
        engine.set_pool(pool)
        await engine._reload_file(tmp_servers_file)
        assert engine._consecutive_parse_errors == 1

    async def test_consecutive_errors_accumulate(self, tmp_servers_file: Path) -> None:
        tmp_servers_file.write_text("{bad")
        engine = ConfigWatcherEngine(servers_file=tmp_servers_file)
        await engine._reload_file(tmp_servers_file)
        await engine._reload_file(tmp_servers_file)
        await engine._reload_file(tmp_servers_file)
        assert engine._consecutive_parse_errors == 3

    async def test_reload_reg_tokens(self, tmp_reg_tokens_file: Path) -> None:
        engine = ConfigWatcherEngine(reg_tokens_file=tmp_reg_tokens_file)
        await engine._reload_file(tmp_reg_tokens_file)
        assert engine._reload_count == 1


@pytest.mark.asyncio
class TestHealthCheck:
    """Tests for config watcher health reporting."""

    async def test_healthy(self) -> None:
        engine = ConfigWatcherEngine()
        h = await engine.check_health()
        assert h.status == EngineStatus.RUNNING
        assert "0 reloads" in h.message

    async def test_degraded_after_errors(self) -> None:
        engine = ConfigWatcherEngine()
        engine._consecutive_parse_errors = 3
        engine._last_error = "Parse error in servers.json"
        h = await engine.check_health()
        assert h.status == EngineStatus.DEGRADED

    async def test_running_with_error(self) -> None:
        engine = ConfigWatcherEngine()
        engine._consecutive_parse_errors = 1
        engine._last_error = "Parse error"
        h = await engine.check_health()
        assert h.status == EngineStatus.RUNNING
        assert "Last error" in h.message


class TestMetrics:
    """Tests for config watcher metrics."""

    def test_metrics(self) -> None:
        engine = ConfigWatcherEngine(check_interval=20)
        m = engine.get_metrics()
        assert m["reload_count"] == 0
        assert m["check_interval"] == 20
        assert m["consecutive_parse_errors"] == 0


@pytest.mark.asyncio
class TestStartStop:
    """Tests for engine lifecycle."""

    async def test_start_stop(self, tmp_token_file: Path) -> None:
        engine = ConfigWatcherEngine(token_file=tmp_token_file, check_interval=1)
        await engine.start()
        assert engine._task is not None
        await engine.stop()
        assert engine._task is None
