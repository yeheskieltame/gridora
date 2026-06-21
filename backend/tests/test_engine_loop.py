"""End-to-end engine + loop tests against the in-memory adapters (no keys, no net)."""
import asyncio
from decimal import Decimal

import pytest

from gridora.adapters.chain.memory_chain import MemoryChain
from gridora.adapters.exchanges.fake import FakeExchange
from gridora.adapters.signals.fake import FakeSignals
from gridora.agent.loop import LearningLoop
from gridora.app.engine import GridEngine
from gridora.app.safety import Allowlist, CircuitBreaker, ProfitGuard
from gridora.app.service import GridService
from gridora.domain.models import GridConfig, GridState, RiskPreset, Side, Spacing, Venue

ALLOW = Allowlist(symbols=frozenset({"CAKE", "USDT"}))


def _loop(exchange, signals=None, breaker=None):
    return LearningLoop(
        exchange, signals or FakeSignals(), MemoryChain(), ALLOW,
        breaker or CircuitBreaker(), ProfitGuard(),
    )


async def test_launch_commits_then_executes():
    ex = FakeExchange({"mid": "2.5", "tick": "0.0001", "step": "0.0001", "min_order_size": "0.001"})
    svc = GridService(_loop(ex), venue=Venue.FAKE)
    res = await svc.launch("CAKE/USDT", RiskPreset.BALANCED, Decimal(100))
    assert res.commit_tx.startswith("0x")
    assert res.orders_placed > 0
    st = await svc.status(res.instance_id)
    assert st["state"] == "RUNNING"
    assert int(st["resting_orders"]) == res.orders_placed


async def test_off_allowlist_market_rejected_before_commit():
    ex = FakeExchange({"mid": "1.0"})
    chain = MemoryChain()
    loop = LearningLoop(ex, FakeSignals(), chain, ALLOW, CircuitBreaker(), ProfitGuard())
    svc = GridService(loop, venue=Venue.FAKE)
    with pytest.raises(ValueError):
        await svc.launch("DOGE/USDT", RiskPreset.SAFE, Decimal(50))
    assert chain._commits == {}  # nothing pinned on-chain for a rejected market


async def test_grid_banks_spread_on_dip_and_bounce():
    ex = FakeExchange({"mid": "100", "tick": "0.01", "step": "0.0001", "min_order_size": "0.001"})
    loop = _loop(ex)
    svc = GridService(loop, venue=Venue.FAKE)
    res = await svc.launch("CAKE/USDT", RiskPreset.BALANCED, Decimal(1000))
    eng = loop.engine(res.instance_id)
    # Dip fills BUYs, bounce fills the paired SELLs one level up -> realized > 0.
    for px in ("97", "98", "99", "100.5", "101"):
        for f in await ex.move_price("CAKE/USDT", Decimal(px)):
            await eng.handle_fill(f)
    assert eng.realized > 0
    assert eng.closes >= 1


async def test_circuit_breaker_trips_and_flattens_on_crash():
    ex = FakeExchange({"mid": "100", "tick": "0.01", "step": "0.0001", "min_order_size": "0.001"})
    # Tight drawdown cap so a one-sided crash trips it deterministically.
    breaker = CircuitBreaker(max_drawdown=Decimal("5"))
    loop = _loop(ex, breaker=breaker)
    svc = GridService(loop, venue=Venue.FAKE)
    # Pre-set the breaker via a margin whose dd cap is dwarfed by the explicit cap.
    res = await svc.launch("CAKE/USDT", RiskPreset.AGGRESSIVE, Decimal(1000))
    eng = loop.engine(res.instance_id)
    # Crash hard: BUYs fill into a growing long bag, unrealized loss blows the cap.
    for px in ("95", "90", "85", "80"):
        for f in await ex.move_price("CAKE/USDT", Decimal(px)):
            await eng.handle_fill(f)
        await eng.maybe_recenter()
    assert breaker.tripped
    assert eng.state is GridState.HALTED
    assert "CAKE/USDT" in ex.flatten_calls  # flattened to stablecoin on the trip


