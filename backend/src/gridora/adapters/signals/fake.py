"""In-memory SignalPort for offline dry-runs and tests.

Returns a deterministic RegimeFingerprint (configurable F&G / funding / momentum)
run through the same pure `classify()` the live CMC adapter uses — so the regime
label + grid bias are produced by identical logic whether the data is real or fake.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Sequence

from ...domain.models import RegimeFingerprint, TokenMetric
from ...domain.regime import classify


class FakeSignals:
    def __init__(self, fear_greed: int = 50, funding_bps: Decimal | str | float = 0,
                 momentum: Decimal | str | float = 0, social_heat: Decimal | str | float = 0) -> None:
        self.fear_greed = int(fear_greed)
        self.funding_bps = Decimal(str(funding_bps))
        self.momentum = Decimal(str(momentum))
        self.social_heat = Decimal(str(social_heat))

    async def snapshot(self, market: str) -> RegimeFingerprint:
        label, bias = classify(self.fear_greed, self.funding_bps, self.momentum)
        return RegimeFingerprint(
            market=market,
            fear_greed=self.fear_greed,
            funding_bps=self.funding_bps,
            momentum=self.momentum,
            social_heat=self.social_heat,
            label=label,
            bias=bias,
        )

    async def market_metrics(self, symbols: Sequence[str]) -> dict[str, TokenMetric]:
        """Deterministic synthetic metrics (stable per symbol) so the universe selector +
        dry/backtest runs have a varied liquidity/momentum spread to rank."""
        out: dict[str, TokenMetric] = {}
        for s in symbols:
            h = int(hashlib.sha256(s.encode()).hexdigest(), 16)
            vol = Decimal((h % 600) + 1) * Decimal(1_000_000)        # $1M..$600M
            pct7 = round(((h >> 12) % 5000) / 100.0 - 25.0, 2)       # -25%..+25%
            rng = round(2.0 + ((h >> 24) % 1400) / 100.0, 2)         # 2%..16% daily range
            out[s] = TokenMetric(symbol=s, price_usd=Decimal("1"), vol_24h_usd=vol,
                                 pct_24h=0.0, pct_7d=pct7, range_24h_pct=rng)
        return out
