from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tessera.engines.voter_registry import VoterRegistryEngine

if TYPE_CHECKING:
    from pathlib import Path


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
        self, registry: VoterRegistryEngine
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        validated = registry.validate_token(token.token)
        assert not validated.used

    @pytest.mark.asyncio
    async def test_plaintext_token_not_persisted_to_disk(
        self, registry: VoterRegistryEngine, tmp_path: Path
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        tokens_data = (tmp_path / "tokens.json").read_text()
        assert token.token not in tokens_data

    @pytest.mark.asyncio
    async def test_listed_tokens_show_truncated_prefix_only(
        self, registry: VoterRegistryEngine
    ) -> None:
        await registry.start()
        registry.generate_token()
        listed = registry.list_tokens()
        assert len(listed) == 1
        assert listed[0].token.endswith("...")
        assert len(listed[0].token) == 11

    @pytest.mark.asyncio
    async def test_consumed_token_cannot_be_reused(
        self, registry: VoterRegistryEngine
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        raw = token.token
        registry.consume_token(raw, "test-voter")
        with pytest.raises(Exception, match="already used"):
            registry.validate_token(raw)

    @pytest.mark.asyncio
    async def test_voter_registration_succeeds_with_valid_token(
        self, registry: VoterRegistryEngine
    ) -> None:
        await registry.start()
        token = registry.generate_token()
        record, psk = registry.register_voter("test-vm", token.token)
        assert record.status == "active"
        assert psk is not None