async def test_close_attests_and_writes_memory():
    ex = FakeExchange({"mid": "2.5", "tick": "0.0001", "step": "0.0001", "min_order_size": "0.001"})
    chain = MemoryChain()
    signals = FakeSignals(fear_greed=54, momentum="0.05")
    loop = LearningLoop(ex, signals, chain, ALLOW, CircuitBreaker(), ProfitGuard())
    svc = GridService(loop, venue=Venue.FAKE)
    res = await svc.launch("CAKE/USDT", RiskPreset.BALANCED, Decimal(100))
    out = await svc.close(res.instance_id)
    assert out["attest_ok"] is True
    assert out["attest_tx"].startswith("0x")
    assert res.instance_id in chain._attestations
    # LEARN: the episode is now recallable for its regime.
    recalled = await chain.recall(await signals.snapshot("CAKE/USDT"), k=5)
    assert len(recalled) == 1


async def test_recenter_follows_price_out_of_band():
    ex = FakeExchange({"mid": "100", "tick": "0.01", "step": "0.0001", "min_order_size": "0.001"})
    cfg = GridConfig(
        instance_id="CAKE/USDT-x", market="CAKE/USDT", lower=Decimal("95"), upper=Decimal("105"),
        levels=11, order_size=Decimal("0.1"), spacing=Spacing.ARITHMETIC,
    )
    eng = GridEngine(ex, cfg, breaker=CircuitBreaker())
    await eng.start()
    assert eng.lower == Decimal("95") and eng.upper == Decimal("105")
    await ex.move_price("CAKE/USDT", Decimal("112"))  # leave the band upward
    assert await eng.maybe_recenter() is True
    assert eng.lower > Decimal("95")  # band shifted up to follow price
    assert eng.center == Decimal("112")


async def test_consume_exits_on_halt_without_more_fills():
    # After a guard HALT clears resting orders, no more fills arrive; consume() must
    # notice the halt and return instead of blocking on stream_fills forever.
    ex = FakeExchange({"mid": "100", "tick": "0.01", "step": "0.0001", "min_order_size": "0.001"})
    cfg = GridConfig(instance_id="CAKE/USDT-h", market="CAKE/USDT", lower=Decimal("95"),
                     upper=Decimal("105"), levels=5, order_size=Decimal("0.1"), spacing=Spacing.ARITHMETIC)
    eng = GridEngine(ex, cfg, breaker=CircuitBreaker())
    await eng.start()
    task = asyncio.ensure_future(eng.consume())
    await asyncio.sleep(0)            # let consume start waiting on the fill stream
    eng.pause()                       # HALT with no further fills
    await asyncio.wait_for(task, timeout=1.0)   # returns, doesn't hang


async def test_start_is_inventory_aware_when_seeded():
    # A carried long seeded BEFORE start() must shape the grid: no averaging UP above entry.
    ex = FakeExchange({"mid": "100", "tick": "0.01", "step": "0.0001", "min_order_size": "0.001"})
    cfg = GridConfig(instance_id="CAKE/USDT-s", market="CAKE/USDT", lower=Decimal("90"),
                     upper=Decimal("110"), levels=11, order_size=Decimal("0.1"), spacing=Spacing.ARITHMETIC)
    eng = GridEngine(ex, cfg, breaker=CircuitBreaker())
    eng.seed_position(Decimal("0.5"), Decimal("100"))   # carried long, avg 100
    await eng.start()
    buys_above = [o for o in eng._resting.values() if o.side is Side.BUY and o.price > Decimal("100")]
    assert not buys_above
    assert eng.net_inventory() == Decimal("0.5")        # seeded inventory preserved


async def test_recenter_hysteresis_no_thrash_at_band_edge():
    # An oscillation that just grazes the band edge must NOT re-center (that thrash bled
    # ~-830 bps/episode). Only a move beyond the band + buffer re-centers.
    ex = FakeExchange({"mid": "100", "tick": "0.01", "step": "0.0001", "min_order_size": "0.001"})
    cfg = GridConfig(instance_id="CAKE/USDT-hy", market="CAKE/USDT", lower=Decimal("95"),
                     upper=Decimal("105"), levels=11, order_size=Decimal("0.1"), spacing=Spacing.ARITHMETIC)
    eng = GridEngine(ex, cfg, breaker=CircuitBreaker())
    await eng.start()                                    # band [95,105], width 10, buffer 10% -> [94,106]
    await ex.move_price("CAKE/USDT", Decimal("106"))     # just past the edge, within the buffer
    assert await eng.maybe_recenter() is False           # hysteresis: hold, don't thrash
    await ex.move_price("CAKE/USDT", Decimal("108"))     # clearly beyond the buffer
    assert await eng.maybe_recenter() is True
