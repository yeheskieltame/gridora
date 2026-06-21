"""Pure grid logic. No I/O. Buy dips, sell rips inside a band.

PORTED NEARLY VERBATIM FROM:
   perps-agent/backend/src/perpsagent/domain/grid.py

Level math + order construction + fill pairing live here so the engine in `app/`
stays a thin async shell over the `ExchangePort`. Always quantize to the market
tick before placing (round down for BUYs, up for SELLs, so a quantized price never
crosses to the wrong side of its level).

Gridora keeps this math identical to perps-agent. The ONLY change is downstream:
orders are handed to the `bsc_twak` ExchangePort, which signs them through TWAK and
sends them to BSC. The grid math itself is venue-agnostic.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

from .models import GridConfig, Order, Side, Spacing


def arithmetic_levels(lower: Decimal, upper: Decimal, n: int) -> list[Decimal]:
    if n < 2:
        raise ValueError("need >= 2 levels")
    step = (upper - lower) / (n - 1)
    return [lower + step * i for i in range(n)]


def geometric_levels(lower: Decimal, upper: Decimal, n: int) -> list[Decimal]:
    if n < 2:
        raise ValueError("need >= 2 levels")
    if lower <= 0:
        raise ValueError("lower must be > 0 for geometric spacing")
    ratio = (upper / lower) ** (Decimal(1) / Decimal(n - 1))
    return [lower * (ratio ** i) for i in range(n)]


def quantize(value: Decimal, tick: Decimal, side: Side | None = None) -> Decimal:
    """Snap a price to the tick grid. BUYs round down, SELLs round up so the
    quantized price never crosses to the wrong side of the intended level."""
    if tick <= 0:
        return value
    rounding = ROUND_DOWN if side is Side.BUY else ROUND_UP if side is Side.SELL else ROUND_HALF_UP
    return (value / tick).quantize(Decimal(1), rounding=rounding) * tick


def quantize_qty(qty: Decimal, step: Decimal, min_qty: Decimal) -> Decimal:
    """Round an order qty DOWN to the venue's qty step. A qty that floors below the
    venue min (including zero/dust) returns 0 — an unfunded grid must place nothing,
    never get silently inflated up to min_qty (which would spend money the user never
    meant to deploy). The engine treats a 0 size as 'don't trade this grid'."""
    if qty <= 0:
        return Decimal(0)
    if step > 0:
        qty = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
    return qty if (min_qty <= 0 or qty >= min_qty) else Decimal(0)


def levels_for(lower: Decimal, upper: Decimal, n: int, spacing: Spacing) -> list[Decimal]:
    """Build n price levels in [lower, upper] with the given spacing. Shared by the
    initial grid and by live re-centering (same band width, shifted center)."""
    fn = geometric_levels if spacing is Spacing.GEOMETRIC else arithmetic_levels
    return fn(lower, upper, n)


def plan_levels(cfg: GridConfig) -> list[Decimal]:
    if cfg.levels > cfg.max_levels:
        raise ValueError(f"levels {cfg.levels} exceeds max_levels {cfg.max_levels}")
    return levels_for(cfg.lower, cfg.upper, cfg.levels, cfg.spacing)


def _external_id(instance_id: str, level: int, nonce: int) -> str:
    return f"grid-{instance_id}-L{level}-{nonce}"


def build_grid_orders(cfg: GridConfig, levels: list[Decimal], mid: Decimal, nonce: int = 0,
                      bias: int = 0) -> list[Order]:
    """Grid: BUY at every level below mid, SELL at every level above. The level
    nearest mid is skipped to avoid an immediate self-cross. `nonce` namespaces the
    external_ids per generation so re-centered grids never collide with already-seen
    order ids.

    `bias` shapes the ladder for the regime: 0 places both sides (mean-reversion,
    ranging market); +1 places ONLY the buy ladder (uptrend — sells then appear
    organically as paired take-profits of filled buys, so the grid is never net
    short against the trend); -1 mirrors that for a downtrend."""
    orders: list[Order] = []
    for i, price in enumerate(levels):
        if price < mid:
            side = Side.BUY
        elif price > mid:
            side = Side.SELL
        else:
            continue
        if (bias > 0 and side is Side.SELL) or (bias < 0 and side is Side.BUY):
            continue  # trend mode: never open inventory against the trend
        orders.append(
            Order(
                instance_id=cfg.instance_id,
                market=cfg.market,
                side=side,
                price=price,
                qty=cfg.order_size,
                external_id=_external_id(cfg.instance_id, i, nonce),
                level=i,
            )
        )
    return orders


def compute_paired_order(cfg: GridConfig, levels: list[Decimal], fill_level: int,
                         fill_side: Side, nonce: int) -> Order | None:
    """On a BUY fill at level i, place a SELL one level up (i+1); on a SELL fill
    at level j, place a BUY one level down (j-1). Returns None at the boundary."""
    if fill_side is Side.BUY:
        target = fill_level + 1
        side = Side.SELL
    else:
        target = fill_level - 1
        side = Side.BUY
    if target < 0 or target >= len(levels):
        return None
    return Order(
        instance_id=cfg.instance_id,
        market=cfg.market,
        side=side,
        price=levels[target],
        qty=cfg.order_size,
        external_id=_external_id(cfg.instance_id, target, nonce),
        level=target,
    )


