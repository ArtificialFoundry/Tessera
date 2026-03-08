"""Sprint 5 tests — audit trail, backup encryption, webhooks."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from tessera.engines.audit import AuditEngine
from tessera.engines.backup import BackupEngine
from tessera.engines.webhooks import WebhookEngine

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import AsyncClient


class TestAuditEngine:
    """Audit trail engine tests."""

    @pytest.fixture
    def engine(self, tmp_path: Path) -> AuditEngine:
        return AuditEngine(audit_dir=tmp_path / "audit")

    @pytest.mark.asyncio
    async def test_record_and_query(self, engine: AuditEngine) -> None:
        await engine.start()
        engine.record("backup.create", "10.0.0.1", "bk-001", "test")
        engine.record("voter.approve", "10.0.0.2", "voter-a")

        events, total = engine.query()
        assert total == 2
        assert events[0].action == "voter.approve"  # newest first
        assert events[1].action == "backup.create"

    @pytest.mark.asyncio
    async def test_filter_by_action(self, engine: AuditEngine) -> None:
        await engine.start()
        engine.record("backup.create", "10.0.0.1")
        engine.record("backup.delete", "10.0.0.1")
        engine.record("voter.approve", "10.0.0.2")

        _events, total = engine.query(action_filter="backup")
        assert total == 2

    @pytest.mark.asyncio
    async def test_filter_by_actor(self, engine: AuditEngine) -> None:
        await engine.start()
        engine.record("a", "10.0.0.1")
        engine.record("b", "10.0.0.2")

        _events, total = engine.query(actor_filter="10.0.0.1")
        assert total == 1

    @pytest.mark.asyncio
    async def test_persistence_across_restarts(self, tmp_path: Path) -> None:
        audit_dir = tmp_path / "audit"
        engine1 = AuditEngine(audit_dir=audit_dir)
        await engine1.start()
        engine1.record("test.action", "actor1", "target1")
        await engine1.stop()

        engine2 = AuditEngine(audit_dir=audit_dir)
        await engine2.start()
        events, total = engine2.query()
        assert total == 1
        assert events[0].action == "test.action"

    @pytest.mark.asyncio
    async def test_pagination(self, engine: AuditEngine) -> None:
        await engine.start()
        for i in range(10):
            engine.record(f"action.{i}", "actor")

        events, total = engine.query(offset=3, limit=2)
        assert total == 10
        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_metrics(self, engine: AuditEngine) -> None:
        await engine.start()
        engine.record("a", "b")
        m = engine.get_metrics()
        assert m["total_events"] == 1
        assert m["buffered_events"] == 1


class TestBackupEncryption:
    """Backup encryption at rest."""

    @pytest.fixture
    def mock_client(self) -> AsyncMock:
        client = AsyncMock()
        client.list_scopes = AsyncMock(
            return_value=[{"name": "test-scope", "enabled": True}]
        )
        client.get_scope = AsyncMock(
            return_value={
                "name": "test-scope",
                "startingAddress": "10.0.0.1",
                "endingAddress": "10.0.0.254",
                "subnetMask": "255.255.255.0",
            }
        )
        client.get_reservations = AsyncMock(return_value=[])
        client._base_url = "http://test:5380"
        return client

    @pytest.mark.asyncio
    async def test_encrypted_backup_roundtrip(
        self, tmp_path: Path, mock_client: AsyncMock
    ) -> None:
        engine = BackupEngine(
            backup_dir=tmp_path / "backups",
            encryption_key="my-secret-key",
        )
        engine.set_active_client(mock_client)
        await engine.start()

        manifest = await engine.create_backup("encrypted test")
        filepath = tmp_path / "backups" / f"{manifest.backup_id}.json"

        # File should start with encryption header
        raw = filepath.read_bytes()
        assert raw.startswith(b"TESSERA_ENC_V1\n")

        # Should be able to read it back
        backup = await engine.get_backup(manifest.backup_id)
        assert backup.manifest.description == "encrypted test"

    @pytest.mark.asyncio
    async def test_encrypted_backup_unreadable_without_key(
        self, tmp_path: Path, mock_client: AsyncMock
    ) -> None:
        from tessera.exceptions import AppError

        engine_enc = BackupEngine(
            backup_dir=tmp_path / "backups",
            encryption_key="my-secret-key",
        )
        engine_enc.set_active_client(mock_client)
        await engine_enc.start()

        manifest = await engine_enc.create_backup("secret")

        # Try to read without key
        engine_plain = BackupEngine(backup_dir=tmp_path / "backups")
        engine_plain.set_active_client(mock_client)
        await engine_plain.start()

        with pytest.raises(AppError, match="encrypted"):
            await engine_plain.get_backup(manifest.backup_id)

    @pytest.mark.asyncio
    async def test_wrong_key_fails(
        self, tmp_path: Path, mock_client: AsyncMock
    ) -> None:
        from tessera.exceptions import AppError

        engine1 = BackupEngine(
            backup_dir=tmp_path / "backups",
            encryption_key="key-one",
        )
        engine1.set_active_client(mock_client)
        await engine1.start()

        manifest = await engine1.create_backup("secret")

        engine2 = BackupEngine(
            backup_dir=tmp_path / "backups",
            encryption_key="key-two",
        )
        engine2.set_active_client(mock_client)
        await engine2.start()

        with pytest.raises(AppError, match="decrypt"):
            await engine2.get_backup(manifest.backup_id)

    @pytest.mark.asyncio
    async def test_plain_backup_still_works(
        self, tmp_path: Path, mock_client: AsyncMock
    ) -> None:
        engine = BackupEngine(backup_dir=tmp_path / "backups")
        engine.set_active_client(mock_client)
        await engine.start()

        manifest = await engine.create_backup("plain")
        filepath = tmp_path / "backups" / f"{manifest.backup_id}.json"

        # Should NOT start with encryption header
        raw = filepath.read_bytes()
        assert not raw.startswith(b"TESSERA_ENC_V1\n")

        # Readable as plain JSON
        filepath.chmod(0o644)
        data = json.loads(filepath.read_text())
        assert "manifest" in data

    @pytest.mark.asyncio
    async def test_encrypted_metrics_flag(
        self, tmp_path: Path, mock_client: AsyncMock
    ) -> None:
        enc = BackupEngine(
            backup_dir=tmp_path / "b1",
            encryption_key="key",
        )
        enc.set_active_client(mock_client)
        await enc.start()
        assert enc.get_metrics()["encrypted"] is True

        plain = BackupEngine(backup_dir=tmp_path / "b2")
        plain.set_active_client(mock_client)
        await plain.start()
        assert plain.get_metrics()["encrypted"] is False


class TestWebhookEngine:
    """Webhook engine tests."""

    @pytest.mark.asyncio
    async def test_no_urls_is_silent(self) -> None:
        engine = WebhookEngine()
        await engine.start()
        engine.notify("test.event", detail="hello")
        assert engine.get_metrics()["queue_size"] == 0

    @pytest.mark.asyncio
    async def test_notify_queues_event(self) -> None:
        engine = WebhookEngine(urls=["http://localhost:9999/hook"])
        await engine.start()
        engine.notify("test.event", detail="hello")
        assert engine.get_metrics()["queue_size"] == 1
        await engine.stop()

    @pytest.mark.asyncio
    async def test_metrics(self) -> None:
        engine = WebhookEngine(urls=["http://a", "http://b"])
        m = engine.get_metrics()
        assert m["configured_urls"] == 2
        assert m["total_sent"] == 0


class TestAuditAPI:
    """Audit API endpoint tests."""

    @pytest.mark.asyncio
    async def test_list_audit_events(
        self, client: AsyncClient, audit_engine: AuditEngine
    ) -> None:
        await audit_engine.start()
        audit_engine.record("test.action", "10.0.0.1", "target")

        resp = await client.get(
            "/api/v1/audit",
            headers={"Authorization": "Bearer test-key"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["events"]) == 1
        assert data["events"][0]["action"] == "test.action"
        assert data["pagination"]["total"] == 1
