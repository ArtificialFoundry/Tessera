"""Tests for P0 critical security and data integrity hardening."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from tessera.engines.backup import BackupEngine
from tessera.engines.voter_registry import VoterRegistryEngine
from tessera.settings_store import SettingsStore

if TYPE_CHECKING:
    from pathlib import Path


class TestSettingsStoreLocking:
    """Issue #1: Concurrent writes must not corrupt data."""

    def test_concurrent_writes(self, tmp_path: Path) -> None:
        """Multiple threads writing should not corrupt data."""
        store = SettingsStore(tmp_path / "settings.json")
        errors: list[Exception] = []

        def writer(section: str, n: int) -> None:
            try:
                for i in range(50):
                    store.put(section, {"counter": i, "thread": n})
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=writer, args=(f"s{i}", i))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        data = json.loads((tmp_path / "settings.json").read_text())
        assert isinstance(data, dict)
        for i in range(8):
            assert f"s{i}" in data

    def test_lock_is_per_instance(self, tmp_path: Path) -> None:
        """Each SettingsStore has its own lock."""
        s1 = SettingsStore(tmp_path / "a.json")
        s2 = SettingsStore(tmp_path / "b.json")
        assert s1._lock is not s2._lock


class TestAtomicRestoreRollback:
    """Issue #2: restore_backup rollback on failure."""

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        mock = AsyncMock()
        mock._base_url = "https://test:53443"
        mock.list_scopes = AsyncMock(
            return_value=[{"name": "LAN", "enabled": True}]
        )
        mock.get_scope = AsyncMock(
            return_value={"reservedLeases": []}
        )
        mock.set_scope = AsyncMock()
        mock.add_reservation = AsyncMock()
        mock.remove_reservation = AsyncMock()
        return mock

    @pytest.mark.asyncio
    async def test_restore_creates_pre_restore_backup(
        self, tmp_path: Path, mock_client: AsyncMock
    ) -> None:
        engine = BackupEngine(
            backup_dir=tmp_path / "backups", max_backups=50
        )
        engine.set_active_client(mock_client)
        await engine.start()

        manifest = await engine.create_backup(description="test")
        result = await engine.restore_backup(manifest.backup_id)
        assert "pre_restore_backup_id" in result
        assert result["pre_restore_backup_id"] is not None

    @pytest.mark.asyncio
    async def test_restore_creates_pre_restore_id(
        self, tmp_path: Path, mock_client: AsyncMock
    ) -> None:
        """Pre-restore backup ID is always present in result."""
        engine = BackupEngine(
            backup_dir=tmp_path / "backups", max_backups=50
        )
        engine.set_active_client(mock_client)
        await engine.start()

        manifest = await engine.create_backup(description="test")
        result = await engine.restore_backup(manifest.backup_id)
        pre_id = result["pre_restore_backup_id"]
        assert pre_id is not None
        # Pre-restore backup should be listable
        backups = await engine.list_backups()
        ids = [b.backup_id for b in backups]
        assert pre_id in ids


class TestHMACTimingSafe:
    """Issue #4: HMAC uses compare_digest."""

    def test_verify_vote_uses_compare_digest(self) -> None:
        """verify_vote_signature uses hmac.compare_digest."""
        import inspect

        from tessera.engines.failover import verify_vote_signature

        source = inspect.getsource(verify_vote_signature)
        assert "hmac.compare_digest" in source


class TestRequestSizeLimit:
    """Issue #5: Request body size limit middleware."""

    @pytest.mark.asyncio
    async def test_small_body_accepted(self) -> None:
        from tessera.app import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/ping")
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_oversized_body_rejected(self) -> None:
        from tessera.app import create_app

        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            big_body = "x" * (1_048_576 + 1)
            resp = await client.post(
                "/api/v1/vote",
                content=big_body,
                headers={"content-type": "application/json"},
            )
            assert resp.status_code == 413


class TestHashedTokenValidation:
    """Issue #6: Registration tokens stored as hashes."""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> VoterRegistryEngine:
        return VoterRegistryEngine(
            voter_keys_file=tmp_path / "keys.json",
            voter_registry_file=tmp_path / "registry.json",
            reg_tokens_file=tmp_path / "tokens.json",
            auto_approve=True,
            token_ttl=3600,
            psk_grace_period=60,
        )

    @pytest.mark.asyncio
    async def test_generated_token_validates(
        self, registry: VoterRegistryEngine
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        validated = registry.validate_token(token.token)
        assert not validated.used

    @pytest.mark.asyncio
    async def test_raw_token_not_stored(
        self, registry: VoterRegistryEngine, tmp_path: Path
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        tokens_data = (tmp_path / "tokens.json").read_text()
        assert token.token not in tokens_data

    @pytest.mark.asyncio
    async def test_list_tokens_truncated(
        self, registry: VoterRegistryEngine
    ) -> None:
        await registry.start()
        registry.generate_token()
        listed = registry.list_tokens()
        assert len(listed) == 1
        assert listed[0].token.endswith("...")
        assert len(listed[0].token) == 11

    @pytest.mark.asyncio
    async def test_token_consume_and_reuse(
        self, registry: VoterRegistryEngine
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        raw = token.token
        registry.consume_token(raw, "test-voter")
        with pytest.raises(Exception, match="already used"):
            registry.validate_token(raw)

    @pytest.mark.asyncio
    async def test_register_with_hashed_token(
        self, registry: VoterRegistryEngine
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        record, psk = registry.register_voter("test-vm", token.token)
        assert record.status == "active"
        assert psk is not None
