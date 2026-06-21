"""X402Payments (PaymentPort) — pay-per-call CMC data via `twak x402 request`.

TWAK fetches the x402-gated URL, signs + settles the micropayment, returns the JSON;
key never leaves TWAK. `max_payment` caps per-call spend; responses cached briefly (TTL).
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlencode

from ..exchanges.bsc_twak.twak_client import TwakClient


class X402Payments:
    def __init__(self, twak: TwakClient, max_payment: int = 100_000, cache_ttl_s: float = 5.0,
                 prefer_network: str = "bsc") -> None:
        self.twak = twak
        self.max_payment = max_payment        # atomic units of the x402 settlement asset
        self.cache_ttl_s = cache_ttl_s
        self.prefer_network = prefer_network
        self._cache: dict[str, tuple[float, dict]] = {}

    async def pay_and_fetch(self, url: str, params: dict) -> dict[str, Any]:
        """x402 GET: serve from the in-cycle cache, else have TWAK pay-and-fetch."""
        full = f"{url}?{urlencode(params)}" if params else url
        hit = self._cache.get(full)
        if hit is not None:
            if (time.time() - hit[0]) < self.cache_ttl_s:
                return hit[1]
            del self._cache[full]   # stale — evict so distinct (url,params) can't accumulate unbounded
        data = await self.twak.x402_request(full, self.max_payment, prefer_network=self.prefer_network)
        if not isinstance(data, dict):
            data = {"data": data}
        self._cache[full] = (time.time(), data)
        return data

    def clear_cycle(self) -> None:
        """Drop the cache (e.g. to force a fresh read at the start of a decision cycle)."""
        self._cache.clear()
