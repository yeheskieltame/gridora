"""GridService — the typed facade. The ONLY seam the UI / runner / CLI talks to.

The frontend and any CLI call these methods; they never import the engine, the
adapters, or any chain/wallet SDK. Keep this surface small, typed, versioned.

ADAPTED FROM perps-agent/backend/src/perpsagent/app/service.py. The one-tap risk
presets map to band width, level count, deployed fraction, and the circuit-breaker
drawdown cap — the perps preset pattern, tuned for the BNB Hack 30% DQ.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from ..agent.loop import LearningLoop, new_instance_id
from ..domain.grid import fee_floor_bps, fit_levels_to_fees, vol_half_band
from ..domain.models import GridConfig, RiskPreset, Spacing, Venue


@dataclass(frozen=True)
class Preset:
    half_band: Decimal     # ± fraction of mid for the band edges
    levels: int            # grid levels across the band (capped by the fee floor)
    deploy_frac: Decimal   # fraction of the margin actually put to work
    dd_frac: Decimal       # circuit-breaker drawdown cap as a fraction of margin
    min_spacing_bps: int   # extra floor on adjacent-level spacing (on top of fees)


# Safe books many small spreads in a tight band and halts early; Aggressive widens
# the band, deploys more, and tolerates a deeper (but still well-below-DQ) drawdown.
# The level count is the requested MAX — the fee floor (grid.fee_floor_bps) widens the
# grid (drops levels) whenever the spacing wouldn't clear a round-trip's cost.
PRESETS: dict[RiskPreset, Preset] = {
    RiskPreset.SAFE:       Preset(Decimal("0.05"), 8,  Decimal("0.50"), Decimal("0.08"), 40),
    RiskPreset.BALANCED:   Preset(Decimal("0.07"), 12, Decimal("0.75"), Decimal("0.12"), 30),
    RiskPreset.AGGRESSIVE: Preset(Decimal("0.10"), 20, Decimal("0.95"), Decimal("0.18"), 20),
}


@dataclass
class LaunchResult:
    instance_id: str
    commit_tx: str
    orders_placed: int
    bias: int
    rationale: str


class GridService:
    def __init__(self, loop: LearningLoop, venue: Venue = Venue.FAKE) -> None:
        self.loop = loop
        self.venue = venue
        self._seq = 0

    async def launch(self, market: str, preset: RiskPreset, quote_margin: Decimal) -> LaunchResult:
        """One-tap launch: build a GridConfig from the preset + current mid, tune the
        breaker for the preset's drawdown cap, then run SENSE->RECALL->DECIDE->
        COMMIT(on-chain)->EXECUTE."""
        bid, ask = await self.loop.exchange.best_bid_ask(market)
        mid = (bid + ask) / 2
        self._seq += 1
        cfg = self.config_for_preset(
            new_instance_id(market, self._seq), market, preset, quote_margin, mid, self.venue)

        # Reset per-episode guard state so a relaunch never inherits a tripped/peaked guard,
        # then (re)tune the auto caps for this preset/margin (operator-set caps are kept).
        p = PRESETS[preset]
        self.loop.breaker.reset()
        self.loop.profit_guard.reset()
        if self.loop.breaker.max_drawdown <= 0:
            self.loop.breaker.max_drawdown = quote_margin * p.dd_frac
        if self.loop.account_guard is not None:
            self.loop.account_guard.reset()
            if self.loop.account_guard.max_drop <= 0:
                self.loop.account_guard.max_drop = quote_margin * p.dd_frac

        res = await self.loop.plan_and_launch(cfg)
        return LaunchResult(
            instance_id=res["instance_id"], commit_tx=res["commit_tx"],
            orders_placed=res["orders_placed"], bias=res["bias"], rationale=res["rationale"],
        )

    async def status(self, instance_id: str) -> dict:
        """Open grid: state, band, net position, realized PnL, resting orders."""
        eng = self.loop.engine(instance_id)
        return {
            "instance_id": instance_id,
            "market": eng.cfg.market,
            "state": eng.state.value,
            "bias": eng.bias,
            "band": [str(eng.lower), str(eng.upper)],
            "levels": eng.cfg.levels,
            "net_base": str(eng.net_inventory()),
            "avg_entry": str(eng.pos_avg),
            "realized_pnl_quote": str(eng.realized),
            "fills": eng.fill_count,
            "resting_orders": len(eng._resting),
            "rationale": self.loop.rationale(instance_id),
        }

    async def close(self, instance_id: str) -> dict:
        """Flatten, ATTEST(on-chain), write StrategyMemory (LEARN). Returns outcome."""
        result = await self.loop.close_and_learn(instance_id)
        o = result["outcome"]
        return {
            "instance_id": instance_id,
            "realized_pnl_quote": str(o.realized_pnl_quote),
            "pnl_bps": o.pnl_bps,
            "trades": o.trades,
            "max_drawdown_bps": o.max_drawdown_bps,
            "fills_root": o.fills_root,
            "attest_tx": result["attest_tx"],
            "attest_ok": result["attest_ok"],
        }

    @staticmethod
    def config_for_preset(instance_id: str, market: str, preset: RiskPreset,
                          quote_margin: Decimal, mid: Decimal,
                          venue: Venue = Venue.FAKE, range_24h_pct: float = 0.0) -> GridConfig:
        """Safe/Balanced/Aggressive -> band width, level count, per-level size. The
        band is centered on the current mid; `order_size` is the base qty each level
        deploys (capital-per-level / mid). `range_24h_pct` (>0) sizes the band to the
        token's recent daily swing (clamped to the preset's half-band as the risk cap);
        0 = use the preset's fixed half-band."""
        p = PRESETS[preset]
        half = vol_half_band(range_24h_pct, p.half_band)
        lower = mid * (Decimal(1) - half)
        upper = mid * (Decimal(1) + half)
        # Fee-aware: widen the grid (fewer levels) until each pair clears a round-trip.
        floor = max(p.min_spacing_bps, fee_floor_bps())
        levels = fit_levels_to_fees(lower, upper, p.levels, Spacing.GEOMETRIC, floor)
        quote_per_grid = (quote_margin * p.deploy_frac) / levels
        order_size = quote_per_grid / mid if mid > 0 else Decimal(0)
        return GridConfig(
            instance_id=instance_id, market=market, lower=lower, upper=upper, levels=levels,
            order_size=order_size, quote_per_grid=quote_per_grid, spacing=Spacing.GEOMETRIC,
            preset=preset, venue=venue, policy_version="v0",
        )