def parse_external_id(external_id: str) -> tuple[str, int, int]:
    """Reverse of _external_id: 'grid-{instance}-L{level}-{nonce}' ->
    (instance_id, level, nonce). Instance ids may contain hyphens; we split on the
    last '-L' segment."""
    if not external_id.startswith("grid-"):
        raise ValueError(f"not a grid external_id: {external_id}")
    body = external_id[len("grid-"):]
    instance_id, sep, tail = body.rpartition("-L")
    if not sep:
        raise ValueError(f"cannot parse level from: {external_id}")
    level_s, _, nonce_s = tail.partition("-")
    return instance_id, int(level_s), int(nonce_s or 0)


def parse_level(external_id: str) -> int:
    return parse_external_id(external_id)[1]


# ---- fee-aware spacing -------------------------------------------------------
# A grid only profits if each paired buy/sell clears the round-trip cost. On BSC
# that's the swap fee (PancakeSwap V2 = 25 bps; V3 stable as low as 1-5 bps) on BOTH
# legs, plus slippage and the competition's simulated tx cost. Levels closer than
# that bleed money on every cycle — so the builders below widen the grid (drop
# levels) until the tightest adjacent spacing clears the floor.

DEFAULT_FEE_BPS = 25        # PancakeSwap V2 swap fee per leg (0.25%)
DEFAULT_SLIPPAGE_BPS = 10   # execution slippage allowance
DEFAULT_GAS_BPS = 5         # headroom for the simulated tx cost


def fee_floor_bps(fee_bps: int = DEFAULT_FEE_BPS, slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
                  gas_bps: int = DEFAULT_GAS_BPS) -> int:
    """Minimum profitable round-trip spacing in bps: fee on the buy AND the sell, plus
    slippage and simulated gas."""
    return 2 * fee_bps + slippage_bps + gas_bps


def min_adjacent_spacing_bps(lower: Decimal, upper: Decimal, levels: int, spacing: Spacing) -> int:
    """Tightest gap between adjacent levels, in bps relative to the higher price
    (worst case for fee coverage). Monotonically shrinks as `levels` grows."""
    if levels < 2 or lower <= 0 or upper <= lower:
        return 0
    lv = levels_for(lower, upper, levels, spacing)
    gaps = [(lv[i + 1] - lv[i]) / lv[i + 1] for i in range(len(lv) - 1)]
    return int(min(gaps) * Decimal(10_000))


def max_levels_for_spacing(lower: Decimal, upper: Decimal, floor_bps: int,
                           spacing: Spacing, cap: int = 200) -> int:
    """Largest level count whose tightest adjacent spacing still clears `floor_bps`."""
    best = 2
    for n in range(2, cap + 1):
        if min_adjacent_spacing_bps(lower, upper, n, spacing) >= floor_bps:
            best = n
        else:
            break
    return best


def fit_levels_to_fees(lower: Decimal, upper: Decimal, levels: int, spacing: Spacing,
                       floor_bps: int) -> int:
    """Reduce `levels` until adjacent spacing clears `floor_bps`. Raises if even a
    2-level grid can't (the band itself is narrower than the fee floor — widen it)."""
    if min_adjacent_spacing_bps(lower, upper, 2, spacing) < floor_bps:
        raise ValueError(
            f"band [{lower},{upper}] spans < fee floor {floor_bps}bps — widen the band "
            f"or pick a wider preset; a grid this tight loses money to fees every cycle")
    return min(levels, max_levels_for_spacing(lower, upper, floor_bps, spacing))


# ---- volatility-sized band ---------------------------------------------------
# A grid only fills if its band matches how far price actually travels. A fixed
# preset ±7% band on a token that swings ~1%/day means price never crosses a level
# (the CAKE paper run: 5 fills in 3.6h, ~flat PnL). Size the half-band to the recent
# daily range instead, clamped to a viable floor (still clears the fee floor with a
# few levels) and the preset's risk cap (vol sizing only ever TIGHTENS — it can't make
# the band riskier than the chosen preset).
VOL_BAND_MULT = Decimal("0.75")        # half-band ≈ 0.75 × (24h range frac) -> band ≈ 1.5× the daily swing
VOL_HALF_BAND_FLOOR = Decimal("0.015")  # never tighter than ±1.5% (keeps several fee-clearing levels)


def vol_half_band(range_24h_pct: float, cap: Decimal,
                  floor: Decimal = VOL_HALF_BAND_FLOOR) -> Decimal:
    """Half-band sized to a token's recent daily range, clamped to [floor, cap].
    `range_24h_pct` = (24h high − low)/price × 100. 0/missing -> `cap` (preset default)."""
    if range_24h_pct <= 0:
        return cap
    h = Decimal(str(range_24h_pct)) / Decimal(100) * VOL_BAND_MULT
    return max(floor, min(cap, h))


def _demo() -> None:
    cap = Decimal("0.07")
    assert vol_half_band(0.0, cap) == cap                  # unknown range -> preset
    assert vol_half_band(1.1, cap) == VOL_HALF_BAND_FLOOR  # CAKE-flat -> clamped to floor (was ±7%)
    assert vol_half_band(99.0, cap) == cap                 # wild -> clamped to preset cap
    mid = vol_half_band(6.0, cap)                          # 6% range -> 0.045, between floor & cap
    assert VOL_HALF_BAND_FLOOR < mid < cap, mid
    print("grid.vol_half_band OK:", [str(vol_half_band(r, cap)) for r in (0, 1.1, 6.0, 99.0)])


if __name__ == "__main__":
    _demo()
