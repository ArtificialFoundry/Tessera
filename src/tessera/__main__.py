"""Entry point for ``python -m tessera``."""

from __future__ import annotations

from tessera.app import create_app

app = create_app()
