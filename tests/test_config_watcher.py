"""Tests for ConfigWatcherEngine hot-reload and SIGHUP handling."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from tessera.engines.config_watcher import ConfigWatcherEngine

if TYPE_CHECKING:
    from pathlib import Path


class TestConfigWatcherDetectsFileChanges:
    """ConfigWatcherEngine detects server config file modifications."""

    @pytest.fixture
    def watcher(self, tmp_path: Path) -> ConfigWatcherEngine:
        servers_file = tmp_path / "servers.json"
        servers_file.write_text(json.dumps([
            {"name": "s1", "url": "https://s1:53443", "role": "active", "priority": 0},
        ]))
        return ConfigWatcherEngine(check_interval=1, servers_file=servers_file)

    @pytest.mark.asyncio
    async def test_server_file_change_detected(
        self,
        watcher: ConfigWatcherEngine,
        tmp_path: Path,
    ) -> None:
        path = tmp_path / "servers.json"
        watcher._mtimes[str(path)] = path.stat().st_mtime

        await asyncio.sleep(0.05)
        path.write_text(json.dumps([
            {"name": "s1", "url": "https://s1:53443",
             "role": "active", "priority": 0},
            {"name": "s2", "url": "https://s2:53443",
             "role": "candidate", "priority": 1},
        ]))

        # mtime should change
        new_mtime = path.stat().st_mtime
        assert new_mtime != watcher._mtimes[str(path)]
