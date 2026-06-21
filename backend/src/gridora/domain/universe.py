"""Universe selection — decide WHICH allowlist tokens to grid, from CMC liquidity +
momentum. Pure: feed it TokenMetric rows, get back ranked MarketPicks. No I/O.

Why this exists (see the token research): on the 149-token list the edge is *what* you
trade, not grid mechanics. Most of the list is too illiquid to grid (no fills, ruinous
slippage), the week's regime is broadly bearish, and the few real uptrends are scarce.
So:
  - Tradeable only — 24h USD volume >= MIN_VOL (drop the illiquid tail + paper pumps).
  - Don't grid a falling knife — exclude names below DOWN_FLOOR over 7d.
  - Rank by relative strength + a liquidity bonus; lean bias from 7d momentum.
  - Diversify — cap to n picks, weight by score, never all-in one name.
"""
from __future__ import annotations

from decimal import Decimal

from .models import MarketPick, TokenMetric

MIN_VOL_USD = Decimal(20_000_000)   # below this a grid can't fill without bleeding to slippage
DOWN_FLOOR = -8.0                   # exclude names down more than this over 7d (don't grid a downtrend)
UP_LEAN = 5.0                       # 7d momentum above this -> long-lean grid
N_DEFAULT = 4                       # diversify across this many uncorrelated liquid names
MIN_RANGE_PCT = 1.5                 # below this daily range a token is too flat to feed a grid (skip if range unknown)
RANGE_CAP = 12.0                    # cap the volatility credit (12%+/day is ample grid fuel; beyond is chaos)
RANGE_W = 1.5                       # weight on volatility — the primary driver of fills for a maker grid

# USD-pegged stables eligible as the quote leg (subset of the allowlist; --price is a USD target).
USD_QUOTES = ("USDT", "USDC", "FDUSD", "DAI", "TUSD", "USDD", "USD1")


def bias_for(pct_7d: float) -> int:
    """Grid lean from 7d momentum: +1 long on strength, 0 neutral (range), -1 only on
    clear weakness. (A spot maker grid can't truly short, so -1 mostly means 'sell-heavy
    / shed inventory'; the selector usually just declines to pick weak names.)"""
    if pct_7d >= UP_LEAN:
        return 1
    if pct_7d <= -UP_LEAN:
        return -1
    return 0


def score(m: TokenMetric) -> float:
    """Higher = better to grid. Volatility (24h range) is the dominant term — a grid earns
    from price crossing levels, so a flat token is worthless however liquid/strong it is
    (the CAKE lesson). Plus relative strength (clipped, don't grid a faller) + a liquidity
    bonus (+1 per $50M/24h vol, capped). range=0 (unknown, e.g. CMC) -> vol term 0, falls
    back to rs+liq."""
    vol = min(RANGE_CAP, m.range_24h_pct) * RANGE_W
    rs = max(-10.0, min(15.0, m.pct_7d))
    liq = min(10.0, float(m.vol_24h_usd) / 50_000_000)
    return vol + rs + liq


