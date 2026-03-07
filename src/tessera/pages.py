"""Server-rendered page routes.

Serves thin HTML shells that mount per-page Vite-built Preact bundles.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATE_DIR = Path(__file__).parent / "templates"
DIST_DIR = Path(__file__).parent / "static" / "dist"
templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
router = APIRouter()

_ASSET_MAP_TTL = 300  # seconds — rebuild asset map after this duration
_asset_map_cache: dict[str, dict[str, str]] = {}
_asset_map_ts: float = 0.0


def _build_asset_map() -> dict[str, dict[str, str]]:
    """Return cached asset map, rebuilding when TTL expires."""
    global _asset_map_cache, _asset_map_ts
    now = time.monotonic()
    if _asset_map_cache and (now - _asset_map_ts) < _ASSET_MAP_TTL:
        return _asset_map_cache
    _asset_map_cache = _scan_assets()
    _asset_map_ts = now
    return _asset_map_cache


def _scan_assets() -> dict[str, dict[str, str]]:
    """Scan dist/ for hashed filenames and build entry→path mapping.

    Returns:
        Mapping of entry name → {js, css} paths relative to /static/dist/.
    """
    assets: dict[str, dict[str, str]] = {}
    if not DIST_DIR.is_dir():
        return assets

    # JS entries: failover.CzXexGKR.js → failover
    for f in DIST_DIR.glob("*.js"):
        m = re.match(r"^(\w+)\.\w+\.js$", f.name)
        if m:
            name = m.group(1)
            assets.setdefault(name, {})["js"] = f"/static/dist/{f.name}"

    # Shared CSS from assets/
    assets_dir = DIST_DIR / "assets"
    if assets_dir.is_dir():
        for f in assets_dir.glob("*.css"):
            # Shared CSS applies to all pages
            for name in assets:
                assets[name]["css"] = f"/static/dist/assets/{f.name}"

    # Chunks (shared Preact bundle)
    chunks_dir = DIST_DIR / "chunks"
    if chunks_dir.is_dir():
        for f in chunks_dir.glob("*.js"):
            for name in assets:
                assets[name].setdefault("chunks", "")
                assets[name]["chunks"] = f"/static/dist/chunks/{f.name}"

    return assets


def _ctx(request: Request, tab: str) -> dict[str, object]:
    """Build template context with resolved asset paths."""
    asset_map = _build_asset_map()
    entry = asset_map.get(tab, {})
    return {
        "request": request,
        "active_tab": tab,
        "js": entry.get("js", ""),
        "css": entry.get("css", ""),
        "chunk_js": entry.get("chunks", ""),
    }


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/failover", response_class=HTMLResponse, include_in_schema=False)
async def failover_page(request: Request) -> HTMLResponse:
    """Render the failover dashboard page."""
    return templates.TemplateResponse("page.html", _ctx(request, "failover"))


@router.get("/dhcp", response_class=HTMLResponse, include_in_schema=False)
async def dhcp_page(request: Request) -> HTMLResponse:
    """Render the DHCP management page."""
    return templates.TemplateResponse("page.html", _ctx(request, "dhcp"))


@router.get("/protection", response_class=HTMLResponse, include_in_schema=False)
async def protection_page(request: Request) -> HTMLResponse:
    """Render the protection (backup/enforcement) page."""
    return templates.TemplateResponse("page.html", _ctx(request, "protection"))


@router.get("/servers", response_class=HTMLResponse, include_in_schema=False)
async def servers_page(request: Request) -> HTMLResponse:
    """Render the servers management page."""
    return templates.TemplateResponse("page.html", _ctx(request, "servers"))


@router.get("/voters", response_class=HTMLResponse, include_in_schema=False)
async def voters_page(request: Request) -> HTMLResponse:
    """Render the voter management page."""
    return templates.TemplateResponse("page.html", _ctx(request, "voters"))
