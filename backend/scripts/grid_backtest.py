"""Real-data grid backtest — drive the REAL GridEngine over each shortlist token's recent
hourly price path (CoinGecko), per risk preset. Reports return% / maxDD% / fills/day /
win% so we can rank what's actually worth gridding before risking money.

Scale-free: real prices are normalized to multipliers off the first close and replayed at
MID=100 — grid mechanics are scale-invariant, so this isolates the one thing that matters,
the token's actual oscillation. History is cached to .bt_cache/ so re-runs (and any agents)
read disk, never hammering the API.

Run:  python -m scripts.universe_snapshot      # writes .bt_cache/shortlist.json
      python -m scripts.grid_backtest [days]   # default 14
"""
from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

import httpx

from gridora.adapters.exchanges.fake import FakeExchange
from gridora.app.engine import GridEngine
from gridora.app.safety import AccountGuard, CircuitBreaker, ProfitGuard
from gridora.app.service import GridService
from gridora.domain.models import RiskPreset, Venue

MID = Decimal("100")
MARGIN = Decimal("1000")
CACHE = Path(__file__).resolve().parent.parent / ".bt_cache"
PRESETS = [RiskPreset.SAFE, RiskPreset.BALANCED, RiskPreset.AGGRESSIVE]


async def _history(token: dict, days: int) -> list[float]:
    """Hourly USD closes (oldest→newest), cached per symbol. Retries on 429; returns []
    (never raises) so one rate-limited token can't crash the whole run."""
    f = CACHE / f"hist_{token['symbol']}_{days}d.json"
    if f.exists():
        return json.loads(f.read_text())
    url = f"https://api.coingecko.com/api/v3/coins/{token['id']}/market_chart"
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(timeout=25) as h:
                r = await h.get(url, params={"vs_currency": "usd", "days": days})
            if r.status_code == 429:
                await asyncio.sleep(8 * (attempt + 1))
                continue
            r.raise_for_status()
            prices = [p[1] for p in (r.json().get("prices") or []) if p[1] and p[1] > 0]
            f.write_text(json.dumps(prices))
            return prices
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                print(f"  ! {token['symbol']} history failed: {e}")
            await asyncio.sleep(5)
    return []


async def fetch_all(tokens: list[dict], days: int) -> None:
    missing = [t for t in tokens if not (CACHE / f"hist_{t['symbol']}_{days}d.json").exists()]
    for i, t in enumerate(missing):
        if i:
            await asyncio.sleep(6.0)   # free-tier throttle (429s on bursts)
        n = len(await _history(t, days))
        if n:
            print(f"  cached {t['symbol']:<8} {n} pts")


async def backtest(token: dict, preset: RiskPreset, days: int):
    prices = await _history(token, days)
    if len(prices) < 24:
        return None
    p0 = prices[0]
    path = [MID * Decimal(str(p / p0)) for p in prices]

    ex = FakeExchange({"mid": str(MID), "tick": "0.001", "step": "0.0001",
                       "min_order_size": "0.0001", "equity": str(MARGIN)})
    cfg = GridService.config_for_preset(f"bt-{token['symbol']}", token["market"], preset,
                                        MARGIN, MID, Venue.FAKE,
                                        range_24h_pct=token.get("range_24h_pct", 0.0))
    cfg.bias = token.get("bias", 0)   # regime lean (the live system applies this; neutral was unfair)
    eng = GridEngine(ex, cfg, breaker=CircuitBreaker(max_drawdown_frac=Decimal("0.12")),
                     profit_guard=ProfitGuard(trail_frac=Decimal("0.4"), trail_arm=MARGIN * Decimal("0.05")),
                     account_guard=AccountGuard())
    await eng.start()
    for price in path:
        for fl in await ex.move_price(token["market"], price):
            await eng.handle_fill(fl)
        await eng.maybe_recenter()
    o = eng.outcome()
    hrs = len(prices)
    return {
        "return_pct": o.pnl_bps / 100, "maxdd_pct": o.max_drawdown_bps / 100,
        "trades": o.trades, "fills": eng.fill_count, "fills_per_day": eng.fill_count / (hrs / 24),
        "winrate": eng.winrate * 100, "exit": eng.exit_kind or "—",
        "endmove_pct": float((path[-1] / MID - 1) * 100),
    }


async def main() -> None:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 14
    tokens = json.loads((CACHE / "shortlist.json").read_text())
    print(f"\nFetching {days}d hourly history for {len(tokens)} tokens (cached)…")
    await fetch_all(tokens, days)

    print(f"\n=== REAL-DATA GRID BACKTEST — {days}d hourly, neutral grid, ${MARGIN} margin, "
          f"breaker 12% ===")
    print(f"{'token':<8}{'preset':<6}{'7dΔ%':>7}{'move%':>7}{'ret%':>7}{'maxDD%':>8}"
          f"{'fills':>7}{'f/day':>7}{'win%':>6}  exit")
    summary = []
    for t in tokens:
        best = None
        for preset in PRESETS:
            r = await backtest(t, preset, days)
            if r is None:
                print(f"{t['symbol']:<8}{preset.value:<6}  (insufficient history)")
                continue
            print(f"{t['symbol']:<8}{preset.value:<6}{t['pct_7d']:>7.1f}{r['endmove_pct']:>7.1f}"
                  f"{r['return_pct']:>7.2f}{r['maxdd_pct']:>8.2f}{r['fills']:>7}"
                  f"{r['fills_per_day']:>7.1f}{r['winrate']:>5.0f}%  {r['exit']}")
            if best is None or r["return_pct"] > best[1]["return_pct"]:
                best = (preset, r)
        if best:
            summary.append((t["symbol"], best[0].value, best[1]))
        print()

    # rank by risk-adjusted: best-preset return, must clear DQ-style gates AND beat holding
    print("=== RANKED (best preset per token; gate = maxDD<30%, fills/day≥1) ===")
    print(f"{'token':<8}{'preset':<6}{'grid%':>7}{'hold%':>7}{'edge%':>7}{'maxDD%':>8}{'f/day':>7}  verdict")
    for sym, preset, r in sorted(summary, key=lambda s: s[2]["return_pct"], reverse=True):
        hold = r["endmove_pct"]
        edge = r["return_pct"] - hold
        ok = r["maxdd_pct"] < 30 and r["fills_per_day"] >= 1 and r["return_pct"] > 0
        verdict = ("TRADE" if ok and edge > 0 else "TRADE (sub-hold)" if ok
                   else "thin fills" if r["fills_per_day"] < 1 else "neg/risky")
        print(f"{sym:<8}{preset:<6}{r['return_pct']:>7.2f}{hold:>7.1f}{edge:>7.2f}"
              f"{r['maxdd_pct']:>8.2f}{r['fills_per_day']:>7.1f}  {verdict}")


if __name__ == "__main__":
    asyncio.run(main())
