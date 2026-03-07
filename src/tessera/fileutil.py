"""Shared file I/O utilities.

Provides atomic write operations to prevent data corruption from
partial writes, crashes, or disk-full conditions.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically.

    Writes to a temporary file in the same directory, then renames
    to the target path. This ensures the file is never in a partial
    state — either the old content or the new content, never a mix.

    Args:
        path: Target file path.
        content: String content to write.

    Raises:
        OSError: If the write or rename fails.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
    tmp = Path(tmp_path)
    try:
        tmp.write_text(content)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    finally:
        # fd was opened by mkstemp; close it (write_text opens its own)
        import contextlib
        import os

        with contextlib.suppress(OSError):
            os.close(fd)
