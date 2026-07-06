"""Live universe snapshot — pull REAL CoinGecko metrics for the eligible 149 and rank
them for grid-suitability (the same scoring the agent uses to pick what to trade).

Uses 2 market-cap pages (top 500) instead of a long symbols= filter — 2 calls, no 400/429
storm. Writes the ranked shortlist + CoinGecko ids to .bt_cache/shortlist.json so the
backtest step can pull price history without re-deriving anything.

Run:  python -m scripts.universe_snapshot [N]   (from backend/, venv active)
No keys, no money — the same free CoinGecko read paper mode uses.
"""
from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

import httpx

from gridora.adapters.signals.allowlist import load_allowlist
from gridora.domain import universe as U
from gridora.domain.models import TokenMetric

_MK = "https://api.coingecko.com/api/v3/coins/markets"
CACHE = Path(__file__).resolve().parent.parent / ".bt_cache"


async def _mcap_rows(pages: int = 2) -> list[dict]:
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=20) as h:
        for p in range(1, pages + 1):
            if p > 1:
                await asyncio.sleep(3.0)
            r = await h.get(_MK, params={"vs_currency": "usd", "order": "market_cap_desc",
                                         "per_page": 250, "page": p,
                                         "price_change_percentage": "24h,7d"})
            r.raise_for_status()
            rows.extend(r.json() or [])
    return rows


def _metric(row: dict) -> TokenMetric:
    price = float(row.get("current_price") or 0)
    hi, lo = float(row.get("high_24h") or 0), float(row.get("low_24h") or 0)
    rng = (hi - lo) / price * 100 if price > 0 and hi >= lo > 0 else 0.0
    return TokenMetric(
        symbol=str(row.get("symbol", "")).upper(),
        price_usd=Decimal(str(row.get("current_price") or 0)),
        vol_24h_usd=Decimal(str(row.get("total_volume") or 0)),
        pct_24h=float(row.get("price_change_percentage_24h") or 0),
        pct_7d=float(row.get("price_change_percentage_7d_in_currency") or 0),
        range_24h_pct=rng)


def _status(m, quote_set) -> str:
    if m.symbol in quote_set:
        return "quote"
    if m.vol_24h_usd < U.MIN_VOL_USD:
        return f"illiquid (${float(m.vol_24h_usd)/1e6:.1f}M)"
    if m.pct_7d < U.DOWN_FLOOR:
        return f"downtrend ({m.pct_7d:.1f}%)"
    if 0 < m.range_24h_pct < U.MIN_RANGE_PCT:
        return f"too-flat ({m.range_24h_pct:.1f}%)"
    return "OK"


async def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    allow = load_allowlist()
    quote_set = set(U.USD_QUOTES)
    rows = await _mcap_rows()

    # keep highest-volume row per allowlisted symbol; remember the coingecko id for history
    metrics: dict[str, TokenMetric] = {}
    ids: dict[str, str] = {}
    for row in rows:
        sym = str(row.get("symbol", "")).upper()
        if sym not in allow:
            continue
        m = _metric(row)
        if sym not in metrics or m.vol_24h_usd > metrics[sym].vol_24h_usd:
            metrics[sym] = m
            ids[sym] = row.get("id", "")

    have = [m for m in metrics.values() if m.symbol not in quote_set]
    print(f"\nMatched {len(metrics)}/{len(allow)} eligible symbols in CoinGecko top-500.\n")
    print(f"{'sym':<10}{'price':>12}{'vol$M':>9}{'7d%':>8}{'24h%':>8}{'range%':>8}{'score':>8}  status")
    for m in sorted(have, key=U.score, reverse=True):
        print(f"{m.symbol:<10}{float(m.price_usd):>12.6g}{float(m.vol_24h_usd)/1e6:>9.1f}"
              f"{m.pct_7d:>8.1f}{m.pct_24h:>8.1f}{m.range_24h_pct:>8.1f}{U.score(m):>8.1f}  "
              f"{_status(m, quote_set)}")

    picks = U.select_markets(metrics, allow, "USDT", n=n)
    print(f"\n=== TOP {len(picks)} GRID PICKS (selector, USDT quote) ===")
    print(f"{'market':<14}{'bias':>5}{'weight':>8}{'7d%':>8}{'vol$M':>9}{'range%':>8}{'score':>8}")
    for p in picks:
        print(f"{p.market:<14}{p.bias:>5}{float(p.weight):>8.3f}{p.pct_7d:>8.1f}"
              f"{float(p.vol_24h_usd)/1e6:>9.1f}{p.range_24h_pct:>8.1f}{p.score:>8.1f}")

    CACHE.mkdir(exist_ok=True)
    shortlist = [{"symbol": p.market.split("/")[0], "market": p.market, "id": ids.get(p.market.split("/")[0], ""),
                  "bias": p.bias, "weight": float(p.weight), "range_24h_pct": p.range_24h_pct,
                  "pct_7d": p.pct_7d, "score": p.score} for p in picks]
    (CACHE / "shortlist.json").write_text(json.dumps(shortlist, indent=2))
    print(f"\nwrote {CACHE/'shortlist.json'} — SHORTLIST:",
          " ".join(s["symbol"] for s in shortlist))


if __name__ == "__main__":
    asyncio.run(main())
