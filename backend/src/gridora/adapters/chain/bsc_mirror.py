"""BscMirror (ChainPort) — writes the agent's proofs to the OWN custom contracts on BSC
mainnet (IdentityRegistry + StrategyLedger + TradeJournal), so the public verifier shows
live data.

TWAK can't call arbitrary contracts (only its fixed verbs), so this OPTIONAL mirror signs
with the agent key directly, by shelling out to Foundry `cast` (already installed) — same
shell-out pattern as TwakClient, no new Python dep. The PRIMARY identity stays the
TWAK-native ERC-8004 registration; this is the self-hosted verifier feed.

  register_identity -> IdentityRegistry.register(uri, wallet)   (idempotent: reuses agentOf)
  commit_strategy   -> StrategyLedger.commit(instanceId, configHash)   BEFORE trading
  attest            -> StrategyLedger.attest(instanceId, outcomeHash)
                       + TradeJournal.record(agentId, tradeHash, pnlBps, closedAt)

Hashes are SHA-256 (32 bytes -> bytes32); the chain stores them opaquely and the verifier
just displays them, so they need only be deterministic + non-zero. configHash matches
MemoryChain.config_hash so the commitment equals the offline/TWAK path.

CLI:  set -a; . backend/.env; set +a
      python -m gridora.adapters.chain.bsc_mirror register   # mint the identity (one-off)
      python -m gridora.adapters.chain.bsc_mirror status     # read agentId / counts
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Sequence

from ...domain.models import EpisodeOutcome, GridConfig, MemoryRecord, RegimeFingerprint
from .memory_chain import config_hash, risk_score

ZERO_ADDR = "0x0000000000000000000000000000000000000000"
MAINNET_RPC = "https://bsc-rpc.publicnode.com"


def _b32(s: str) -> str:
    """Deterministic non-zero bytes32 from a string (sha256)."""
    return "0x" + hashlib.sha256(s.encode()).hexdigest()


def outcome_hash(o: EpisodeOutcome) -> str:
    """Mirror of client.outcome_hash — the attested outcome digest (bytes32)."""
    raw = f"{o.realized_pnl_quote}|{o.pnl_bps}|{o.trades}|{o.max_drawdown_bps}|{o.fills_root}"
    return "0x" + hashlib.sha256(raw.encode()).hexdigest()


def _trade_hash(instance_id: str, o: EpisodeOutcome) -> str:
    """Non-zero bytes32 anchor for a settled episode (TradeJournal rejects 0x0)."""
    return _b32(f"{instance_id}|{o.fills_root}|{o.pnl_bps}|{o.closed_at}")


class BscMirrorError(RuntimeError):
    pass


class BscMirror:
    """ChainPort that mirrors proofs to the deployed verifier contracts via `cast`."""

    def __init__(self, identity: str, journal: str, ledger: str, private_key: str,
                 agent_wallet: str, rpc_url: str = MAINNET_RPC,
                 agent_uri: str = "https://gridora.vercel.app") -> None:
        if not (identity and journal and ledger):
            raise BscMirrorError("contract addresses not set (GRIDORA_IDENTITY/JOURNAL/LEDGER_ADDR)")
        if not private_key:
            raise BscMirrorError("no signer key (DEPLOYER_PRIVATE_KEY)")
        self.identity = identity
        self.journal = journal
        self.ledger = ledger
        self._key = private_key
        self.agent_wallet = agent_wallet or ZERO_ADDR
        self.rpc_url = rpc_url
        self.agent_uri = agent_uri
        self.agent_id: int | None = None
        self._memory: dict[str, list[MemoryRecord]] = {}

    @classmethod
    def from_env(cls) -> "BscMirror":
        return cls(
            identity=os.environ.get("GRIDORA_IDENTITY_ADDR", ""),
            journal=os.environ.get("GRIDORA_JOURNAL_ADDR", ""),
            ledger=os.environ.get("GRIDORA_LEDGER_ADDR", ""),
            private_key=os.environ.get("DEPLOYER_PRIVATE_KEY", ""),
            agent_wallet=os.environ.get("GRIDORA_AGENT_ADDRESS", ""),
            rpc_url=os.environ.get("GRIDORA_MAINNET_RPC", MAINNET_RPC),
            agent_uri=os.environ.get("GRIDORA_AGENT_URI") or "https://gridora.vercel.app",
        )

    async def _cast(self, *args: str, send: bool) -> str:
        """Run `cast call|send`; return decoded output (call) or tx hash (send)."""
        base = ["cast", "send" if send else "call", "--rpc-url", self.rpc_url]
        if send:
            base += ["--json", "--private-key", self._key]  # local single-signer; key not logged
        proc = await asyncio.create_subprocess_exec(
            *base, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise BscMirrorError((err or out).decode().strip()[:400] or "cast failed")
        text = out.decode().strip()
        if not send:
            return text
        try:
            return str(json.loads(text).get("transactionHash", ""))
        except json.JSONDecodeError:
            return text  # older cast prints the hash plainly

    async def _agent_of(self) -> int:
        raw = await self._cast(self.identity, "agentOf(address)(uint256)", self.agent_wallet, send=False)
        return int(raw.split()[0]) if raw else 0  # cast may append the type, e.g. "1"

    async def register_identity(self, agent_address: str) -> str:
        """Mint the agent identity NFT on the custom registry (idempotent: if this wallet
        already holds one, reuse its agentId and don't re-mint)."""
        existing = await self._agent_of()
        if existing > 0:
            self.agent_id = existing
            return ""  # already registered on-chain
        tx = await self._cast(self.identity, "register(string,address)",
                              self.agent_uri, self.agent_wallet, send=True)
        self.agent_id = await self._agent_of()
        return tx

    async def _ensure_agent(self) -> int:
        if self.agent_id is None:
            self.agent_id = await self._agent_of()
        if not self.agent_id:
            raise BscMirrorError("no agentId on the custom registry — register_identity first")
        return self.agent_id

    async def commit_strategy(self, instance_id: str, config: GridConfig) -> str:
        return await self._cast(self.ledger, "commit(bytes32,bytes32)",
                                _b32(instance_id), config_hash(config), send=True)

    async def attest(self, instance_id: str, outcome: EpisodeOutcome) -> str:
        agent_id = await self._ensure_agent()
        tx = await self._cast(self.ledger, "attest(bytes32,bytes32)",
                              _b32(instance_id), outcome_hash(outcome), send=True)
        # mirror the settled episode into the append-only journal (closedAt <= now)
        closed = outcome.closed_at if 0 < outcome.closed_at <= int(time.time()) else int(time.time())
        await self._cast(self.journal, "record(uint256,bytes32,int256,uint64)",
                         str(agent_id), _trade_hash(instance_id, outcome),
                         str(outcome.pnl_bps), str(closed), send=True)
        return tx

    async def write_memory(self, record: MemoryRecord) -> str:
        self._memory.setdefault(record.regime_label, []).append(record)  # recall is off-chain
        return ""

    async def recall(self, regime: RegimeFingerprint, k: int = 5) -> Sequence[MemoryRecord]:
        records = list(self._memory.get(regime.label, []))
        records.sort(key=lambda r: risk_score(r.outcome), reverse=True)
        return records[:k]


def _demo() -> None:
    """Self-check: the bytes32 derivations are well-formed (0x + 64 hex, non-zero) and
    deterministic. No network."""
    iid = "CAKE/USDT-123-1"
    o = EpisodeOutcome("i", __import__("decimal").Decimal("1.5"), 42, 3, 7, 1700000000, "0xabc")
    for h in (_b32(iid), outcome_hash(o), _trade_hash(iid, o)):
        assert h.startswith("0x") and len(h) == 66, h
        assert int(h, 16) != 0, h
    assert _b32(iid) == _b32(iid) and _b32(iid) != _b32(iid + "x")
    print("bsc_mirror._demo OK:", _b32(iid)[:18], outcome_hash(o)[:18], _trade_hash(iid, o)[:18])


async def _cli() -> None:
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    m = BscMirror.from_env()
    if cmd == "register":
        tx = await m.register_identity(m.agent_wallet)
        print(json.dumps({"agentId": m.agent_id, "tx": tx or "(already registered)",
                          "explorer": f"https://bscscan.com/tx/{tx}" if tx else ""}))
    elif cmd == "status":
        aid = await m._agent_of()
        total = await m._cast(m.identity, "totalAgents()(uint256)", send=False)
        count = await m._cast(m.journal, "tradeCountByAgent(uint256)(uint256)", str(aid or 0), send=False) if aid else "0"
        print(json.dumps({"agentId": aid, "totalAgents": total.split()[0] if total else "0",
                          "tradeCount": count.split()[0] if count else "0",
                          "identity": m.identity, "journal": m.journal, "ledger": m.ledger}))
    else:
        print("usage: python -m gridora.adapters.chain.bsc_mirror [register|status]")


if __name__ == "__main__":
    if os.environ.get("GRIDORA_IDENTITY_ADDR"):
        asyncio.run(_cli())
    else:
        _demo()