def select_markets(
    metrics: dict[str, TokenMetric],
    allowlist: frozenset[str],
    quote: str = "USDT",
    n: int = N_DEFAULT,
    min_vol_usd: Decimal = MIN_VOL_USD,
    down_floor: float = DOWN_FLOOR,
    min_range_pct: float = MIN_RANGE_PCT,
) -> list[MarketPick]:
    """Rank eligible tokens and return up to `n` MarketPicks (margin weights sum to ~1).
    Eligible = on the allowlist, not the quote, quote is a USD stable on the allowlist,
    24h volume >= min_vol_usd, 7d change >= down_floor, and (when range is known) the daily
    range >= min_range_pct (too-flat tokens can't feed a grid)."""
    if quote not in allowlist or quote not in USD_QUOTES:
        raise ValueError(f"quote {quote!r} must be a USD-pegged stable on the allowlist")
    cands = [
        m for sym, m in metrics.items()
        if sym in allowlist and sym != quote
        and m.vol_24h_usd >= min_vol_usd and m.pct_7d >= down_floor
        and (m.range_24h_pct <= 0 or m.range_24h_pct >= min_range_pct)
    ]
    cands.sort(key=score, reverse=True)
    top = cands[:max(0, n)]
    if not top:
        return []
    # Rank-weight (linear: k, k-1, …, 1): the strongest pick gets the most margin but every
    # pick keeps a meaningful slice — diversification matters more than conviction near the
    # 30% DQ, and the score already did the quality filtering via selection.
    k = len(top)
    weights = [float(k - i) for i in range(k)]
    total = sum(weights)
    picks = []
    for m, w in zip(top, weights):
        picks.append(MarketPick(
            market=f"{m.symbol}/{quote}", bias=bias_for(m.pct_7d),
            weight=(Decimal(str(w / total))).quantize(Decimal("0.0001")),
            score=round(score(m), 2), vol_24h_usd=m.vol_24h_usd, pct_7d=m.pct_7d,
            range_24h_pct=m.range_24h_pct))
    return picks


def _demo() -> None:
    """Self-check: the selector must drop the illiquid tail + downtrends + DEAD-FLAT names,
    rank by volatility-first score, lean bias by momentum, and weight to ~1."""
    allow = frozenset({"USDT", "XPL", "UNI", "AVAX", "KOGE", "ETH", "FET", "GUA", "FLAT"})
    M = lambda s, v, p7, r=8.0: TokenMetric(s, Decimal(1), Decimal(v), 0.0, p7, r)  # noqa: E731
    metrics = {
        "XPL": M("XPL", 105_000_000, 6.2, 15.0),    # liquid + strong + VOLATILE -> top pick, long
        "UNI": M("UNI", 186_000_000, 17.8, 3.0),    # liquid + strongest 7d but quiet -> pick, long
        "ETH": M("ETH", 6_900_000_000, 3.1, 5.0),   # liquid + ranging -> pick, neutral
        "FLAT": M("FLAT", 500_000_000, 12.0, 1.0),  # liquid + strong but DEAD FLAT (1% range) -> EXCLUDED
        "AVAX": M("AVAX", 456_000_000, -8.9, 8.0),  # liquid but falling past floor -> EXCLUDED
        "FET": M("FET", 60_000_000, -9.1, 8.0),     # falling past floor -> EXCLUDED
        "KOGE": M("KOGE", 41_000, 2.0, 8.0),        # pump but $41k vol  -> EXCLUDED (illiquid)
        "GUA": M("GUA", 2_800_000, 40.0, 30.0),     # big move but $2.8M -> EXCLUDED (illiquid)
        "USDT": M("USDT", 9e10, 0.0, 0.5),          # the quote itself   -> EXCLUDED
    }
    picks = select_markets(metrics, allow, "USDT", n=4)
    got = {p.market: p for p in picks}
    assert set(got) == {"XPL/USDT", "UNI/USDT", "ETH/USDT"}, got  # FLAT dropped: too flat to grid
    assert got["UNI/USDT"].bias == 1 and got["ETH/USDT"].bias == 0
    assert abs(float(sum(p.weight for p in picks)) - 1.0) < 0.01
    # volatility flips the order: XPL (15% range) outranks UNI despite UNI's higher 7d %
    # (old liquidity+strength-only score would have put UNI first).
    assert picks[0].market == "XPL/USDT", [(p.market, p.score) for p in picks]
    assert got["XPL/USDT"].weight >= got["UNI/USDT"].weight
    assert got["XPL/USDT"].range_24h_pct == 15.0
    # quote must be a USD stable on the allowlist
    try:
        select_markets(metrics, allow, "FET")
        raise AssertionError("expected ValueError for non-stable quote")
    except ValueError:
        pass
    print("universe._demo OK:", [(p.market, p.bias, str(p.weight), f"{p.range_24h_pct}%") for p in picks])


if __name__ == "__main__":
    _demo()
