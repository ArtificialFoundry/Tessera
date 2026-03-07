"""Technitium DNS Server DHCP API client and multi-server pool.

Async HTTP client wrapping the Technitium DHCP API endpoints.
SSL verification is disabled for self-signed certificates.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, runtime_checkable

import httpx

from tessera.exceptions import TechnitiumError
from tessera.registry import Engine, EngineHealth, EngineStatus

if TYPE_CHECKING:
    from pathlib import Path

    from tessera.config import DhcpServer

logger = logging.getLogger(__name__)


@runtime_checkable
class DhcpClientProtocol(Protocol):
    """Structural protocol for DHCP client implementations."""

    async def list_scopes(self) -> list[dict[str, Any]]: ...
    async def get_scope(self, name: str) -> dict[str, Any]: ...
    async def get_leases(self, scope_name: str) -> list[dict[str, Any]]: ...
    async def set_scope(self, name: str, settings: dict[str, str]) -> None: ...
    async def add_reservation(
        self,
        scope_name: str,
        *,
        hardware_address: str,
        address: str,
        host_name: str,
        comments: str,
    ) -> None: ...
    async def remove_reservation(
        self, scope_name: str, *, hardware_address: str
    ) -> None: ...


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

    _tls_warned_urls: ClassVar[set[str]] = set()

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        server_name: str = "",
        ca_cert_file: str = "",
    ) -> None:
        super().__init__()
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._client: httpx.AsyncClient | None = None
        self.server_name = server_name
        self._ca_cert_file = ca_cert_file

    async def start(self) -> None:
        """Initialize the HTTP client."""
        verify: bool | str = False
        if self._ca_cert_file:
            verify = self._ca_cert_file
        if verify is False and self._base_url not in TechnitiumClient._tls_warned_urls:
            TechnitiumClient._tls_warned_urls.add(self._base_url)
            logger.warning(
                "TLS verification disabled for %s "
                "— connections are vulnerable to MITM attacks",
                self._base_url,
            )
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            verify=verify,
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
        """Make an authenticated request to Technitium with retry.

        Args:
            method: HTTP method.
            path: API path.
            params: Query parameters.
            data: Form data for POST requests.

        Returns:
            Parsed JSON response.

        Raises:
            TechnitiumError: On HTTP or API errors after retries exhausted.
        """
        all_params = {"token": self._token}
        if params:
            all_params.update(params)

        _max_attempts = 3
        _backoff = [0.5, 1.0, 2.0]
        last_exc: Exception | None = None

        for attempt in range(_max_attempts):
            try:
                if method == "GET":
                    resp = await self.client.get(path, params=all_params)
                else:
                    form_data = dict(all_params)
                    if data:
                        form_data.update(data)
                    resp = await self.client.post(path, data=form_data)

                if resp.status_code in (502, 503, 504):
                    raise httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}",
                        request=resp.request,
                        response=resp,
                    )

                resp.raise_for_status()
            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                last_exc = exc
                if attempt < _max_attempts - 1:
                    logger.warning(
                        "Technitium request %s %s failed "
                        "(attempt %d/%d): %s — retrying",
                        method, path, attempt + 1,
                        _max_attempts, exc,
                    )
                    await asyncio.sleep(_backoff[attempt])
                    continue
                raise TechnitiumError(str(exc)) from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (502, 503, 504):
                    last_exc = exc
                    if attempt < _max_attempts - 1:
                        logger.warning(
                            "Technitium %s %s returned %d "
                            "(attempt %d/%d) — retrying",
                            method, path,
                            exc.response.status_code,
                            attempt + 1, _max_attempts,
                        )
                        await asyncio.sleep(_backoff[attempt])
                        continue
                    raise TechnitiumError(
                        f"HTTP {exc.response.status_code}: {exc.response.text}",
                        status_code=exc.response.status_code,
                    ) from exc
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

        # Should not reach here, but satisfy type checker
        raise TechnitiumError(str(last_exc))

    async def list_scopes(self) -> list[dict[str, Any]]:
        """List all DHCP scopes."""
        result = await self._request("GET", "/api/dhcp/scopes/list")
        response: dict[str, Any] = result.get("response", {})
        scopes: list[dict[str, Any]] = response.get("scopes", [])
        return scopes

    async def get_scope(self, name: str) -> dict[str, Any]:
        """Get detailed scope information including reservations."""
        result = await self._request(
            "GET", "/api/dhcp/scopes/get", params={"name": name}
        )
        response: dict[str, Any] = result.get("response", {})
        return response

    async def set_scope(self, name: str, settings: dict[str, Any]) -> None:
        """Update scope settings."""
        data = {"name": name, **settings}
        await self._request("POST", "/api/dhcp/scopes/set", data=data)

    async def enable_scope(self, name: str) -> None:
        """Enable a DHCP scope."""
        await self._request("POST", "/api/dhcp/scopes/enable", data={"name": name})

    async def disable_scope(self, name: str) -> None:
        """Disable a DHCP scope."""
        await self._request("POST", "/api/dhcp/scopes/disable", data={"name": name})

    async def delete_scope(self, name: str) -> None:
        """Delete a DHCP scope."""
        await self._request("POST", "/api/dhcp/scopes/delete", data={"name": name})

    async def remove_lease(self, scope_name: str, *, address: str) -> None:
        """Remove/convert a dynamic lease from a scope."""
        await self._request(
            "POST",
            "/api/dhcp/leases/remove",
            data={"name": scope_name, "address": address},
        )

    async def add_reservation(
        self,
        scope_name: str,
        *,
        hardware_address: str,
        address: str,
        host_name: str = "",
        comments: str = "",
    ) -> None:
        """Add a DHCP reservation to a scope."""
        data: dict[str, Any] = {
            "name": scope_name,
            "hardwareAddress": hardware_address,
            "ipAddress": address,
        }
        if host_name:
            data["hostName"] = host_name
        if comments:
            data["comments"] = comments
        await self._request("POST", "/api/dhcp/scopes/addReservedLease", data=data)

    async def remove_reservation(
        self, scope_name: str, *, hardware_address: str
    ) -> None:
        """Remove a DHCP reservation from a scope."""
        await self._request(
            "POST",
            "/api/dhcp/scopes/removeReservedLease",
            data={"name": scope_name, "hardwareAddress": hardware_address},
        )

    async def get_leases(self, scope_name: str) -> list[dict[str, Any]]:
        """Get active leases for a scope."""
        result = await self._request(
            "GET", "/api/dhcp/leases/list", params={"name": scope_name}
        )
        response: dict[str, Any] = result.get("response", {})
        leases: list[dict[str, Any]] = response.get("leases", [])
        return leases


class TechnitiumPool:
    """Manages multiple TechnitiumClient instances for N-server failover.

    Tracks server roles (active, candidate, observer) and supports
    runtime promotion/demotion.
    """

    def __init__(
        self,
        token: str,
        *,
        ca_cert_file: str = "",
        servers_file: Path | None = None,
    ) -> None:
        self._token = token
        self._ca_cert_file = ca_cert_file
        self._clients: dict[str, TechnitiumClient] = {}
        self._roles: dict[str, str] = {}  # name -> role
        self._priorities: dict[str, int] = {}  # name -> priority
        self._servers_file = servers_file

    @classmethod
    def from_servers(
        cls, servers: list[DhcpServer], token: str, *, ca_cert_file: str = "",
        servers_file: Path | None = None,
    ) -> TechnitiumPool:
        """Create a pool from a list of DhcpServer configs.

        Args:
            servers: Server configurations.
            token: Technitium API token.
            ca_cert_file: Optional CA certificate bundle path.
            servers_file: Optional file for persisting role changes.

        Returns:
            A configured TechnitiumPool.
        """
        pool = cls(token=token, ca_cert_file=ca_cert_file, servers_file=servers_file)

        # Load persisted roles if available
        persisted_roles: dict[str, str] = {}
        if servers_file and servers_file.is_file():
            try:
                saved = json.loads(servers_file.read_text())
                persisted_roles = {s["name"]: s["role"] for s in saved}
                logger.info("Loaded persisted server roles from %s", servers_file)
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupt servers file, ignoring: %s", servers_file)

        for server in servers:
            client = TechnitiumClient(
                base_url=server.url,
                token=token,
                server_name=server.name,
                ca_cert_file=ca_cert_file,
            )
            pool._clients[server.name] = client
            pool._roles[server.name] = persisted_roles.get(server.name, server.role)
            pool._priorities[server.name] = server.priority
        return pool

    async def start_all(self) -> None:
        """Start all clients in the pool."""
        for client in self._clients.values():
            await client.start()

    async def stop_all(self) -> None:
        """Stop all clients in the pool."""
        for client in self._clients.values():
            await client.stop()

    def _persist_roles(self) -> None:
        """Write current server roles to disk atomically."""
        if not self._servers_file:
            return
        data = [
            {
                "name": name,
                "url": self._clients[name]._base_url,
                "role": self._roles[name],
                "priority": self._priorities[name],
            }
            for name in self._clients
        ]
        tmp = self._servers_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(self._servers_file)
        logger.debug("Persisted server roles to %s", self._servers_file)

    def get_active(self) -> TechnitiumClient:
        """Return the current active client.

        Raises:
            TechnitiumError: If no active server is configured.
        """
        for name, role in self._roles.items():
            if role == "active":
                return self._clients[name]
        raise TechnitiumError("No active server configured", status_code=0)

    def get_candidate(self) -> TechnitiumClient | None:
        """Return the highest-priority candidate client, or None."""
        candidates = [
            (name, self._priorities[name])
            for name, role in self._roles.items()
            if role == "candidate"
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[1])
        return self._clients[candidates[0][0]]

    def get_candidates(self) -> list[TechnitiumClient]:
        """Return all candidate clients sorted by priority."""
        candidates = [
            (name, self._priorities[name])
            for name, role in self._roles.items()
            if role == "candidate"
        ]
        candidates.sort(key=lambda x: x[1])
        return [self._clients[name] for name, _ in candidates]

    def get_all(self) -> list[TechnitiumClient]:
        """Return all clients."""
        return list(self._clients.values())

    def get_client(self, name: str) -> TechnitiumClient | None:
        """Return a client by server name."""
        return self._clients.get(name)

    def get_role(self, name: str) -> str | None:
        """Return the current role of a server."""
        return self._roles.get(name)

    def promote(self, server_name: str) -> None:
        """Promote a server to active, demoting the current active.

        Args:
            server_name: Name of the server to promote.

        Raises:
            TechnitiumError: If the server is not found or is an observer.
        """
        if server_name not in self._clients:
            raise TechnitiumError(
                f"Server not found: {server_name}", status_code=0
            )
        if self._roles[server_name] == "observer":
            raise TechnitiumError(
                f"Cannot promote observer: {server_name}", status_code=0
            )
        # Demote current active to candidate
        for name, role in self._roles.items():
            if role == "active":
                self._roles[name] = "candidate"
                logger.info("Demoted %s from active to candidate", name)
                break
        self._roles[server_name] = "active"
        logger.info("Promoted %s to active", server_name)
        self._persist_roles()

    def demote(self, server_name: str) -> None:
        """Demote a server to candidate.

        Args:
            server_name: Name of the server to demote.

        Raises:
            TechnitiumError: If the server is not found.
        """
        if server_name not in self._clients:
            raise TechnitiumError(
                f"Server not found: {server_name}", status_code=0
            )
        self._roles[server_name] = "candidate"
        logger.info("Demoted %s to candidate", server_name)
        self._persist_roles()

    def get_server_states(self) -> list[dict[str, Any]]:
        """Return current state of all servers.

        Returns:
            List of dicts with name, url, role, priority, and health info.
        """
        states: list[dict[str, Any]] = []
        for name, client in self._clients.items():
            states.append({
                "name": name,
                "url": client._base_url,
                "role": self._roles[name],
                "priority": self._priorities[name],
                "status": client.health.status.value,
                "message": client.health.message,
            })
        return states

    async def check_health_all(self) -> dict[str, EngineHealth]:
        """Check health of all servers in the pool.

        Returns:
            Dict of server name to EngineHealth.
        """
        results: dict[str, EngineHealth] = {}
        for name, client in self._clients.items():
            try:
                results[name] = await client.check_health()
            except Exception:
                results[name] = EngineHealth(
                    status=EngineStatus.DEGRADED,
                    message="Health check failed",
                )
        return results

    def update_servers(self, servers: list[DhcpServer]) -> list[str]:
        """Update the server pool with a new config.

        Adds new servers, removes deleted ones, updates roles/priorities.
        Does NOT change running clients — caller must start/stop as needed.

        Args:
            servers: New server configurations.

        Returns:
            List of change descriptions.
        """
        changes: list[str] = []
        new_names = {s.name for s in servers}
        old_names = set(self._clients.keys())

        # Remove deleted servers
        for name in old_names - new_names:
            del self._clients[name]
            del self._roles[name]
            del self._priorities[name]
            changes.append(f"removed server {name}")

        # Add/update servers
        for server in servers:
            if server.name not in old_names:
                client = TechnitiumClient(
                    base_url=server.url,
                    token=self._token,
                    server_name=server.name,
                    ca_cert_file=self._ca_cert_file,
                )
                self._clients[server.name] = client
                self._roles[server.name] = server.role
                self._priorities[server.name] = server.priority
                changes.append(f"added server {server.name} ({server.role})")
            else:
                if self._priorities[server.name] != server.priority:
                    self._priorities[server.name] = server.priority
                    changes.append(
                        f"updated priority for {server.name} to {server.priority}"
                    )
                # Role changes via config only update priority, not role
                # (role changes happen via API promote/demote)

        return changes
