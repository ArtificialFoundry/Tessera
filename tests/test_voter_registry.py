"""VoterRegistryEngine: token lifecycle, PSK rotation, voter management."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import TYPE_CHECKING

import pytest

from tessera.engines.failover import FailoverEngine
from tessera.engines.voter_registry import VoterRegistryEngine
from tessera.exceptions import AuthenticationError

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import AsyncClient


class TestRegistrationTokenSecurity:
    """Registration token hashing and lifecycle."""

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
    async def test_generated_token_passes_validation(
        self,
        registry: VoterRegistryEngine,
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        validated = registry.validate_token(token.token)
        assert not validated.used

    @pytest.mark.asyncio
    async def test_plaintext_token_not_persisted_to_disk(
        self,
        registry: VoterRegistryEngine,
        tmp_path: Path,
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        tokens_data = (tmp_path / "tokens.json").read_text()
        assert token.token not in tokens_data

    @pytest.mark.asyncio
    async def test_listed_tokens_show_truncated_prefix_only(
        self,
        registry: VoterRegistryEngine,
    ) -> None:
        await registry.start()
        registry.generate_token()
        listed = registry.list_tokens()
        assert len(listed) == 1
        assert listed[0].token.endswith("...")
        assert len(listed[0].token) == 11

    @pytest.mark.asyncio
    async def test_consumed_token_cannot_be_reused(
        self,
        registry: VoterRegistryEngine,
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        raw = token.token
        registry.consume_token(raw, "test-voter")
        with pytest.raises(Exception, match="already used"):
            registry.validate_token(raw)

    @pytest.mark.asyncio
    async def test_voter_registration_succeeds_with_valid_token(
        self,
        registry: VoterRegistryEngine,
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        record, psk = registry.register_voter("test-vm", token.token)
        assert record.status == "active"
        assert psk is not None


class TestTokenGeneration:
    """Token creation, validation, expiry, and IP binding."""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> VoterRegistryEngine:
        return VoterRegistryEngine(
            voter_keys_file=tmp_path / "voter-keys.json",
            voter_registry_file=tmp_path / "voter-registry.json",
            reg_tokens_file=tmp_path / "reg-tokens.json",
            static_registration_token="static-token",
            auto_approve=False,
            token_ttl=3600,
            psk_grace_period=2,
        )

    def test_generated_token_is_64_hex_chars(
        self, registry: VoterRegistryEngine
    ) -> None:
        token = registry.generate_token()
        assert len(token.token) == 64
        assert token.is_valid()

    def test_zero_ttl_token_expires_immediately(
        self, registry: VoterRegistryEngine
    ) -> None:
        token = registry.generate_token(ttl=0)
        time.sleep(0.01)
        assert token.is_expired()

    def test_valid_token_passes_validation(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        validated = registry.validate_token(token.token)
        assert validated.is_valid()

    def test_expired_token_rejected(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token(ttl=0)
        time.sleep(0.01)
        with pytest.raises(AuthenticationError, match="expired"):
            registry.validate_token(token.token)

    def test_unknown_token_rejected(self, registry: VoterRegistryEngine) -> None:
        with pytest.raises(AuthenticationError, match="Invalid"):
            registry.validate_token("nonexistent-token")

    def test_reused_token_rejected(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        with pytest.raises(AuthenticationError, match="already used"):
            registry.register_voter("host-2", token.token)

    def test_static_token_reusable_across_voters(
        self, registry: VoterRegistryEngine
    ) -> None:
        registry.register_voter("host-1", "static-token")
        record, _ = registry.register_voter("host-2", "static-token")
        assert record.status == "pending"

    def test_ip_bound_token_rejects_wrong_ip(
        self, registry: VoterRegistryEngine
    ) -> None:
        token = registry.generate_token(bind_ip="10.0.0.1")
        with pytest.raises(AuthenticationError, match="bound to"):
            registry.validate_token(token.token, source_ip="10.0.0.2")
        validated = registry.validate_token(token.token, source_ip="10.0.0.1")
        assert validated.is_valid()

    def test_cleanup_removes_only_expired_tokens(
        self, registry: VoterRegistryEngine
    ) -> None:
        registry.generate_token(ttl=0)
        registry.generate_token(ttl=3600)
        time.sleep(0.01)
        removed = registry.cleanup_expired_tokens()
        assert removed == 1
        assert len(registry.list_tokens()) == 1


class TestVoterLifecycle:
    """Voter registration, approval, revocation, and deletion."""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> VoterRegistryEngine:
        return VoterRegistryEngine(
            voter_keys_file=tmp_path / "voter-keys.json",
            voter_registry_file=tmp_path / "voter-registry.json",
            reg_tokens_file=tmp_path / "reg-tokens.json",
            static_registration_token="static-token",
            auto_approve=False,
            token_ttl=3600,
            psk_grace_period=2,
        )

    @pytest.fixture
    def auto_registry(self, tmp_path: Path) -> VoterRegistryEngine:
        return VoterRegistryEngine(
            voter_keys_file=tmp_path / "voter-keys.json",
            voter_registry_file=tmp_path / "voter-registry.json",
            reg_tokens_file=tmp_path / "reg-tokens.json",
            static_registration_token="",
            auto_approve=True,
            token_ttl=3600,
            psk_grace_period=2,
        )

    def test_manual_registration_is_pending(
        self, registry: VoterRegistryEngine
    ) -> None:
        token = registry.generate_token()
        record, psk = registry.register_voter("host-1", token.token)
        assert record.status == "pending"
        assert psk is None

    def test_auto_approve_grants_active_with_psk(
        self, auto_registry: VoterRegistryEngine
    ) -> None:
        token = auto_registry.generate_token()
        record, psk = auto_registry.register_voter("host-1", token.token)
        assert record.status == "active"
        assert psk is not None
        assert len(psk) == 64

    def test_approve_pending_voter_returns_psk(
        self, registry: VoterRegistryEngine
    ) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        record, psk = registry.approve_voter("host-1")
        assert record.status == "active"
        assert psk is not None

    def test_revoke_active_voter(self, registry: VoterRegistryEngine) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        registry.approve_voter("host-1")
        record = registry.revoke_voter("host-1")
        assert record.status == "revoked"

    def test_list_pending_voters(self, registry: VoterRegistryEngine) -> None:
        t1 = registry.generate_token()
        t2 = registry.generate_token()
        registry.register_voter("host-1", t1.token)
        registry.register_voter("host-2", t2.token)
        assert len(registry.list_pending()) == 2


class TestPSKRotation:
    """PSK rotation with grace period for seamless key rollover."""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> VoterRegistryEngine:
        return VoterRegistryEngine(
            voter_keys_file=tmp_path / "voter-keys.json",
            voter_registry_file=tmp_path / "voter-registry.json",
            reg_tokens_file=tmp_path / "reg-tokens.json",
            static_registration_token="static-token",
            auto_approve=False,
            token_ttl=3600,
            psk_grace_period=2,
        )

    def test_rotated_key_differs_from_original(
        self, registry: VoterRegistryEngine
    ) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        _, original_psk = registry.approve_voter("host-1")
        new_psk = registry.rotate_key("host-1")
        assert new_psk != original_psk
        assert len(new_psk) == 64

    def test_both_keys_valid_during_grace_period(
        self, registry: VoterRegistryEngine
    ) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        _, original_psk = registry.approve_voter("host-1")
        new_psk = registry.rotate_key("host-1")

        psks = registry.get_valid_psks("host-1")
        assert new_psk in psks
        assert original_psk in psks
        assert registry.is_in_grace_period("host-1")

    def test_old_key_invalid_after_grace_expires(
        self, registry: VoterRegistryEngine
    ) -> None:
        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        _, original_psk = registry.approve_voter("host-1")
        new_psk = registry.rotate_key("host-1")

        registry._grace_keys["host-1"].expires_at = time.time() - 1

        psks = registry.get_valid_psks("host-1")
        assert new_psk in psks
        assert original_psk not in psks
        assert not registry.is_in_grace_period("host-1")

    def test_rotate_revoked_voter_raises_error(
        self, registry: VoterRegistryEngine
    ) -> None:
        from tessera.engines.voter_registry import RegistrationError

        token = registry.generate_token()
        registry.register_voter("host-1", token.token)
        registry.approve_voter("host-1")
        registry.revoke_voter("host-1")
        with pytest.raises(RegistrationError, match="revoked"):
            registry.rotate_key("host-1")


class TestVoterPersistence:
    """Voter registry persists state across restarts."""

    @pytest.mark.asyncio
    async def test_voters_survive_restart(self, tmp_path: Path) -> None:
        r1 = VoterRegistryEngine(
            voter_keys_file=tmp_path / "keys.json",
            voter_registry_file=tmp_path / "registry.json",
            reg_tokens_file=tmp_path / "tokens.json",
            auto_approve=True,
            token_ttl=3600,
            psk_grace_period=60,
        )
        await r1.start()
        token = r1.generate_token()
        r1.register_voter("host-1", token.token)
        await r1.stop()

        r2 = VoterRegistryEngine(
            voter_keys_file=tmp_path / "keys.json",
            voter_registry_file=tmp_path / "registry.json",
            reg_tokens_file=tmp_path / "tokens.json",
            auto_approve=True,
            token_ttl=3600,
            psk_grace_period=60,
        )
        await r2.start()
        voters = r2.list_voters()
        assert len(voters) == 1
        assert voters[0].name == "host-1"


class TestFailoverGracePeriodHMAC:
    """HMAC validation accepts old PSK during grace period."""

    @pytest.fixture
    def setup(self, tmp_path: Path) -> tuple[FailoverEngine, VoterRegistryEngine]:
        registry = VoterRegistryEngine(
            voter_keys_file=tmp_path / "keys.json",
            voter_registry_file=tmp_path / "reg.json",
            reg_tokens_file=tmp_path / "tokens.json",
            auto_approve=True,
            token_ttl=3600,
            psk_grace_period=60,
        )
        token = registry.generate_token()
        _, psk = registry.register_voter("voter-1", token.token)
        assert psk is not None

        engine = FailoverEngine(
            quorum=1,
            failover_rounds=1,
            failback_rounds=1,
            voter_keys={"voter-1": psk},
            vote_cooldown=0,
        )
        engine.set_voter_registry(registry)
        return engine, registry

    def test_old_key_accepted_during_grace(
        self,
        setup: tuple[FailoverEngine, VoterRegistryEngine],
    ) -> None:
        engine, registry = setup
        old_psk = registry._load_voter_keys()["voter-1"]
        registry.rotate_key("voter-1")
        engine.update_voter_keys(registry._load_voter_keys())

        ts = int(time.time())
        sig = hmac.new(
            old_psk.encode(), f"voter-1|up|{ts}".encode(), hashlib.sha256
        ).hexdigest()
        vote = engine.submit_vote("voter-1", "up", ts, sig)
        assert vote.voter == "voter-1"

    def test_new_key_accepted_after_rotation(
        self,
        setup: tuple[FailoverEngine, VoterRegistryEngine],
    ) -> None:
        engine, registry = setup
        new_psk = registry.rotate_key("voter-1")
        engine.update_voter_keys(registry._load_voter_keys())

        ts = int(time.time())
        sig = hmac.new(
            new_psk.encode(), f"voter-1|up|{ts}".encode(), hashlib.sha256
        ).hexdigest()
        vote = engine.submit_vote("voter-1", "up", ts, sig)
        assert vote.voter == "voter-1"

    def test_old_key_rejected_after_grace_expires(
        self,
        setup: tuple[FailoverEngine, VoterRegistryEngine],
    ) -> None:
        engine, registry = setup
        old_psk = registry._load_voter_keys()["voter-1"]
        registry.rotate_key("voter-1")
        engine.update_voter_keys(registry._load_voter_keys())
        registry._grace_keys["voter-1"].expires_at = time.time() - 1

        ts = int(time.time())
        sig = hmac.new(
            old_psk.encode(), f"voter-1|up|{ts}".encode(), hashlib.sha256
        ).hexdigest()
        with pytest.raises(AuthenticationError, match="Invalid signature"):
            engine.submit_vote("voter-1", "up", ts, sig)


class TestVotersAPI:
    """Tests for /api/v1/voters endpoints."""

    @pytest.mark.asyncio
    async def test_generate_token_returns_64_char_hex(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={})
        assert resp.status_code == 200
        assert len(resp.json()["token"]) == 64

    @pytest.mark.asyncio
    async def test_generate_token_with_ip_binding(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={"bind_ip": "10.0.0.1"})
        assert resp.status_code == 200
        assert resp.json()["bind_ip"] == "10.0.0.1"

    @pytest.mark.asyncio
    async def test_register_voter_pending_status(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={})
        token = resp.json()["token"]
        resp = await client.post(
            "/api/v1/voters/register", json={"name": "test-host", "token": token}
        )
        assert resp.status_code == 200
        assert resp.json()["voter_name"] == "test-host"
        assert resp.json()["status"] == "pending"
        assert resp.json()["psk"] is None

    @pytest.mark.asyncio
    async def test_register_with_static_token(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/voters/register",
            json={"name": "static-host", "token": "static-test-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    @pytest.mark.asyncio
    async def test_register_invalid_token_returns_401(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post(
            "/api/v1/voters/register",
            json={"name": "bad-host", "token": "invalid-token"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_approve_voter_returns_psk(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={})
        token = resp.json()["token"]
        await client.post(
            "/api/v1/voters/register", json={"name": "approve-host", "token": token}
        )
        resp = await client.post("/api/v1/voters/approve-host/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"
        assert len(resp.json()["psk"]) == 64

    @pytest.mark.asyncio
    async def test_revoke_voter_via_api(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={})
        token = resp.json()["token"]
        await client.post(
            "/api/v1/voters/register", json={"name": "revoke-host", "token": token}
        )
        await client.post("/api/v1/voters/revoke-host/approve")
        resp = await client.post("/api/v1/voters/revoke-host/revoke")
        assert resp.status_code == 200
        assert resp.json()["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_delete_voter_via_api(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={})
        token = resp.json()["token"]
        await client.post(
            "/api/v1/voters/register", json={"name": "del-host", "token": token}
        )
        await client.post("/api/v1/voters/del-host/approve")
        resp = await client.delete("/api/v1/voters/del-host")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_rotate_key_via_api(self, client: AsyncClient) -> None:
        resp = await client.post("/api/v1/voters/tokens", json={})
        token = resp.json()["token"]
        await client.post(
            "/api/v1/voters/register", json={"name": "rotate-host", "token": token}
        )
        await client.post("/api/v1/voters/rotate-host/approve")
        resp = await client.post("/api/v1/voters/rotate-host/rotate-key")
        assert resp.status_code == 200
        assert "new_psk" in resp.json()
        assert resp.json()["grace_period"] > 0

    @pytest.mark.asyncio
    async def test_list_voters_via_api(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/voters")
        assert resp.status_code == 200
        assert "voters" in resp.json()

    @pytest.mark.asyncio
    async def test_list_pending_via_api(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/voters/pending")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_tokens_via_api(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/voters/tokens")
        assert resp.status_code == 200
        assert "tokens" in resp.json()
