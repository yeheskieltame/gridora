"""CmcSignals (SignalPort) — RegimeFingerprint from CoinMarketCap (F&G + 24h momentum).

Data via x402-through-TWAK (primary; scores native-x402) with a keyed pro-api fallback.
Raw inputs map to (label, bias) by the same pure `classify()` the offline FakeSignals uses.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Sequence

import httpx

from ...domain.models import RegimeFingerprint, TokenMetric
from ...domain.ports import PaymentPort
from ...domain.regime import classify


def momentum_from_pct(pct_24h: float) -> Decimal:
    """Normalize a 24h % change to the ~[-1, 1] momentum scale `classify` expects
    (a ±10%/24h move reads as a full-strength trend)."""
    m = max(-1.0, min(1.0, pct_24h / 10.0))
    return Decimal(str(round(m, 4)))


class CmcSignals:
    """x402-via-TWAK is the PRIMARY data path (scores the native-x402 criterion). If a
    call fails (e.g. TWAK's testnet x402 node, or an unpaid endpoint) AND a CMC Pro key
    is configured, fall back to a direct keyed pro-api GET so the regime read still
    works. The fingerprint is built by the SAME `classify()` the offline FakeSignals
    uses, so SENSE behaves identically on real or fake data."""

    def __init__(self, pay: PaymentPort, base_url: str = "https://pro-api.coinmarketcap.com",
                 api_key: str = "") -> None:
        self.pay = pay  # x402-via-TWAK payment port
        self.base = base_url.rstrip("/")
        self.api_key = api_key

    async def _fetch(self, path: str, params: dict) -> dict:
        url = f"{self.base}{path}"
        try:
            return await self.pay.pay_and_fetch(url, params)   # x402 via TWAK (primary)
        except Exception:  # noqa: BLE001 — fall back to a keyed pro-api call if we can
            if not self.api_key:
                raise
            async with httpx.AsyncClient(timeout=15) as http:
                r = await http.get(url, params=params, headers={"X-CMC_PRO_API_KEY": self.api_key})
                r.raise_for_status()
                return r.json()

    async def snapshot(self, market: str) -> RegimeFingerprint:
        base, _, _ = market.partition("/")

        fear_greed = 50  # neutral default
        fg_ok = False
        try:
            fear_greed = _fng_value(await self._fetch("/v3/fear-and-greed/latest", {}))
            fg_ok = True
        except Exception as e:  # noqa: BLE001 — an F&G outage defaults to neutral, never crashes SENSE
            logging.warning("CMC fear-and-greed fetch failed: %s", e)

        pct_24h = 0.0
        q_ok = False
        try:
            quotes = await self._fetch("/v2/cryptocurrency/quotes/latest", {"symbol": base})
            pct_24h = _pct_change_24h(quotes, base)
            q_ok = True
        except Exception as e:  # noqa: BLE001 — a missing quote must not sink the regime read
            logging.warning("CMC quotes fetch failed for %s: %s", base, e)

        momentum = momentum_from_pct(pct_24h)
        funding_bps = Decimal(0)  # CMC core does not expose funding; left at 0 (RANGING-safe)
        social_heat = Decimal(0)

        label, bias = classify(fear_greed, funding_bps, momentum)
        return RegimeFingerprint(
            market=market, fear_greed=fear_greed, funding_bps=funding_bps,
            momentum=momentum, social_heat=social_heat, label=label, bias=bias,
            stale=not (fg_ok or q_ok),  # BOTH feeds down -> this neutral is synthesized, don't trade on it
        )

    async def market_metrics(self, symbols: Sequence[str]) -> dict[str, TokenMetric]:
        """Bulk CMC quotes/latest (24h USD volume + % changes) for universe selection.
        Chunks the symbol list; a failed chunk is skipped (its symbols are just omitted)."""
        out: dict[str, TokenMetric] = {}
        syms = [s for s in symbols if s.isascii()]   # CMC symbol param is ASCII; skip exotic glyphs
        for i in range(0, len(syms), 100):
            chunk = ",".join(syms[i:i + 100])
            try:
                body = await self._fetch("/v2/cryptocurrency/quotes/latest", {"symbol": chunk})
            except Exception as e:  # noqa: BLE001 — a bad chunk must not sink selection
                logging.warning("CMC quotes/latest failed for a chunk: %s", e)
                continue
            for sym, entry in (body.get("data") or {}).items():
                if isinstance(entry, list):
                    entry = entry[0] if entry else {}
                q = ((entry or {}).get("quote") or {}).get("USD") or {}
                out[sym] = TokenMetric(
                    symbol=sym,
                    price_usd=Decimal(str(q.get("price") or 0)),
                    vol_24h_usd=Decimal(str(q.get("volume_24h") or 0)),
                    pct_24h=float(q.get("percent_change_24h") or 0),
                    pct_7d=float(q.get("percent_change_7d") or 0))
        return out


def _fng_value(body: dict) -> int:
    """Pull the Fear & Greed score from CMC's response (shape-tolerant)."""
    data = body.get("data", body)
    if isinstance(data, list) and data:
        data = data[0]
    if isinstance(data, dict):
        for k in ("value", "score", "fearGreedIndex"):
            if k in data:
                try:
                    return int(float(data[k]))
                except (TypeError, ValueError):
                    pass
    return 50  # neutral default if the field moves


def _pct_change_24h(body: dict, symbol: str) -> float:
    """Pull percent_change_24h for `symbol` from a CMC quotes/latest response."""
    data = body.get("data", {})
    entry = data.get(symbol)
    if isinstance(entry, list) and entry:
        entry = entry[0]
    if isinstance(entry, dict):
        quote = entry.get("quote", {}).get("USD", {})
        pct = quote.get("percent_change_24h")
        if pct is not None:
            return float(pct)
    return 0.0
