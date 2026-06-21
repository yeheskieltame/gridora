"""In-memory ChainPort for tests and `--mode dry` (no node, no keys).

Mirrors the on-chain StrategyLedger + TradeJournal + StrategyMemory semantics
without a BSC node: commits are write-once, attestations require a prior commit,
and `recall` ranks records by a RISK-ADJUSTED score (PnL per unit of drawdown —
better systems, not raw PnL). The real BscChain adapter (adapters/chain/client.py)
implements the same ChainPort against deployed contracts via TWAK signing.
"""
from __future__ import annotations

import hashlib
from typing import Sequence

from ...domain.models import EpisodeOutcome, GridConfig, MemoryRecord, RegimeFingerprint


def config_hash(cfg: GridConfig) -> str:
    """Deterministic pre-commitment hash of a grid config (what StrategyLedger
    stores BEFORE trading, so the strategy can't be changed after the fact)."""
    raw = (f"{cfg.market}|{cfg.lower}|{cfg.upper}|{cfg.levels}|{cfg.spacing.value}|"
           f"{cfg.order_size}|{cfg.bias}|{cfg.preset.value}|{cfg.policy_version}")
    return "0x" + hashlib.sha256(raw.encode()).hexdigest()


def risk_score(outcome: EpisodeOutcome) -> float:
    """PnL-per-drawdown ranking key. Divide by a tiny floor so a zero-drawdown episode
    ranks ABOVE a tiny-drawdown one (monotonic across mdd=0), not at raw pnl_bps."""
    return outcome.pnl_bps / max(outcome.max_drawdown_bps, 1e-9)


class MemoryChain:
    """Implements gridora.domain.ports.ChainPort in memory."""

    def __init__(self, agent: str = "0xagent") -> None:
        self.agent = agent
        self.identity_tx: str = ""
        self._commits: dict[str, str] = {}          # instance_id -> configHash
        self._attestations: dict[str, EpisodeOutcome] = {}
        self._memory: dict[str, list[MemoryRecord]] = {}  # regime_label -> records
        self._tx = 0

    def _txhash(self) -> str:
        self._tx += 1
        return f"0xfaketx{self._tx:058x}"[:66]

    async def register_identity(self, agent_address: str) -> str:
        self.agent = agent_address
        self.identity_tx = self._txhash()
        return self.identity_tx

    async def commit_strategy(self, instance_id: str, config: GridConfig) -> str:
        if instance_id in self._commits:
            raise ValueError(f"already committed: {instance_id}")
        self._commits[instance_id] = config_hash(config)
        return self._txhash()

    async def attest(self, instance_id: str, outcome: EpisodeOutcome) -> str:
        if instance_id not in self._commits:
            raise ValueError(f"no commitment for {instance_id} — commit before attesting")
        self._attestations[instance_id] = outcome
        return self._txhash()

    async def write_memory(self, record: MemoryRecord) -> str:
        self._memory.setdefault(record.regime_label, []).append(record)
        return self._txhash()

    async def recall(self, regime: RegimeFingerprint, k: int = 5) -> Sequence[MemoryRecord]:
        records = list(self._memory.get(regime.label, []))
        records.sort(key=lambda r: risk_score(r.outcome), reverse=True)
        return records[:k]
