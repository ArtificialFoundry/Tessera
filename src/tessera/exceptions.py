"""Custom exception hierarchy for Tessera.

All application exceptions inherit from ``AppError``.
"""

from __future__ import annotations


class AppError(Exception):
    """Base for all Tessera application errors."""


class RegistryError(AppError):
    """An operation on a registry failed."""


class DuplicateEntryError(RegistryError):
    """Attempted to register an entry that already exists."""

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind
        self.name = name
        super().__init__(f"{kind} already registered: {name}")


class NotFoundError(RegistryError):
    """Requested registry entry does not exist."""

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind
        self.name = name
        super().__init__(f"{kind} not found: {name}")


class EngineStartupError(AppError):
    """An engine failed during startup."""

    def __init__(self, engine_name: str, cause: Exception) -> None:
        self.engine_name = engine_name
        self.__cause__ = cause
        super().__init__(f"Engine '{engine_name}' failed to start: {cause}")


class DependencyError(AppError):
    """An engine dependency is missing or unsatisfied."""

    def __init__(self, engine_name: str, dependency: str) -> None:
        self.engine_name = engine_name
        self.dependency = dependency
        super().__init__(
            f"Engine '{engine_name}' depends on unregistered engine '{dependency}'"
        )


class AuthenticationError(AppError):
    """HMAC or voter authentication failed."""

    def __init__(self, message: str = "Authentication failed") -> None:
        super().__init__(message)


class TechnitiumError(AppError):
    """Communication with Technitium API failed."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        self.status_code = status_code
        super().__init__(message)
