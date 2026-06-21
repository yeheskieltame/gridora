"""PaperExchange — paper trading: REAL price feed, SIMULATED execution. No TWAK, no
on-chain, no real money.

Same simulated order book as FakeExchange, but the mid comes from a LIVE price source
(`price_fn`) instead of scripted move_price(), and a simulated wallet (quote + base) marks
to the real price — so PnL and the account guard behave like a real run. Fills are recorded
to the optional store. This is the "test live without risk" venue: prove the strategy on
real data before pointing the same engine at the live TWAK adapter.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, AsyncIterator, Awaitable, Callable

from ...domain.models import BalanceView, Fill, Side
from .fake import FakeExchange


class PaperExchange(FakeExchange):
    venue = "PAPER"

    def __init__(self, market: str, price_fn: Callable[[], Awaitable[Decimal]],
                 config: dict[str, Any] | None = None, poll_interval: float = 10.0,
                 spread_bps: int = 10, store=None) -> None:
        super().__init__(config)
        self.market = market
        self.price_fn = price_fn          # async () -> Decimal: the base token's real USD price
        self.poll_interval = poll_interval
        self.spread_bps = spread_bps
        self.store = store
        self._quote = self._equity        # simulated stablecoin balance
        self._base = Decimal(0)           # simulated base-token balance

    async def best_bid_ask(self, market: str) -> tuple[Decimal, Decimal]:
        px = await self.price_fn()
        if px and px > 0:
            self._mid = Decimal(str(px))
        half = self._mid * Decimal(self.spread_bps) / Decimal(10_000)
        return (self._mid - half, self._mid + half)

    def _settle(self, f: Fill) -> None:
        """Move the simulated wallet on a fill (no reservation; deploy_frac bounds exposure)."""
        if f.side is Side.BUY:
            self._quote -= f.qty * f.price
            self._base += f.qty
        else:
            self._quote += f.qty * f.price
            self._base -= f.qty

    async def stream_fills(self) -> AsyncIterator[Fill]:
        """Poll the REAL price; fill any resting order it crosses (simulated), settle the
        wallet, record, and emit — exactly the engine's fill-driven loop, on live data."""
        while True:
            await asyncio.sleep(self.poll_interval)
            try:
                px = await self.price_fn()
            except Exception as e:  # noqa: BLE001 — a bad price read must not kill the stream
                print(f"  ! paper price fetch failed: {e}")
                continue
            if not px or px <= 0:
                continue
            for f in self._match(self.market, Decimal(str(px))):
                self._settle(f)
                if self.store is not None:
                    try:
                        await self.store.record_fill(f)
                    except Exception:  # noqa: BLE001 — recording is best-effort
                        pass
                yield f

    async def balance(self) -> BalanceView:
        equity = self._quote + self._base * self._mid   # mark to the real price
        return BalanceView(quote_free=self._quote, base_free=self._base, equity_quote=equity)

    async def flatten(self, market: str) -> None:
        self.flatten_calls.append(market)
        await self.cancel_all(market)
        if self._base != 0 and self._mid > 0:
            self._quote += self._base * self._mid       # simulate selling base -> quote at mid
            self._base = Decimal(0)
        self._positions.pop(market, None)


def coingecko_price_fn(signals, market: str) -> Callable[[], Awaitable[Decimal]]:
    """Bind a CoinGeckoSignals (or any object with async price(market)) to one market."""
    async def _p() -> Decimal:
        return await signals.price(market)
    return _p
