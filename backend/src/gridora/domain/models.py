"""Domain models — pure dataclasses/enums shared by the engine, agent, and ports.

PORTED FROM: perps-agent/backend/src/perpsagent/domain/models.py, reshaped for the
BNB Hack stack:
  - Spot grid (no leverage): capital is sized in the stablecoin `quote_per_grid`;
    the launch step derives a base `order_size` per level from it.
  - `RegimeFingerprint` is the CoinMarketCap AI Agent Hub read (F&G / funding /
    momentum / social), not the CEX microstructure read.
  - `Order`/`Fill` carry `tx_hash` so every settled trade can be mirrored on-chain.

The shapes still match perps-agent closely enough that `domain/grid.py` and the
execution engine port almost verbatim (both key off `cfg.order_size` + `bias`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Spacing(str, Enum):
    ARITHMETIC = "ARITHMETIC"
    GEOMETRIC = "GEOMETRIC"


class Venue(str, Enum):
    FAKE = "FAKE"
    BSC_TWAK = "BSC_TWAK"


class RiskPreset(str, Enum):
    SAFE = "SAFE"
    BALANCED = "BALANCED"
    AGGRESSIVE = "AGGRESSIVE"


class GridState(str, Enum):
    INITIALIZING = "INITIALIZING"
    RUNNING = "RUNNING"
    REBALANCING = "REBALANCING"
    EXITING = "EXITING"
    HALTED = "HALTED"


@dataclass(frozen=True)
class MarketMeta:
    """Venue trading rules for a market. `tick`/`qty_step`/`min_qty` align orders to
    what the venue will accept; decimals matter on-chain (BEP-20 amounts are integers
    of `10**decimals`)."""

    market: str
    tick: Decimal
    qty_step: Decimal
    min_qty: Decimal
    base_decimals: int = 18
    quote_decimals: int = 18


@dataclass
class GridConfig:
    """One grid instance. `market` is a BEP-20 base/quote pair from the 149-token
    allowlist, e.g. 'CAKE/USDT'.

    `quote_per_grid` is the user-facing knob (stablecoin deployed per level); the
    launch step converts it into a fixed base `order_size` at the current mid so the
    paired buy/sell legs bank a clean spread (constant base qty per level)."""

    instance_id: str
    market: str
    lower: Decimal
    upper: Decimal
    levels: int
    order_size: Decimal = Decimal("0")   # base qty per level (derived from quote_per_grid at launch)
    quote_per_grid: Decimal = Decimal("0")  # capital per level, in quote (stablecoin) — user knob
    spacing: Spacing = Spacing.GEOMETRIC
    max_levels: int = 50
    preset: RiskPreset = RiskPreset.BALANCED
    venue: Venue = Venue.FAKE
    policy_version: str = "v0"  # provenance for the on-chain commitment
    # Grid mode for the regime: 0 = symmetric (ranging), +1 = long-bias (uptrend:
    # buy-ladder only, sells appear as paired take-profits), -1 = short-bias.
    bias: int = 0


@dataclass
class Order:
    instance_id: str
    market: str
    side: Side
    price: Decimal
    qty: Decimal
    external_id: str = ""        # grid-{instance}-L{level}-{nonce} — recovery anchor
    level: int = 0
    post_only: bool = True
    order_id: Optional[str] = None  # venue handle once placed
    tx_hash: str = ""            # on-chain settlement proof (bsc_twak path)


@dataclass
class Fill:
    instance_id: str
    market: str
    side: Side
    price: Decimal
    qty: Decimal
    external_id: str = ""
    level: int = 0
    ts: int = 0
    fee: Decimal = Decimal("0")
    tx_hash: str = ""


@dataclass
class Position:
    market: str
    net_base: Decimal = Decimal("0")
    avg_entry: Decimal = Decimal("0")


@dataclass
class BalanceView:
    """Spot balances. `equity_quote` is total account value marked in the quote
    (stablecoin) — the number the circuit breaker and account guard watch."""

    quote_free: Decimal = Decimal("0")
    base_free: Decimal = Decimal("0")
    equity_quote: Decimal = Decimal("0")


@dataclass(frozen=True)
class RegimeFingerprint:
    """Output of SENSE. Built from CoinMarketCap AI Agent Hub data.

    `bias` is the grid lean this regime implies (-1 short / 0 ranging / +1 long);
    `label` is the human-readable regime class used in the rationale + memory key."""

    market: str
    fear_greed: int = 50            # 0..100 (master risk dial)
    funding_bps: Decimal = Decimal("0")   # derivatives funding, basis points / 8h
    momentum: Decimal = Decimal("0")      # signed trend strength, ~[-1, 1]
    social_heat: Decimal = Decimal("0")
    label: str = "RANGING"          # TRENDING_UP | TRENDING_DOWN | RANGING | SQUEEZE
    bias: int = 0                   # -1 short-lean, 0 neutral, +1 long-lean
    stale: bool = False             # True when SENSE read NO live data (this neutral is synthesized)


@dataclass
class EpisodeOutcome:
    instance_id: str
    realized_pnl_quote: Decimal
    pnl_bps: int                    # realized PnL vs deployed capital, basis points
    trades: int                     # closed round-trips (paired fills)
    max_drawdown_bps: int           # worst peak-to-trough equity drop, basis points
    closed_at: int
    fills_root: str = ""            # Merkle/hash root of fills for verifiability


@dataclass
class MemoryRecord:
    regime_label: str
    config: GridConfig
    outcome: EpisodeOutcome
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TokenMetric:
    """A liquidity + momentum read for one token (from CMC), used to pick the trading
    universe. `vol_24h_usd` gates tradeability; `pct_7d` is the relative-strength lean;
    `range_24h_pct` ((24h high − low)/price × 100) is the grid fuel — how far price
    actually swings is what a maker grid harvests (0 = unknown -> band falls back to preset)."""

    symbol: str
    price_usd: Decimal = Decimal(0)
    vol_24h_usd: Decimal = Decimal(0)
    pct_1h: float = 0.0          # short-term momentum — used to detect a rising move STALLING (profit-lock)
    pct_24h: float = 0.0
    pct_7d: float = 0.0
    range_24h_pct: float = 0.0


@dataclass(frozen=True)
class MarketPick:
    """A market the universe selector chose to grid: the pair, the regime lean, and the
    fraction of total margin to deploy on it (weights across picks sum to ~1)."""

    market: str
    bias: int = 0
    weight: Decimal = Decimal(0)
    score: float = 0.0
    vol_24h_usd: Decimal = Decimal(0)
    pct_7d: float = 0.0
    range_24h_pct: float = 0.0   # 24h high-low range %, sizes this grid's band
