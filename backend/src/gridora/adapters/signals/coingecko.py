"""CoinGeckoSignals — FREE real market data for PAPER trading (no API key, no x402
micropayments, no money). Provides:
  - price(market)            : live USD price of the base token (drives PaperExchange)
  - snapshot(market)         : regime (momentum from 7d %, F&G from alternative.me)
  - market_metrics(symbols)  : liquidity + 7d momentum for universe selection

CoinGecko has no Fear & Greed, so F&G comes from alternative.me's free crypto F&G index.
A per-symbol cache (TTL) keeps calls well under CoinGecko's free rate limit even when the
engine polls price frequently.
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Sequence

import httpx

from ...domain.models import RegimeFingerprint, TokenMetric
from ...domain.regime import classify
from .cmc import momentum_from_pct

_CG = "https://api.coingecko.com/api/v3/coins/markets"
_FNG = "https://api.alternative.me/fng/?limit=1"


class CoinGeckoSignals:
    def __init__(self, cache_ttl_s: float = 60.0, timeout: float = 15.0) -> None:
        self.cache_ttl_s = cache_ttl_s
        self.timeout = timeout
        self._cache: dict[str, tuple[float, TokenMetric]] = {}   # UPPER sym -> (ts, metric)
        self._fng = 50
        self._fng_at = 0.0

    async def _get(self, url: str, params: dict | None = None):
        async with httpx.AsyncClient(timeout=self.timeout) as h:
            r = await h.get(url, params=params)
            r.raise_for_status()
            return r.json()

    async def _fetch(self, symbols: Sequence[str]) -> None:
        """Batch-fetch the given symbols from CoinGecko and refresh the cache (keep the
        highest-volume match per symbol). CoinGecko 400s on a long `symbols=` list, so
        chunk it (≤30/call → ~5 calls for the full allowlist, well under the free limit)."""
        uniq = sorted({s.lower() for s in symbols if s and s.isascii()})
        if not uniq:
            return
        rows: list = []
        for i in range(0, len(uniq), 30):  # ponytail: 30/chunk dodges the URL/symbol-count 400
            if i:
                await asyncio.sleep(2.5)   # throttle: free tier 429s on bursts
            chunk = ",".join(uniq[i:i + 30])
            try:
                part = await self._get(_CG, {"vs_currency": "usd", "symbols": chunk,
                                             "price_change_percentage": "24h,7d", "per_page": 250})
                rows.extend(part or [])
            except Exception as e:  # noqa: BLE001 — keep last-good cache on a hiccup
                print(f"  ! coingecko fetch failed ({chunk[:40]}…): {e}")
        best: dict[str, TokenMetric] = {}
        for row in rows or []:
            sym = str(row.get("symbol", "")).upper()
            if not sym:
                continue
            price = float(row.get("current_price") or 0)
            hi, lo = float(row.get("high_24h") or 0), float(row.get("low_24h") or 0)
            rng = (hi - lo) / price * 100 if price > 0 and hi >= lo > 0 else 0.0  # 24h high-low range %
            m = TokenMetric(
                symbol=sym, price_usd=Decimal(str(row.get("current_price") or 0)),
                vol_24h_usd=Decimal(str(row.get("total_volume") or 0)),
                pct_24h=float(row.get("price_change_percentage_24h") or 0),
                pct_7d=float(row.get("price_change_percentage_7d_in_currency") or 0),
                range_24h_pct=rng)
            if sym not in best or m.vol_24h_usd > best[sym].vol_24h_usd:
                best[sym] = m
        now = time.time()
        for sym, m in best.items():
            self._cache[sym] = (now, m)

    async def _metrics(self, symbols: Sequence[str]) -> dict[str, TokenMetric]:
        now = time.time()
        stale = [s for s in symbols
                 if s.upper() not in self._cache or (now - self._cache[s.upper()][0]) > self.cache_ttl_s]
        if stale:
            await self._fetch(stale)
        return {s.upper(): self._cache[s.upper()][1] for s in symbols if s.upper() in self._cache}

    async def market_metrics(self, symbols: Sequence[str]) -> dict[str, TokenMetric]:
        return await self._metrics(symbols)

    async def price(self, market: str) -> Decimal:
        base = market.partition("/")[0].upper()
        m = (await self._metrics([base])).get(base)
        return m.price_usd if m else Decimal(0)

    async def _fear_greed(self) -> int:
        if (time.time() - self._fng_at) < 300:
            return self._fng
        try:
            body = await self._get(_FNG)
            self._fng = int(body["data"][0]["value"])
            self._fng_at = time.time()
        except Exception:  # noqa: BLE001 — F&G outage -> last-good / neutral
            pass
        return self._fng

    async def snapshot(self, market: str) -> RegimeFingerprint:
        base = market.partition("/")[0].upper()
        m = (await self._metrics([base])).get(base)
        fg = await self._fear_greed()
        momentum = momentum_from_pct(m.pct_7d) if m else Decimal(0)
        label, bias = classify(fg, Decimal(0), momentum)
        return RegimeFingerprint(market=market, fear_greed=fg, funding_bps=Decimal(0),
                                 momentum=momentum, social_heat=Decimal(0), label=label,
                                 bias=bias, stale=m is None)   # no price data -> stale, agent holds
