"""PaperExchange tests — real-price-driven simulated execution + wallet, offline."""
import asyncio
from decimal import Decimal

from gridora.adapters.exchanges.paper import PaperExchange
from gridora.domain.models import Order, Side


async def test_paper_fills_on_real_price_cross_and_settles_wallet():
    feed = iter([Decimal("100"), Decimal("96"), Decimal("104")])  # start, dip (fills BUY), rip (fills SELL)

    async def price_fn():
        try:
            return next(feed)
        except StopIteration:
            return Decimal("104")

    ex = PaperExchange("CAKE/USDT", price_fn=price_fn, poll_interval=0.0,
                       config={"equity": "1000", "tick": "0.01", "step": "0.0001", "min_order_size": "0.001"})
    buy = Order(instance_id="i", market="CAKE/USDT", side=Side.BUY, price=Decimal("98"),
                qty=Decimal("1"), level=1, external_id="b1")
    sell = Order(instance_id="i", market="CAKE/USDT", side=Side.SELL, price=Decimal("102"),
                 qty=Decimal("1"), level=2, external_id="s1")
    await ex.place_order(buy)
    await ex.place_order(sell)

    gen = ex.stream_fills()
    f1 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)   # price 96 crosses BUY@98
    assert f1.side is Side.BUY and f1.price == Decimal("98")
    assert ex._quote == Decimal("902") and ex._base == Decimal("1")   # wallet settled the buy
    f2 = await asyncio.wait_for(gen.__anext__(), timeout=1.0)   # price 104 crosses SELL@102
    await gen.aclose()
    assert f2.side is Side.SELL and ex._base == Decimal("0")          # sold back to flat
    bal = await ex.balance()
    assert bal.equity_quote == ex._quote                              # flat -> equity is all quote


async def test_paper_balance_marks_to_real_price():
    async def price_fn():
        return Decimal("120")
    ex = PaperExchange("CAKE/USDT", price_fn=price_fn, config={"equity": "1000"})
    await ex.best_bid_ask("CAKE/USDT")   # pulls real price -> mid 120
    ex._base = Decimal("2")              # pretend we hold 2 base
    bal = await ex.balance()
    assert bal.equity_quote == Decimal("1000") + Decimal("2") * Decimal("120")  # marked to live price
