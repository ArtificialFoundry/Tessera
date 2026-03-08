"""Tests for pages module (server-rendered HTML routes)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tessera.pages import _build_asset_map, _scan_assets

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import AsyncClient


@pytest.fixture
def fake_dist(tmp_path: Path) -> Path:
    """Create a fake Vite dist directory."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "failover.AbCdEfGh.js").write_text("// failover")
    (dist / "dhcp.XyZwQrSt.js").write_text("// dhcp")
    (dist / "protection.AaBbCcDd.js").write_text("// protection")
    (dist / "servers.EeFfGgHh.js").write_text("// servers")
    (dist / "voters.IiJjKkLl.js").write_text("// voters")
    assets = dist / "assets"
    assets.mkdir()
    (assets / "tessera.HaSh1234.css").write_text("/* css */")
    chunks = dist / "chunks"
    chunks.mkdir()
    (chunks / "tessera.ChUnK123.js").write_text("// chunk")
    return dist


class TestScanAssets:
    """Tests for _scan_assets."""

    def test_empty_dir(self, tmp_path: Path) -> None:
        with patch("tessera.pages.DIST_DIR", tmp_path / "nope"):
            assets = _scan_assets()
        assert assets == {}

    def test_parses_vite_output(self, fake_dist: Path) -> None:
        with patch("tessera.pages.DIST_DIR", fake_dist):
            assets = _scan_assets()
        assert "failover" in assets
        assert "dhcp" in assets
        assert assets["failover"]["js"].endswith(".js")
        assert "css" in assets["failover"]
        assert "chunks" in assets["failover"]

    def test_js_regex(self) -> None:
        pattern = re.compile(r"^(\w+)\.[\w-]+\.js$")
        assert pattern.match("failover.CzXexGKR.js")
        assert pattern.match("dhcp.C_viYeS3.js")
        assert not pattern.match("some-file.js")


class TestBuildAssetMap:
    """Tests for _build_asset_map caching."""

    def test_caches_result(self, fake_dist: Path) -> None:
        import tessera.pages as pages_mod

        pages_mod._asset_map_cache = {}
        pages_mod._asset_map_ts = 0.0
        with patch("tessera.pages.DIST_DIR", fake_dist):
            m1 = _build_asset_map()
            m2 = _build_asset_map()
        assert m1 is m2  # Same cached object


@pytest.mark.asyncio
class TestPageRoutes:
    """Tests for page rendering routes."""

    async def test_failover_page(self, client: AsyncClient) -> None:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_dhcp_page(self, client: AsyncClient) -> None:
        resp = await client.get("/dhcp")
        assert resp.status_code == 200

    async def test_protection_page(self, client: AsyncClient) -> None:
        resp = await client.get("/protection")
        assert resp.status_code == 200

    async def test_servers_page(self, client: AsyncClient) -> None:
        resp = await client.get("/servers")
        assert resp.status_code == 200

    async def test_voters_page(self, client: AsyncClient) -> None:
        resp = await client.get("/voters")
        assert resp.status_code == 200
