"""Multi-market portfolio tests — universe selection + concurrent grids, offline."""
from decimal import Decimal

from gridora.adapters.chain.memory_chain import MemoryChain
from gridora.adapters.exchanges.fake import FakeExchange
from gridora.adapters.signals.fake import FakeSignals
from gridora.app.portfolio import Portfolio
from gridora.app.safety import Allowlist
from gridora.domain.models import Venue

ALLOW = Allowlist(symbols=frozenset(
    {"USDT", "ETH", "CAKE", "XRP", "LINK", "DOGE", "TRX", "AVAX", "UNI", "AAVE", "ZEC", "LTC"}))


def _exfac(market):
    return FakeExchange({"mid": "100", "tick": "0.01", "step": "0.0001",
                         "min_order_size": "0.001", "equity": "1000"})


async def test_portfolio_selects_liquid_and_launches_n_grids():
    pf = Portfolio(_exfac, FakeSignals(), MemoryChain(), ALLOW, Decimal(1000),
                   venue=Venue.FAKE, quote="USDT", n_markets=3, min_vol_usd=Decimal(20_000_000))
    picks = await pf.launch()
    assert 1 <= len(picks) <= 3
    assert len(pf.engines) == len(picks)
    # margin weights sum to ~1 (full deployment, diversified)
    assert abs(float(sum(p.weight for p in picks)) - 1.0) < 0.02
    for p in picks:
        base, _, quote = p.market.partition("/")
        assert quote == "USDT" and base in ALLOW.symbols and base != "USDT"  # eligible pair
        assert p.bias in (-1, 0, 1)
    assert all(len(e._resting) > 0 for e in pf.engines.values())  # every grid actually placed orders
    outs = await pf.close_all()
    assert len(outs) == len(picks) and not pf.engines


async def test_portfolio_refuses_when_nothing_liquid():
    # min_vol absurdly high -> no eligible markets -> refuse to launch (don't trade blind)
    pf = Portfolio(_exfac, FakeSignals(), MemoryChain(), ALLOW, Decimal(1000),
                   venue=Venue.FAKE, quote="USDT", n_markets=3, min_vol_usd=Decimal(10) ** 18)
    try:
        await pf.launch()
        assert False, "expected refusal"
    except RuntimeError as e:
        assert "no liquid" in str(e)
