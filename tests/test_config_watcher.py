"""Tests for ConfigWatcherEngine hot-reload and SIGHUP handling."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from tessera.engines.config_watcher import ConfigWatcherEngine
from tessera.engines.failover import FailoverEngine

if TYPE_CHECKING:
    from pathlib import Path


class TestConfigWatcherDetectsFileChanges:
    """ConfigWatcherEngine detects voter-key file modifications."""

    @pytest.fixture
    def watcher(self, tmp_path: Path) -> ConfigWatcherEngine:
        keys_file = tmp_path / "voters.json"
        keys_file.write_text('{"v1": "key1"}')
        return ConfigWatcherEngine(check_interval=1, voter_keys_file=keys_file)

    @pytest.mark.asyncio
    async def test_voter_key_change_propagates_to_failover(
        self,
        watcher: ConfigWatcherEngine,
        tmp_path: Path,
    ) -> None:
        engine = FailoverEngine(
            quorum=1,
            failover_rounds=1,
            failback_rounds=1,
            voter_keys={"v1": "key1"},
            vote_cooldown=0,
        )
        watcher.set_failover_engine(engine)

        path = tmp_path / "voters.json"
        watcher._mtimes[str(path)] = path.stat().st_mtime

        await asyncio.sleep(0.05)
        path.write_text('{"v1": "key1", "v2": "key2"}')

        await watcher._check_all_files()
        assert "v2" in engine.config["voters"]
