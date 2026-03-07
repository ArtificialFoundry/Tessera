"""Tests for atomic file write utility."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tessera.fileutil import atomic_write

if TYPE_CHECKING:
    from pathlib import Path


class TestAtomicWrite:
    """atomic_write guarantees no partial writes."""

    def test_writes_content(self, tmp_path: Path) -> None:
        target = tmp_path / "test.json"
        atomic_write(target, '{"key": "value"}')
        assert target.read_text() == '{"key": "value"}'

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "test.json"
        target.write_text("old")
        atomic_write(target, "new")
        assert target.read_text() == "new"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "a" / "b" / "test.json"
        atomic_write(target, "nested")
        assert target.read_text() == "nested"

    def test_no_temp_file_on_success(self, tmp_path: Path) -> None:
        target = tmp_path / "test.json"
        atomic_write(target, "content")
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0
