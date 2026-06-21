"""In-memory ExchangePort for offline dry-runs and unit tests (no keys, no net).

PORTED FROM perps-agent/backend/src/perpsagent/adapters/exchanges/fake.py, adapted
to Gridora's spot models. Fully functional: keeps an order book, simulates limit
fills as the price crosses levels, and streams fills like a real venue. This is the
seam the engine and agent run against with zero network or keys — it is what makes
`python -m gridora.runner --mode dry` a real end-to-end exercise of the loop.
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any, AsyncIterator, Sequence

from ...domain.models import BalanceView, Fill, MarketMeta, Order, Position, Side

venue = "FAKE"


class FakeExchange:
    venue = "FAKE"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self._mid = Decimal(str(cfg.get("mid", "100")))
        self._tick = Decimal(str(cfg.get("tick", "0.01")))
        self._step = Decimal(str(cfg.get("step", "0.001")))
        self._min = Decimal(str(cfg.get("min_order_size", "0.001")))
        self._equity = Decimal(str(cfg.get("equity", "10000")))
        # Optional recent-close series (oldest→newest); defaults to flat at mid.
        self._closes = [Decimal(str(c)) for c in cfg.get("closes", [])]
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._fills: asyncio.Queue[Fill] = asyncio.Queue()
        self._seq = 0
        self.flatten_calls: list[str] = []    # markets flattened (test introspection)

    async def market_meta(self, market: str) -> MarketMeta:
        return MarketMeta(market=market, tick=self._tick, qty_step=self._step, min_qty=self._min)

    async def best_bid_ask(self, market: str) -> tuple[Decimal, Decimal]:
        return (self._mid - self._tick, self._mid + self._tick)

    async def klines(self, market: str, interval: str = "1", limit: int = 60) -> list[Decimal]:
        return (self._closes or [self._mid] * limit)[-limit:]

    async def place_order(self, order: Order) -> Order:
        self._seq += 1
        order.order_id = f"fake-{self._seq}"
        order.tx_hash = f"0xfake{self._seq:060x}"[:66]
        self._orders[order.external_id] = order
        return order

    async def cancel_order(self, market: str, order_id: str) -> None:
        for ext, o in list(self._orders.items()):
            if o.order_id == order_id or o.external_id == order_id:
                del self._orders[ext]
                return

    async def cancel_all(self, market: str) -> None:
        self._orders = {e: o for e, o in self._orders.items() if o.market != market}

    async def flatten(self, market: str) -> None:
        self.flatten_calls.append(market)
        await self.cancel_all(market)
        self._positions.pop(market, None)

    async def open_orders(self, market: str) -> Sequence[Order]:
        return [o for o in self._orders.values() if o.market == market]

    async def balance(self) -> BalanceView:
        return BalanceView(quote_free=self._equity, base_free=Decimal(0), equity_quote=self._equity)

    async def positions(self) -> Sequence[Position]:
        return list(self._positions.values())

    async def stream_fills(self) -> AsyncIterator[Fill]:
        while True:
            yield await self._fills.get()

    # ---- test / simulation helpers (not part of ExchangePort) ----

    def _match(self, market: str, price: Decimal) -> list[Fill]:
        """Set the mid and fill any resting order the price crosses; return the Fills (no
        queueing). Shared by move_price (scripted) and PaperExchange (real price feed)."""
        self._mid = price
        filled: list[Fill] = []
        for ext, o in sorted(self._orders.items(), key=lambda kv: kv[1].price):
            if o.market != market:
                continue
            crossed = (o.side is Side.BUY and price <= o.price) or (
                o.side is Side.SELL and price >= o.price)
            if crossed:
                del self._orders[ext]
                filled.append(Fill(
                    instance_id=o.instance_id, market=o.market, side=o.side, price=o.price,
                    qty=o.qty, external_id=o.external_id, level=o.level,
                    ts=int(time.time()), tx_hash=o.tx_hash))
        return filled

    async def move_price(self, market: str, price: Decimal) -> list[Fill]:
        """Move the mid; fill any limit order the move crosses, emit Fill events."""
        fills = self._match(market, Decimal(str(price)))
        for f in fills:
            await self._fills.put(f)
        return fills
