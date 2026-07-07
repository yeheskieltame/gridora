"""Backtest + grid-search for the autopilot's dip-turn strategy over real Gate history.

Imports the LIVE decision functions from scripts.autopilot (one source of truth) and
replays them over N days of 5m candles for the whole universe: same prefilter, same
2-scan confirm, same never-red ratchet exit. Grid-search patches the module knobs.

Honest limitations (both make live >= backtest, not the reverse):
  - taker flow isn't in historical candles -> the taker>=54 entry filter and the
    taker leg of fast-reversal are SKIPPED here (backtest enters MORE often).
  - fills at candle close +/- friction (no intrabar).

Run:  python -m scripts.autopilot_backtest              # grid search, 30 days
      python -m scripts.autopilot_backtest --days 14 --top 15
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import time
import urllib.request

import scripts.autopilot as ap

CACHE = os.path.join(os.path.dirname(__file__), "..", ".bt_cache")
UA = {"User-Agent": "Mozilla/5.0"}
START_USD = 30.0
SEVEN_ON = False
SEVEN_LO, SEVEN_HI = -25.0, 40.0
WARM = 288          # 24h of 5m candles before trading starts
STEP_5M = 300


def get(url: str):
    for _ in range(3):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20))
        except Exception:  # noqa: BLE001
            time.sleep(1.5)
    return None


def candles_5m(pair: str, days: int) -> list[tuple[int, float, float, float, float]]:
    """[(t, close, high, low, quote_vol)] oldest-first, cached on disk."""
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, f"ap_{pair}_{days}.json")
    if os.path.exists(f) and time.time() - os.path.getmtime(f) < 6 * 3600:
        with open(f) as fh:
            return [tuple(c) for c in json.load(fh)]
    end = int(time.time())
    start = end - days * 86400
    out: list[tuple[int, float, float, float, float]] = []
    t0 = start
    while t0 < end:
        t1 = min(t0 + 999 * STEP_5M, end)
        rows = get(f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={pair}"
                   f"&interval=5m&from={t0}&to={t1}")
        if isinstance(rows, list):
            for c in rows:
                try:
                    out.append((int(c[0]), float(c[2]), float(c[3]), float(c[4]), float(c[1])))
                except (ValueError, IndexError):
                    continue
        t0 = t1 + STEP_5M
        time.sleep(0.15)
    out.sort()
    with open(f, "w") as fh:
        json.dump(out, fh)
    return out


def precompute(cs: list[tuple[int, float, float, float, float]]) -> list[dict | None]:
    """Per-step primitives the live scanner derives from tickers + deep()."""
    n = len(cs)
    stats: list[dict | None] = [None] * n
    for i in range(WARM, n):
        w = cs[i - WARM + 1: i + 1]
        px = cs[i][1]
        hi = max(c[2] for c in w)
        lo = min(c[3] for c in w)
        if lo <= 0 or hi <= lo:
            continue
        lows15 = [min(c[3] for c in cs[i - j - 2: i - j + 1]) for j in range(33, -3, -3)]
        stats[i] = {
            "px": px,
            "chg24": (px / cs[i - WARM][1] - 1) * 100,
            "rng": (hi / lo - 1) * 100,
            "pos": (px - lo) / (hi - lo),
            "room": (hi / px - 1) * 100,
            "vol": sum(c[4] for c in w),
            "up": cs[i][1] >= cs[i - 1][1],
            "m5": (cs[i][1] / cs[i - 1][1] - 1) * 100,
            "base": ap.base_formed(lows15),
            "chg7d": (px / cs[i - 2016][1] - 1) * 100 if i >= 2016 else None,
        }
    return stats


def simulate(data: dict[str, list], stats: dict[str, list], btc: dict[int, tuple[bool, float]],
             ticks: list[int], idx: dict[str, dict[int, int]], gap_mode: str, peak_frac: float,
             sl_mode: str = "none", sl_pct: float = 0.05) -> dict:
    usdt = START_USD
    pos = None          # (sym, qty, cost, eff, peak, px_peak, t_open)
    cooldown: dict[str, float] = {}
    pending: tuple[str, int] | None = None
    trades: list[float] = []
    holds: list[float] = []
    eq_peak, max_dd = START_USD, 0.0

    for t in ticks:
        ok_btc = btc.get(t, (True, 0.0))[0]
        if pos:
            sym, qty, cost, eff, peak, px_peak, t0 = pos
            j = idx[sym].get(t)
            if j is None:
                continue
            px = data[sym][j][1]
            m5 = stats[sym][j]["m5"] if stats[sym][j] else 0.0
            n = ap.net_of(qty, px, cost)
            peak = max(peak, n); px_peak = max(px_peak, px)
            pos = (sym, qty, cost, eff, peak, px_peak, t0)
            sell = None
            btc_1h = btc.get(t, (True, 0.0))[1]
            if sl_mode == "hard" and n <= -sl_pct * cost:
                sell = "sl"
            elif sl_mode == "btc_crash" and n < 0 and btc_1h <= -2.5:
                sell = "btc-crash-cut"
            elif px >= eff * (1 + ap.CAP_PCT):
                sell = "cap"
            elif n >= ap.FAST_NET_PCT * cost and m5 <= ap.FAST_5M:
                sell = "fast"
            elif peak >= ap.ARM:
                if gap_mode == "fixed":
                    gap = cost * (ap.RIDE_GAP_PCT if ok_btc else ap.LOCK_GAP_PCT)
                    floor = ap.ratchet_floor(peak, gap)
                else:  # peakfrac: give back at most peak_frac of the peak
                    floor = max(ap.FLOOR_MIN, peak * (1 - peak_frac))
                if ap.should_lock(n, floor):
                    sell = "lock"
            if sell:
                usdt += qty * px * (1 - ap.SELL_FRIC) - ap.GAS_USD
                trades.append(n); holds.append((t - t0) / 3600)
                cooldown[sym] = t + ap.COOLDOWN_MIN * 60
                pos = None
            eq = usdt if not pos else usdt + qty * px * (1 - ap.SELL_FRIC)
        else:
            eq = usdt
            if ok_btc:
                best = None
                for sym, st in stats.items():
                    j = idx[sym].get(t)
                    s = st[j] if j is not None else None
                    if not s or t < cooldown.get(sym, 0):
                        continue
                    sc = ap.dip_score(s["chg24"], s["rng"], s["pos"], s["room"], s["vol"])
                    if sc is None or not (s["base"] and s["up"]):
                        continue
                    c7 = s.get("chg7d")
                    if SEVEN_ON and c7 is not None and not (SEVEN_LO <= c7 <= SEVEN_HI):
                        continue
                    if best is None or sc > best[0]:
                        best = (sc, sym, s)
                if best:
                    _, sym, s = best
                    if pending and pending[0] == sym:
                        px = s["px"]
                        amt = usdt - float(ap.RESERVE)
                        if amt >= 5:
                            qty = amt * (1 - ap.SELL_FRIC) / px
                            usdt -= amt
                            pos = (sym, qty, amt, amt / qty, -9e9, px, t)
                        pending = None
                    else:
                        pending = (sym, t)
                else:
                    pending = None
            else:
                pending = None
        eq_peak = max(eq_peak, eq)
        max_dd = max(max_dd, (eq_peak - eq) / eq_peak)

    open_mtm = 0.0
    if pos:
        sym, qty, cost, *_ = pos
        last = data[sym][-1][1]
        open_mtm = ap.net_of(qty, last, cost)
    wins = [x for x in trades if x > 0]
    return {"pnl": usdt + (pos[2] + open_mtm if pos else 0) - START_USD,
            "closed": len(trades), "wins": len(wins),
            "win_rate": len(wins) / len(trades) * 100 if trades else 0.0,
            "sum_closed": sum(trades), "max_dd": max_dd * 100,
            "avg_hold_h": sum(holds) / len(holds) if holds else 0.0,
            "open": f"{pos[0]} {open_mtm:+.2f}" if pos else "-"}


def main() -> None:
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 30
    top = int(sys.argv[sys.argv.index("--top") + 1]) if "--top" in sys.argv else 12

    syms = ap.universe()
    print(f"fetching {days}d x 5m for BTC + {len(syms)} tokens (cached in .bt_cache/)...", flush=True)
    btc_cs = candles_5m("BTC_USDT", days)
    data: dict[str, list] = {}
    for s in syms:
        cs = candles_5m(ap.GATE_PAIR.get(s, s) + "_USDT", days)
        if len(cs) > WARM + 100:
            data[s] = cs
    print(f"got {len(data)} tokens with usable history", flush=True)

    stats = {s: precompute(cs) for s, cs in data.items()}
    idx = {s: {c[0]: i for i, c in enumerate(cs)} for s, cs in data.items()}
    bidx = {c[0]: i for i, c in enumerate(btc_cs)}
    btc = {}
    for t, i in bidx.items():
        if i >= WARM:
            c1h = (btc_cs[i][1] / btc_cs[i - 12][1] - 1) * 100
            c24 = (btc_cs[i][1] / btc_cs[i - WARM][1] - 1) * 100
            btc[t] = (c1h >= ap.BTC_1H_MIN and c24 >= ap.BTC_24H_MIN, c1h)
    all_ts: set[int] = set()
    for s in idx:
        all_ts.update(idx[s].keys())
    ticks = sorted(all_ts & set(btc))

    base = {k: getattr(ap, k) for k in
            ("POS_MAX", "RANGE_MIN", "ROOM_MIN", "RIDE_GAP_PCT", "CAP_PCT")}
    grid = list(itertools.product(
        [0.35, 0.45, 0.55],            # POS_MAX
        [4.5, 6.0, 8.0],               # RANGE_MIN
        [0.010, 0.015, 0.025],         # RIDE_GAP_PCT (fixed mode)
        [0.08, 0.12, 0.20],            # CAP_PCT
        [("fixed", 0.0), ("peakfrac", 0.35), ("peakfrac", 0.5)],
    ))
    print(f"grid: {len(grid)} configs over {len(ticks)} ticks\n", flush=True)
    rows = []
    for pos_max, rng_min, gapp, capp, (gmode, pfrac) in grid:
        ap.POS_MAX, ap.RANGE_MIN, ap.RIDE_GAP_PCT, ap.CAP_PCT = pos_max, rng_min, gapp, capp
        r = simulate(data, stats, btc, ticks, idx, gmode, pfrac)
        rows.append(((pos_max, rng_min, gapp, capp, gmode, pfrac), r))
    for k, v in base.items():
        setattr(ap, k, v)

    rows.sort(key=lambda x: -x[1]["pnl"])
    print(f"{'pos':>4} {'rng':>4} {'gap':>5} {'cap':>4} {'exit':<13} | "
          f"{'pnl$':>7} {'closed':>6} {'win%':>5} {'dd%':>5} {'hold_h':>6} open")
    for (pm, rm, gp, cp, gm, pf), r in rows[:top]:
        ex = gm if gm == "fixed" else f"peakfrac {pf}"
        print(f"{pm:>4} {rm:>4} {gp:>5} {cp:>4} {ex:<13} | "
              f"{r['pnl']:>+7.2f} {r['closed']:>6} {r['win_rate']:>5.0f} "
              f"{r['max_dd']:>5.1f} {r['avg_hold_h']:>6.1f} {r['open']}")
    cur = rows and [r for (c, r) in rows if c == (base['POS_MAX'], base['RANGE_MIN'],
          base['RIDE_GAP_PCT'], base['CAP_PCT'], 'fixed', 0.0)]
    if cur:
        print(f"\ncurrent live config: pnl {cur[0]['pnl']:+.2f} | closed {cur[0]['closed']} "
              f"| win {cur[0]['win_rate']:.0f}% | dd {cur[0]['max_dd']:.1f}%")


if __name__ == "__main__":
    main()


def risk_study() -> None:
    """Focused run: best structural config, compare risk modes (never-red vs cuts)."""
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 30
    syms = ap.universe()
    btc_cs = candles_5m("BTC_USDT", days)
    data = {}
    for s in syms:
        cs = candles_5m(ap.GATE_PAIR.get(s, s) + "_USDT", days)
        if len(cs) > WARM + 100:
            data[s] = cs
    stats = {s: precompute(cs) for s, cs in data.items()}
    idx = {s: {c[0]: i for i, c in enumerate(cs)} for s, cs in data.items()}
    bidx = {c[0]: i for i, c in enumerate(btc_cs)}
    btc = {}
    for t, i in bidx.items():
        if i >= WARM:
            c1h = (btc_cs[i][1] / btc_cs[i - 12][1] - 1) * 100
            c24 = (btc_cs[i][1] / btc_cs[i - WARM][1] - 1) * 100
            btc[t] = (c1h >= ap.BTC_1H_MIN and c24 >= ap.BTC_24H_MIN, c1h)
    all_ts = set()
    for s in idx:
        all_ts.update(idx[s].keys())
    ticks = sorted(all_ts & set(btc))

    ap.POS_MAX, ap.RANGE_MIN, ap.RIDE_GAP_PCT = 0.45, 4.5, 0.015
    print(f"{'cap':>4} {'risk mode':<16} | {'pnl$':>7} {'closed':>6} {'win%':>5} {'dd%':>5} {'hold_h':>6} open")
    for cap in (0.06, 0.08, 0.12):
        ap.CAP_PCT = cap
        for mode, pct in (("none", 0), ("btc_crash", 0), ("hard", 0.05), ("hard", 0.08)):
            r = simulate(data, stats, btc, ticks, idx, "fixed", 0.0, mode, pct)
            lbl = mode if mode != "hard" else f"hard-sl {pct*100:.0f}%"
            print(f"{cap:>4} {lbl:<16} | {r['pnl']:>+7.2f} {r['closed']:>6} "
                  f"{r['win_rate']:>5.0f} {r['max_dd']:>5.1f} {r['avg_hold_h']:>6.1f} {r['open']}")


if "--risk" in sys.argv:
    risk_study()


def fng_study() -> None:
    """Regime lever: same 30d sim, gated by CMC Fear&Greed (daily) at various thresholds."""
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 30
    with open(os.path.join(CACHE, "fng_hist.json")) as f:
        fng = {int(x["timestamp"]) // 86400: int(x["value"]) for x in json.load(f)["data"]}
    syms = ap.universe()
    btc_cs = candles_5m("BTC_USDT", days)
    data = {}
    for s in syms:
        cs = candles_5m(ap.GATE_PAIR.get(s, s) + "_USDT", days)
        if len(cs) > WARM + 100:
            data[s] = cs
    stats = {s: precompute(cs) for s, cs in data.items()}
    idx = {s: {c[0]: i for i, c in enumerate(cs)} for s, cs in data.items()}
    bidx = {c[0]: i for i, c in enumerate(btc_cs)}

    ap.POS_MAX, ap.RANGE_MIN, ap.RIDE_GAP_PCT, ap.CAP_PCT = 0.45, 4.5, 0.015, 0.06
    print(f"{'fng_min':>7} | {'pnl$':>7} {'closed':>6} {'win%':>5} {'dd%':>5} {'hold_h':>6} open")
    for fng_min in (0, 15, 20, 25, 30):
        btc = {}
        for t, i in bidx.items():
            if i >= WARM:
                c1h = (btc_cs[i][1] / btc_cs[i - 12][1] - 1) * 100
                c24 = (btc_cs[i][1] / btc_cs[i - WARM][1] - 1) * 100
                f_ok = fng.get(t // 86400, 50) >= fng_min
                btc[t] = (c1h >= ap.BTC_1H_MIN and c24 >= ap.BTC_24H_MIN and f_ok, c1h)
        all_ts = set()
        for s in idx:
            all_ts.update(idx[s].keys())
        ticks = sorted(all_ts & set(btc))
        r = simulate(data, stats, btc, ticks, idx, "fixed", 0.0)
        print(f"{fng_min:>7} | {r['pnl']:>+7.2f} {r['closed']:>6} {r['win_rate']:>5.0f} "
              f"{r['max_dd']:>5.1f} {r['avg_hold_h']:>6.1f} {r['open']}")


if "--fng" in sys.argv:
    fng_study()


def seven_study() -> None:
    """The SLX lever: block dip-buys on post-parabola collapses / blowoffs (7d chg bounds)."""
    import scripts.autopilot_backtest as bt
    days = int(sys.argv[sys.argv.index("--days") + 1]) if "--days" in sys.argv else 30
    syms = ap.universe()
    btc_cs = candles_5m("BTC_USDT", days)
    data = {}
    for s in syms:
        cs = candles_5m(ap.GATE_PAIR.get(s, s) + "_USDT", days)
        if len(cs) > WARM + 100:
            data[s] = cs
    stats = {s: precompute(cs) for s, cs in data.items()}
    idx = {s: {c[0]: i for i, c in enumerate(cs)} for s, cs in data.items()}
    bidx = {c[0]: i for i, c in enumerate(btc_cs)}
    btc = {}
    for t, i in bidx.items():
        if i >= WARM:
            c1h = (btc_cs[i][1] / btc_cs[i - 12][1] - 1) * 100
            c24 = (btc_cs[i][1] / btc_cs[i - WARM][1] - 1) * 100
            btc[t] = (c1h >= ap.BTC_1H_MIN and c24 >= ap.BTC_24H_MIN, c1h)
    all_ts = set()
    for s in idx:
        all_ts.update(idx[s].keys())
    ticks = sorted(all_ts & set(btc))
    ap.POS_MAX, ap.RANGE_MIN, ap.RIDE_GAP_PCT, ap.CAP_PCT = 0.45, 4.5, 0.015, 0.06
    print(f"{'7d guard':<16} | {'pnl$':>7} {'closed':>6} {'win%':>5} {'dd%':>5} open")
    for on, lo, hi in ((False, 0, 0), (True, -25, 40), (True, -20, 30), (True, -15, 25)):
        bt.SEVEN_ON, bt.SEVEN_LO, bt.SEVEN_HI = on, float(lo), float(hi)
        r = simulate(data, stats, btc, ticks, idx, "fixed", 0.0)
        lbl = "off" if not on else f"[{lo},{hi}]%"
        print(f"{lbl:<16} | {r['pnl']:>+7.2f} {r['closed']:>6} {r['win_rate']:>5.0f} {r['max_dd']:>5.1f} {r['open']}")


if "--seven" in sys.argv:
    seven_study()
