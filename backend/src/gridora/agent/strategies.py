"""Strategy registry — the menu Claude routes between.

A pure deterministic grid is "blind": it can't tell when to STOP or SWITCH as the
market changes. So Gridora exposes a set of grid *modes* (strategies); the Claude
brain (agent/brain.py) reads the CoinMarketCap regime each cycle and picks one,
adjusts its band/levels in realtime, enables/disables modes, or halts to stablecoin.

Every strategy is fully adjustable (band width, levels, spacing, bias, deployed
fraction) and new ones can be added to STRATEGIES. The deterministic guardrails
(circuit breaker, allowlist, inventory cap, drawdown) still hard-enforce risk on top
of whatever Claude selects — Claude routes, it never overrides a risk limit.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from ..domain.grid import fee_floor_bps, fit_levels_to_fees
from ..domain.models import GridConfig, RiskPreset, Spacing, Venue

# Adjustment bounds Claude's overrides are clamped to (safety: no absurd configs).
HALF_BAND_MIN, HALF_BAND_MAX = Decimal("0.01"), Decimal("0.25")
LEVELS_MIN, LEVELS_MAX = 4, 50
DEPLOY_MIN, DEPLOY_MAX = Decimal("0.10"), Decimal("1.0")


@dataclass(frozen=True)
class Strategy:
    """One grid mode. `is_flat` marks the halt/exit mode (sit in stablecoin)."""

    key: str
    name: str
    description: str            # shown to Claude (the menu) and in the TUI
    half_band: Decimal          # ± fraction of mid for the band edges
    levels: int                 # requested MAX levels (capped by the fee floor)
    bias: int                   # -1 short-ladder / 0 symmetric / +1 long-ladder
    deploy_frac: Decimal        # fraction of margin put to work
    min_spacing_bps: int = 25   # extra adjacent-spacing floor on top of fees
    spacing: Spacing = Spacing.GEOMETRIC
    preset: RiskPreset = RiskPreset.BALANCED
    enabled: bool = True        # Claude/operator can deactivate a mode
    is_flat: bool = False       # halt to stablecoin (no grid)


# The default menu. `fit`-style names map to a regime; descriptions are what Claude
# reads to choose. Tune freely — this is meant to be edited / extended.
STRATEGIES: dict[str, Strategy] = {
    "range": Strategy(
        "range", "Range Grid",
        "Symmetric mean-reversion grid for a ranging / chop market (no clear trend, "
        "neutral Fear & Greed). Buys dips and sells rips around mid.",
        Decimal("0.06"), 12, 0, Decimal("0.75"), preset=RiskPreset.BALANCED),
    "trend_long": Strategy(
        "trend_long", "Trend Grid (long)",
        "Up-trend buy-ladder: only opens longs, sells appear as paired take-profits. "
        "For positive momentum that is NOT at a greed extreme.",
        Decimal("0.08"), 14, 1, Decimal("0.70"), preset=RiskPreset.BALANCED),
    "trend_short": Strategy(
        "trend_short", "Trend Grid (short)",
        "Down-trend sell-ladder: only opens shorts. For negative momentum that is NOT "
        "at a fear extreme.",
        Decimal("0.08"), 14, -1, Decimal("0.70"), preset=RiskPreset.BALANCED),
    "tight_scalp": Strategy(
        "tight_scalp", "Tight Scalp Grid",
        "Narrow, dense grid for a calm low-volatility range — many small spreads. Use "
        "when volatility is low and price is mid-range.",
        Decimal("0.03"), 18, 0, Decimal("0.80"), preset=RiskPreset.AGGRESSIVE),
    "wide_defensive": Strategy(
        "wide_defensive", "Wide Defensive Grid",
        "Wide band, fewer levels, less capital deployed — for high volatility or "
        "uncertainty (squeeze risk, extreme funding). Survives big swings.",
        Decimal("0.12"), 8, 0, Decimal("0.50"), preset=RiskPreset.SAFE),
    "flat": Strategy(
        "flat", "Flat / Halt",
        "Stop trading and sit in the stablecoin. Use when the market is too dangerous "
        "to make a maker spread (violent trend against inventory, breaker risk).",
        Decimal("0"), 0, 0, Decimal("0"), is_flat=True),
}


def _clampD(v: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(hi, v))


def _clampI(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def with_overrides(strat: Strategy, *, half_band: Decimal | None = None,
                   levels: int | None = None, deploy_frac: Decimal | None = None,
                   bias: int | None = None) -> Strategy:
    """Apply Claude's realtime adjustments, clamped to the safety bounds."""
    return replace(
        strat,
        half_band=_clampD(half_band, HALF_BAND_MIN, HALF_BAND_MAX) if half_band is not None else strat.half_band,
        levels=_clampI(levels, LEVELS_MIN, LEVELS_MAX) if levels is not None else strat.levels,
        deploy_frac=_clampD(deploy_frac, DEPLOY_MIN, DEPLOY_MAX) if deploy_frac is not None else strat.deploy_frac,
        bias=_clampI(bias, -1, 1) if bias is not None else strat.bias,
    )


def build_config(strat: Strategy, instance_id: str, market: str, mid: Decimal,
                 margin: Decimal, venue: Venue = Venue.FAKE) -> GridConfig:
    """Materialize a strategy into a concrete GridConfig at the current mid. Raises if
    called on the flat (halt) strategy — that mode places no grid."""
    if strat.is_flat:
        raise ValueError("flat/halt strategy has no grid config")
    lower = mid * (Decimal(1) - strat.half_band)
    upper = mid * (Decimal(1) + strat.half_band)
    # Fee-aware: widen the grid (fewer levels) until each pair clears a round-trip.
    floor = max(strat.min_spacing_bps, fee_floor_bps())
    levels = fit_levels_to_fees(lower, upper, strat.levels, strat.spacing, floor)
    quote_per_grid = (margin * strat.deploy_frac) / levels
    order_size = quote_per_grid / mid if mid > 0 else Decimal(0)
    return GridConfig(
        instance_id=instance_id, market=market, lower=lower, upper=upper, levels=levels,
        order_size=order_size, quote_per_grid=quote_per_grid, spacing=strat.spacing,
        preset=strat.preset, venue=venue, policy_version=f"v0:{strat.key}", bias=strat.bias,
    )


def enabled_strategies(registry: dict[str, Strategy] | None = None) -> list[Strategy]:
    return [s for s in (registry or STRATEGIES).values() if s.enabled]
