"""Persistent settings store.

Provides a file-backed JSON store for engine settings that must survive
container rebuilds.  Settings are written atomically (write-to-temp +
rename) to avoid corruption on crash.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


class SettingsStore:
    """File-backed JSON settings store.

    Args:
        path: Path to the JSON settings file.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        """Load settings from disk."""
        with self._lock:
            if self._path.is_file():
                try:
                    self._data = json.loads(self._path.read_text())
                    logger.debug("Settings loaded from %s", self._path)
                except (json.JSONDecodeError, OSError):
                    logger.warning(
                        "Corrupt settings file %s, starting fresh",
                        self._path,
                    )
                    self._data = {}
            else:
                self._data = {}

    def _save(self) -> None:
        """Atomically write settings to disk."""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            try:
                tmp.write_text(json.dumps(self._data, indent=2))
                tmp.replace(self._path)
            except OSError:
                logger.exception("Failed to save settings to %s", self._path)

    def get(self, section: str) -> dict[str, Any]:
        """Get a settings section.

        Args:
            section: Section name (e.g. 'backup', 'enforcement').

        Returns:
            Settings dict for the section (empty dict if missing).
        """
        return dict(self._data.get(section, {}))

    def put(self, section: str, values: dict[str, Any]) -> None:
        """Update a settings section and persist to disk.

        Args:
            section: Section name.
            values: Key-value pairs to merge into the section.
        """
        if section not in self._data:
            self._data[section] = {}
        self._data[section].update(values)
        self._save()

    def delete(self, section: str) -> None:
        """Remove a settings section.

        Args:
            section: Section name to remove.
        """
        self._data.pop(section, None)
        self._save()
