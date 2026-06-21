"""Pure performance metrics.

The optimization target is *risk-adjusted*, not raw PnL (Gridora optimizes for not
blowing past the 30% drawdown DQ, not for the highest number). `recall` ranks past
episodes by the same score. PORTED FROM perps-agent domain/pnl.py.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal


def winrate(pnls: Sequence[Decimal]) -> float:
    if not pnls:
        return 0.0
    wins = sum(1 for p in pnls if p > 0)
    return wins / len(pnls)


def max_drawdown(equity_curve: Sequence[Decimal]) -> float:
    """Worst peak-to-trough fractional drop over the equity curve (0..1)."""
    peak: Decimal | None = None
    mdd = 0.0
    for e in equity_curve:
        peak = e if peak is None else max(peak, e)
        if peak and peak > 0:
            mdd = max(mdd, float((peak - e) / peak))
    return mdd


def bps(part: Decimal, whole: Decimal) -> int:
    """`part` as basis points of `whole` (1% = 100 bps). 0 when whole is 0."""
    if whole == 0:
        return 0
    return int((part / whole) * Decimal(10_000))


def risk_adjusted(realized_pnl: Decimal, mdd: float) -> float:
    """PnL-per-drawdown: a simple, explainable risk-adjusted score. Divide by a tiny
    floor so a zero-drawdown episode ranks ABOVE a tiny-drawdown one (monotonic across
    mdd=0), instead of dropping back to raw pnl and ranking below it.

    TODO: upgrade to Sortino over the per-episode return series.
    """
    return float(realized_pnl) / max(mdd, 1e-9)
