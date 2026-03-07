from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

from tessera.settings_store import SettingsStore

if TYPE_CHECKING:
    from pathlib import Path


class TestSettingsStoreLocking:
    """Concurrent access to SettingsStore."""

    def test_concurrent_put_does_not_corrupt_file(self, tmp_path: Path) -> None:
        """Multiple threads writing should not corrupt data."""
        store = SettingsStore(tmp_path / "settings.json")
        errors: list[Exception] = []

        def writer(section: str, n: int) -> None:
            try:
                for i in range(50):
                    store.put(section, {"counter": i, "thread": n})
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(f"s{i}", i)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        data = json.loads((tmp_path / "settings.json").read_text())
        assert isinstance(data, dict)
        for i in range(8):
            assert f"s{i}" in data

    def test_separate_instances_use_independent_locks(self, tmp_path: Path) -> None:
        """Each SettingsStore has its own lock."""
        s1 = SettingsStore(tmp_path / "a.json")
        s2 = SettingsStore(tmp_path / "b.json")
        assert s1._lock is not s2._lock
