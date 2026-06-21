"""BscChain (ChainPort) — verifiable proofs via TWAK-native ERC-8004 (self-custodial).

register_identity -> `twak erc8004 register`; commit_strategy / attest ->
`erc8004 set-metadata <id> --key gridora.{commit,attest}.<iid> --value <hash>`.
Recall memory is off-chain, mirrored by the on-chain attestations.
"""
from __future__ import annotations

import hashlib
from typing import Sequence

from ...domain.models import EpisodeOutcome, GridConfig, MemoryRecord, RegimeFingerprint
from ..chain.memory_chain import config_hash, risk_score
from ..exchanges.bsc_twak.twak_client import TwakClient, TwakError


def _short(instance_id: str) -> str:
    """ERC-8004 metadata key segment: a collision-free digest of the FULL instance_id.
    The raw 24-char tail collided when two instances shared a suffix, letting one
    overwrite the other's commit/attest key."""
    return hashlib.sha256(instance_id.encode()).hexdigest()[:16]


def outcome_hash(o: EpisodeOutcome) -> str:
    raw = f"{o.realized_pnl_quote}|{o.pnl_bps}|{o.trades}|{o.max_drawdown_bps}|{o.fills_root}"
    return "0x" + hashlib.sha256(raw.encode()).hexdigest()


class BscChain:
    def __init__(self, twak: TwakClient, chain_key: str = "bsc",
                 agent_uri: str = "ipfs://gridora-agent.json") -> None:
        self.twak = twak
        self.chain_key = chain_key
        self.agent_uri = agent_uri
        self.agent_id: str | None = None
        self._memory: dict[str, list[MemoryRecord]] = {}  # off-chain recall mirror

    async def register_identity(self, agent_address: str) -> str:
        # Identity == TWAK's own wallet (it signs erc8004 register with its key). The
        # `agent_address` arg satisfies ChainPort but is informational only here; setting
        # GRIDORA_AGENT_ADDRESS does not rebind this path to a different wallet.
        res = await self.twak.erc8004_register(self.agent_uri, chain=self.chain_key)
        self.agent_id = str(res.get("agentId") or res.get("id") or "")
        return str(res.get("hash", ""))

    async def _ensure_agent(self) -> str:
        if not self.agent_id:
            await self.register_identity("")
        if not self.agent_id:
            raise RuntimeError("no ERC-8004 agentId — register_identity must succeed before commit/attest")
        return self.agent_id

    async def _metadata_value(self, agent_id: str, key: str) -> str:
        """Best-effort read of an existing ERC-8004 metadata value. TWAK's get-metadata
        behavior for an UNSET key is unverified offline — treat an error/empty as 'absent'.
        VERIFY against live TWAK before trusting the write-once guards below."""
        try:
            res = await self.twak.erc8004_get_metadata(agent_id, key, chain=self.chain_key)
        except TwakError:
            return ""
        return str(res.get("value") or res.get("data") or "")

    async def commit_strategy(self, instance_id: str, config: GridConfig) -> str:
        agent_id = await self._ensure_agent()
        key = f"gridora.commit.{_short(instance_id)}"
        if await self._metadata_value(agent_id, key):
            raise ValueError(f"already committed on-chain: {instance_id}")  # write-once, mirror MemoryChain
        res = await self.twak.erc8004_set_metadata(
            agent_id, key=key, value=config_hash(config), chain=self.chain_key)
        return str(res.get("hash", ""))

    async def attest(self, instance_id: str, outcome: EpisodeOutcome) -> str:
        agent_id = await self._ensure_agent()
        if not await self._metadata_value(agent_id, f"gridora.commit.{_short(instance_id)}"):
            raise ValueError(f"no on-chain commitment for {instance_id} — commit before attesting")
        key = f"gridora.attest.{_short(instance_id)}"
        if await self._metadata_value(agent_id, key):
            raise ValueError(f"already attested on-chain: {instance_id}")  # outcome is write-once
        res = await self.twak.erc8004_set_metadata(
            agent_id, key=key, value=outcome_hash(outcome), chain=self.chain_key)
        return str(res.get("hash", ""))

    async def write_memory(self, record: MemoryRecord) -> str:
        self._memory.setdefault(record.regime_label, []).append(record)
        return ""  # durable memory is off-chain; the on-chain proof is the attestation

    async def recall(self, regime: RegimeFingerprint, k: int = 5) -> Sequence[MemoryRecord]:
        records = list(self._memory.get(regime.label, []))
        records.sort(key=lambda r: risk_score(r.outcome), reverse=True)
        return records[:k]
