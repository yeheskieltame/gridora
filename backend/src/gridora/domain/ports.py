"""Hexagonal ports — the only contracts the engine and agent depend on.

Adapters implement these Protocols; the domain core and the agent NEVER import a
concrete adapter or any chain/wallet SDK. Add a venue by implementing
`ExchangePort` and registering it in `adapters/exchanges/registry.py`.

PORTED FROM: perps-agent/backend/src/perpsagent/domain/ports.py
Differences for Gridora:
  - ExchangePort is implemented ONLY by the TWAK adapter (sole execution layer).
  - SignalPort is implemented by the CoinMarketCap AI Agent Hub adapter.
  - PaymentPort (new) wraps x402 pay-per-call settled through TWAK native x402.
  - ChainPort targets BSC (IdentityRegistry + TradeJournal + StrategyLedger).
"""
from __future__ import annotations

from decimal import Decimal
from typing import AsyncIterator, Protocol, Sequence

from .models import (
    BalanceView,
    EpisodeOutcome,
    Fill,
    GridConfig,
    MarketMeta,
    MemoryRecord,
    Order,
    Position,
    RegimeFingerprint,
    TokenMetric,
)


class ExchangePort(Protocol):
    """A trading venue. For Gridora the ONLY implementation is `bsc_twak`, which
    signs every order locally through the Trust Wallet Agent Kit. Keys never leave
    the device — this is the heart of the self-custody / Best-Use-of-TWAK story."""

    venue: str

    async def market_meta(self, market: str) -> MarketMeta: ...
    async def best_bid_ask(self, market: str) -> tuple[Decimal, Decimal]: ...
    async def place_order(self, order: Order) -> Order:
        """Sign via TWAK and submit a single grid order to BSC. Returns the order
        with venue id + tx hash populated."""
        ...
    async def cancel_order(self, market: str, order_id: str) -> None: ...
    async def cancel_all(self, market: str) -> None: ...
    async def flatten(self, market: str) -> None:
        """Emergency exit: swap the whole base position back to the stablecoin
        quote (reduce-only). Used by the circuit breaker and graceful shutdown."""
        ...
    async def open_orders(self, market: str) -> Sequence[Order]: ...
    async def balance(self) -> BalanceView: ...
    async def positions(self) -> Sequence[Position]: ...
    async def stream_fills(self) -> AsyncIterator[Fill]:
        """Yield fills as they happen (drive grid logic from fills)."""
        ...


class SignalPort(Protocol):
    """External regime inputs. For Gridora: CoinMarketCap AI Agent Hub — Fear &
    Greed, funding rates, momentum, social heat. Paid per call via PaymentPort."""

    async def snapshot(self, market: str) -> RegimeFingerprint: ...
    async def market_metrics(self, symbols: Sequence[str]) -> dict[str, TokenMetric]:
        """Bulk liquidity + 7d-momentum read (24h USD volume, % changes) for universe
        selection. Keyed by symbol; symbols with no data are omitted."""
        ...


class PaymentPort(Protocol):
    """x402 pay-per-call. Settles each CMC data request through TWAK's native x402
    (EIP-3009 / on-chain transfer on BNB Chain). Real payment in the trade loop."""

    async def pay_and_fetch(self, url: str, params: dict) -> dict:
        """Make an x402-gated GET: on HTTP 402, sign the payment via TWAK, retry,
        return the JSON body. Returns the payment tx hash in the response meta."""
        ...


class ChainPort(Protocol):
    """BSC on-chain layer: identity, append-only trade journal, strategy ledger.

    Makes the agent verifiable: commit the config hash BEFORE trading, attest the
    outcome AFTER, mirror every settled trade to a public append-only log.
    PORTED FROM: perps-agent ChainPort + BridgeAgent TradeJournal/IdentityRegistry.
    """

    async def register_identity(self, agent_address: str) -> str:
        """Mint/ensure the agent's ERC-8004 identity NFT. Returns tx hash."""
        ...
    async def commit_strategy(self, instance_id: str, config: GridConfig) -> str:
        """Commit config hash before trading (pre-commitment). Returns tx hash."""
        ...
    async def attest(self, instance_id: str, outcome: EpisodeOutcome) -> str:
        """Write verified outcome to TradeJournal. Returns tx hash."""
        ...
    async def write_memory(self, record: MemoryRecord) -> str: ...
    async def recall(self, regime: RegimeFingerprint, k: int = 5) -> Sequence[MemoryRecord]: ...


class StorePort(Protocol):
    """Local persistence (fills, instances, recovery anchors)."""

    async def record_fill(self, fill: Fill) -> None: ...
    async def load_open_instances(self) -> Sequence[GridConfig]: ...
