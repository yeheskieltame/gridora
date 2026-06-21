"""Pure PnL-metric tests — offline."""
from decimal import Decimal

from gridora.domain.pnl import bps, max_drawdown, risk_adjusted, winrate


def test_winrate():
    assert winrate([]) == 0.0
    assert winrate([Decimal(1), Decimal(-1), Decimal(2), Decimal(-3)]) == 0.5
    assert winrate([Decimal(1), Decimal(2)]) == 1.0


def test_max_drawdown():
    # peak 120, trough 90 -> 25% drawdown
    curve = [Decimal(100), Decimal(120), Decimal(90), Decimal(110)]
    assert abs(max_drawdown(curve) - 0.25) < 1e-9
    assert max_drawdown([Decimal(100), Decimal(101)]) == 0.0


def test_bps():
    assert bps(Decimal(5), Decimal(100)) == 500       # 5/100 = 5% = 500bps
    assert bps(Decimal(-12), Decimal(100)) == -1200
    assert bps(Decimal(1), Decimal(0)) == 0


def test_risk_adjusted():
    assert risk_adjusted(Decimal(100), 0.5) == 200.0   # pnl per unit drawdown
    # zero drawdown ranks ABOVE any positive drawdown (monotonic across mdd=0), not raw pnl
    assert risk_adjusted(Decimal(100), 0.0) > risk_adjusted(Decimal(100), 0.001)
