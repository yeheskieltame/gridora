"""Portfolio — run N concurrent grids across the liquidity/momentum-selected universe.

The competition shape (see token research): spread a fixed margin across a few LIQUID,
uncorrelated names chosen from live CMC data, rather than one hardcoded pair. Each grid
gets its OWN exchange instance (concurrent consume() loops can't share one — they'd
double-poll the same resting book) and its OWN per-episode guards; a portfolio-level
account kill-switch flattens EVERYTHING if total equity breaches the cap (the 30%-DQ
backstop). Per-grid breakers cap each slice; this caps the aggregate.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Awaitable, Callable, Optional

from ..agent.loop import new_instance_id
from ..domain.models import EpisodeOutcome, MarketPick, RiskPreset, Venue
from ..domain.ports import ChainPort, ExchangePort, SignalPort
from ..domain.universe import select_markets
from .engine import GridEngine
from .safety import AccountGuard, Allowlist, CircuitBreaker, ProfitGuard
from .service import PRESETS, GridService


class Portfolio:
    def __init__(
        self,
        exchange_for: Callable[[str], ExchangePort],   # one fresh ExchangePort per market
        signals: SignalPort,
        chain: ChainPort,
        allowlist: Allowlist,
        margin: Decimal,
        *,
        venue: Venue = Venue.BSC_TWAK,
        quote: str = "USDT",
        n_markets: int = 4,
        min_vol_usd: Decimal = Decimal(20_000_000),
        preset: RiskPreset = RiskPreset.BALANCED,
        account_drawdown_frac: Decimal = Decimal("0.15"),   # portfolio kill-switch (< 30% DQ)
        recenter_interval: float = 0.0,
        agent_address: str = "0xGRIDORA_AGENT",
        total_equity_fn: Optional[Callable[[], Awaitable[Optional[Decimal]]]] = None,
    ) -> None:
        self.exchange_for = exchange_for
        self.signals = signals
        self.chain = chain
        self.allowlist = allowlist
        self.margin = margin
        self.venue = venue
        self.quote = quote
        self.n_markets = n_markets
        self.min_vol_usd = min_vol_usd
        self.preset = preset
        self.recenter_interval = recenter_interval
        self.agent_address = agent_address
        self.account_guard = AccountGuard(max_drop=margin * account_drawdown_frac)
        self._total_equity_fn = total_equity_fn
        self.engines: dict[str, GridEngine] = {}
        self.picks: list[MarketPick] = []
        self.booked: Decimal = Decimal(0)            # realized PnL of closed grids
        self._exchanges: list[ExchangePort] = []
        self._seq = 0

    async def select(self) -> list[MarketPick]:
        metrics = await self.signals.market_metrics(sorted(self.allowlist.symbols))
        return select_markets(metrics, self.allowlist.symbols, self.quote,
                              self.n_markets, self.min_vol_usd)

    async def launch(self) -> list[MarketPick]:
        """Select the universe, then launch one grid per pick (own exchange + guards)."""
        self.picks = await self.select()
        if not self.picks:
            raise RuntimeError("universe selection returned no liquid eligible markets")
        try:
            await self.chain.register_identity(self.agent_address)
        except Exception:  # noqa: BLE001 — identity is best-effort
            pass
        armed = False
        for pick in self.picks:
            self._seq += 1
            iid = new_instance_id(pick.market, self._seq)
            self.allowlist.assert_permits(pick.market)       # both legs in-scope BEFORE commit
            ex = self.exchange_for(pick.market)
            self._exchanges.append(ex)
            bid, ask = await ex.best_bid_ask(pick.market)
            mid = (bid + ask) / 2
            slice_margin = self.margin * pick.weight
            cfg = GridService.config_for_preset(iid, pick.market, self.preset, slice_margin,
                                                mid, self.venue, range_24h_pct=pick.range_24h_pct)
            cfg.bias = pick.bias                              # lean from the pick's momentum
            breaker = CircuitBreaker(max_drawdown_frac=PRESETS[self.preset].dd_frac)
            profit = ProfitGuard(trail_frac=Decimal("0.4"), trail_arm=slice_margin * Decimal("0.05"))
            if not armed and self.account_guard.max_drop > 0:
                try:
                    self.account_guard.arm((await ex.balance()).equity_quote)
                    armed = True
                except Exception:  # noqa: BLE001 — unarmed portfolio guard fails open
                    pass
            await self.chain.commit_strategy(iid, cfg)        # pin hash BEFORE trading
            eng = GridEngine(ex, cfg, breaker=breaker, profit_guard=profit,
                             account_guard=None, monitor_interval=self.recenter_interval)
            await eng.start()
            self.engines[iid] = eng
        return self.picks

    async def _total_equity(self) -> Optional[Decimal]:
        if self._total_equity_fn is not None:
            return await self._total_equity_fn()
        # Live: all per-market exchanges share ONE TWAK wallet, so any one balance is the total.
        for ex in self._exchanges:
            try:
                return (await ex.balance()).equity_quote
            except Exception:  # noqa: BLE001
                continue
        return None

    async def supervise(self) -> None:
        """Drive every grid's consumer + monitor concurrently, plus a portfolio-level
        account-guard monitor that flattens ALL grids if total equity breaches the cap."""
        async def portfolio_monitor() -> None:
            if self.account_guard.max_drop <= 0:
                return
            while self.engines:
                await asyncio.sleep(self.recenter_interval or 30.0)
                eq = await self._total_equity()
                if eq is not None and self.account_guard.check(eq):
                    print(f"  !! PORTFOLIO guard: {self.account_guard.reason} — flatten ALL grids")
                    await self.close_all()
                    return
        await asyncio.gather(portfolio_monitor(),
                             *[self._drive(e) for e in list(self.engines.values())],
                             return_exceptions=True)

    async def _drive(self, eng: GridEngine) -> None:
        await asyncio.gather(eng.consume(), eng.monitor(), return_exceptions=True)

    async def close_all(self) -> dict[str, EpisodeOutcome]:
        """Stop, flatten, and attest every grid. Returns outcomes; books realized PnL."""
        results: dict[str, EpisodeOutcome] = {}
        for iid, eng in list(self.engines.items()):
            try:
                await eng.stop()
                self.booked += eng.realized
                out = eng.outcome()
                results[iid] = out
                try:
                    await self.chain.attest(iid, out)
                except Exception as e:  # noqa: BLE001 — keep closing the rest
                    print(f"  ! attest {iid} failed: {e}")
            except Exception as e:  # noqa: BLE001
                print(f"  ! close {iid} failed: {e}")
        self.engines.clear()
        return results

    @property
    def open_realized(self) -> Decimal:
        return sum((e.realized for e in self.engines.values()), Decimal(0))

    @property
    def total_realized(self) -> Decimal:
        """Booked (closed) + open grids' realized — the portfolio's PnL so far."""
        return self.booked + self.open_realized
