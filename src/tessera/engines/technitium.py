"""Technitium DNS Server DHCP API client.

Async HTTP client wrapping the Technitium DHCP API endpoints.
SSL verification is disabled for self-signed certificates.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from tessera.exceptions import TechnitiumError
from tessera.registry import Engine, EngineHealth, EngineStatus

logger = logging.getLogger(__name__)


class TechnitiumClient(Engine):
    """Async HTTP client for Technitium DNS Server's DHCP API.

    Attributes:
        name: Engine identifier.
        version: Engine version.
        description: Human-readable description.
    """

    name: str = "technitium"
    version: str = "1.0.0"
    description: str = "Technitium DNS Server DHCP API client"

    def __init__(self, base_url: str, token: str) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client: httpx.AsyncClient | None = None

    async def start(self) -> None:
        """Initialize the HTTP client."""
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            verify=False,
            timeout=httpx.Timeout(30.0),
        )
        logger.info("TechnitiumClient started for %s", self._base_url)

    async def stop(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("TechnitiumClient stopped")

    async def check_health(self) -> EngineHealth:
        """Check connectivity to Technitium."""
        try:
            await self._request("GET", "/api/dhcp/scopes/list")
            self.health.status = EngineStatus.RUNNING
            self.health.message = "Connected"
        except Exception as exc:
            self.health.status = EngineStatus.DEGRADED
            self.health.message = str(exc)
        return self.health

    @property
    def client(self) -> httpx.AsyncClient:
        """Return the underlying HTTP client.

        Raises:
            TechnitiumError: If client is not started.
        """
        if self._client is None:
            raise TechnitiumError("Client not started", status_code=0)
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make an authenticated request to Technitium.

        Args:
            method: HTTP method.
            path: API path.
            params: Query parameters.
            data: Form data for POST requests.

        Returns:
            Parsed JSON response.

        Raises:
            TechnitiumError: On HTTP or API errors.
        """
        all_params = {"token": self._token}
        if params:
            all_params.update(params)

        try:
            if method == "GET":
                resp = await self.client.get(path, params=all_params)
            else:
                form_data = dict(all_params)
                if data:
                    form_data.update(data)
                resp = await self.client.post(path, data=form_data)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TechnitiumError(
                f"HTTP {exc.response.status_code}: {exc.response.text}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.HTTPError as exc:
            raise TechnitiumError(str(exc)) from exc

        result: dict[str, Any] = resp.json()
        if result.get("status") == "error":
            raise TechnitiumError(
                result.get("errorMessage", "Unknown API error"),
            )
        return result

    async def list_scopes(self) -> list[dict[str, Any]]:
        """List all DHCP scopes.

        Returns:
            List of scope objects from Technitium.
        """
        result = await self._request("GET", "/api/dhcp/scopes/list")
        response: dict[str, Any] = result.get("response", {})
        scopes: list[dict[str, Any]] = response.get("scopes", [])
        return scopes

    async def get_scope(self, name: str) -> dict[str, Any]:
        """Get detailed scope information including reservations.

        Args:
            name: Scope name.

        Returns:
            Scope detail object.
        """
        result = await self._request(
            "GET", "/api/dhcp/scopes/get", params={"name": name}
        )
        response: dict[str, Any] = result.get("response", {})
        return response

    async def set_scope(self, name: str, settings: dict[str, Any]) -> None:
        """Update scope settings.

        Args:
            name: Scope name.
            settings: Key-value settings to update.
        """
        data = {"name": name, **settings}
        await self._request("POST", "/api/dhcp/scopes/set", data=data)

    async def enable_scope(self, name: str) -> None:
        """Enable a DHCP scope.

        Args:
            name: Scope name.
        """
        await self._request("POST", "/api/dhcp/scopes/enable", data={"name": name})

    async def disable_scope(self, name: str) -> None:
        """Disable a DHCP scope.

        Args:
            name: Scope name.
        """
        await self._request("POST", "/api/dhcp/scopes/disable", data={"name": name})

    async def add_reservation(
        self,
        scope_name: str,
        *,
        hardware_address: str,
        address: str,
        host_name: str = "",
        comments: str = "",
    ) -> None:
        """Add a DHCP reservation to a scope.

        Args:
            scope_name: Target scope name.
            hardware_address: MAC address.
            address: Reserved IP address.
            host_name: Optional hostname.
            comments: Optional comments.
        """
        data: dict[str, Any] = {
            "name": scope_name,
            "hardwareAddress": hardware_address,
            "address": address,
        }
        if host_name:
            data["hostName"] = host_name
        if comments:
            data["comments"] = comments
        await self._request("POST", "/api/dhcp/scopes/addReservedLease", data=data)

    async def remove_reservation(
        self, scope_name: str, *, hardware_address: str
    ) -> None:
        """Remove a DHCP reservation from a scope.

        Args:
            scope_name: Target scope name.
            hardware_address: MAC address to remove.
        """
        await self._request(
            "POST",
            "/api/dhcp/scopes/removeReservedLease",
            data={"name": scope_name, "hardwareAddress": hardware_address},
        )

    async def get_leases(self, scope_name: str) -> list[dict[str, Any]]:
        """Get active leases for a scope.

        Args:
            scope_name: Scope name.

        Returns:
            List of lease objects.
        """
        result = await self._request(
            "GET", "/api/dhcp/leases/list", params={"name": scope_name}
        )
        response: dict[str, Any] = result.get("response", {})
        leases: list[dict[str, Any]] = response.get("leases", [])
        return leases
