"""Offline performance backtest — drive the real GridEngine over deterministic price
paths and report PnL / drawdown / behavior per scenario. No keys, no net.

Run:  python -m scripts.backtest        (from backend/, venv active)
"""
from __future__ import annotations

import asyncio
import math
from decimal import Decimal

from gridora.adapters.chain.memory_chain import MemoryChain
from gridora.adapters.exchanges.fake import FakeExchange
from gridora.adapters.signals.fake import FakeSignals
from gridora.agent.brain import ClaudeBrain, FakeLLM
from gridora.agent.loop import LearningLoop
from gridora.agent.state import GridoraState
from gridora.agent.supervisor import AgentSupervisor
from gridora.adapters.signals.allowlist import load_allowlist
from gridora.app.portfolio import Portfolio
from gridora.app.safety import AccountGuard, Allowlist, CircuitBreaker, ProfitGuard
from gridora.app.service import GridService
from gridora.domain.models import RiskPreset, Venue

MID = Decimal("100")
MARGIN = Decimal("1000")
ALLOW = Allowlist(symbols=frozenset({"CAKE", "USDT"}))

# Tunables a sweep overrides (defaults = current production values).
from gridora.app.engine import GridEngine  # noqa: E402

PROFIT_ARM_FRAC = Decimal("0.05")   # ProfitGuard arms once peak pnl >= this fraction of margin
PROFIT_TRAIL = Decimal("0.4")       # ...then banks on this give-back from the peak
TREND_STOP = Decimal("0.05")        # flatten when price breaks the band by this fraction
RECENTER_BUF = Decimal("0.10")      # re-center hysteresis (fraction of band width)


def _guards():
    breaker = CircuitBreaker(max_drawdown_frac=Decimal("0.12"))
    profit = ProfitGuard(trail_frac=PROFIT_TRAIL, trail_arm=MARGIN * PROFIT_ARM_FRAC)
    GridEngine.RECENTER_BUFFER_FRAC = RECENTER_BUF
    return breaker, profit


def path(drift: float, wiggle: float, steps: int = 60, period: int = 6) -> list[Decimal]:
    """Price multipliers: 1 + linear drift + sinusoidal wiggle (markets oscillate around
    a trend; a grid banks the wiggles). Deterministic so runs are reproducible."""
    out = []
    for t in range(steps):
        d = drift * t / (steps - 1)
        w = wiggle * math.sin(2 * math.pi * t / period)
        out.append(Decimal(str(round(1 + d + w, 5))))
    return out


def v_path(down: float, wiggle: float, steps: int = 60, period: int = 6) -> list[Decimal]:
    """Down to the trough at the midpoint, then back up (a V)."""
    out = []
    for t in range(steps):
        half = steps // 2
        d = -down * (t / half) if t <= half else -down * (2 - t / half)
        w = wiggle * math.sin(2 * math.pi * t / period)
        out.append(Decimal(str(round(1 + d + w, 5))))
    return out


# (name, signals fear_greed, signals momentum, price path)
SCENARIOS = [
    ("range_calm",        52, "0.0",  path(0.00, 0.02)),
    ("range_wide",        52, "0.0",  path(0.00, 0.06)),
    ("sawtooth",          52, "0.0",  path(0.00, 0.04, period=4)),
    ("uptrend_choppy",    62, "0.4",  path(0.25, 0.03)),
    ("downtrend_choppy",  36, "-0.4", path(-0.25, 0.03)),
    ("crash",             20, "-0.8", path(-0.35, 0.02, period=10)),
    ("pump",              80, "0.8",  path(0.35, 0.02, period=10)),
    ("v_recovery",        45, "0.0",  v_path(0.18, 0.03)),
]


async def run_scenario(fear: int, mom: str, mults: list[Decimal], regime_aware: bool):
    ex = FakeExchange({"mid": str(MID), "tick": "0.01", "step": "0.0001",
                       "min_order_size": "0.001", "equity": str(MARGIN)})
    signals = FakeSignals(fear_greed=(fear if regime_aware else 52),
                          momentum=(mom if regime_aware else "0.0"))
    breaker, profit = _guards()
    loop = LearningLoop(ex, signals, MemoryChain(), ALLOW, breaker, profit,
                        account_guard=AccountGuard())
    svc = GridService(loop, venue=Venue.FAKE)
    res = await svc.launch("CAKE/USDT", RiskPreset.BALANCED, MARGIN)
    eng = loop.engine(res.instance_id)
    for m in mults:
        for f in await ex.move_price("CAKE/USDT", MID * m):
            await eng.handle_fill(f)
        await eng.maybe_recenter()
    out = await svc.close(res.instance_id)
    return out, eng, mults[-1]


async def run_agentic_scenario(fear: int, mom: str, mults: list[Decimal]):
    """The ACTUAL product: AgentSupervisor (FakeLLM brain) — adapts the strategy to the
    regime AND re-enters after a self-exit (a single engine can't). Measures cumulative
    booked PnL across all episodes (re-launches included)."""
    ex = FakeExchange({"mid": str(MID), "tick": "0.01", "step": "0.0001",
                       "min_order_size": "0.001", "equity": str(MARGIN)})
    signals = FakeSignals(fear_greed=fear, momentum=Decimal(mom))
    breaker, profit = _guards()
    state = GridoraState(market="CAKE/USDT", mode="dry", chain_id=97, agent_address="0xtest")
    sup = AgentSupervisor(ex, signals, MemoryChain(), ClaudeBrain(FakeLLM()), state,
                          breaker, profit, ALLOW, MARGIN, venue=Venue.FAKE,
                          account_guard=AccountGuard(), trend_stop_frac=TREND_STOP)
    await sup.boot()
    for m in mults:
        for f in await ex.move_price("CAKE/USDT", MID * m):
            if sup.engine is not None:
                await sup.engine.handle_fill(f)
        if sup.engine is not None:
            await sup.engine.maybe_recenter()
            await sup._reap_if_halted()      # book a self-exit
        if sup.engine is None:               # flat (stopped out / not launched) → re-enter
            await sup.decide_and_apply()
    if sup.engine is not None:
        await sup._close_current()           # book the open episode
    return state


async def run_portfolio_scenario(n_markets: int = 4):
    """Full pipeline: select N liquid markets from the real allowlist (FakeSignals hash
    metrics) → launch a grid per market → drive each over a DIFFERENT path (mixed regime:
    sawtooth/up/down/range/V) → close. Shows the portfolio's aggregate vs any single grid."""
    allow = Allowlist(symbols=load_allowlist())
    exchanges: dict[str, FakeExchange] = {}

    def exfac(market: str) -> FakeExchange:
        ex = FakeExchange({"mid": str(MID), "tick": "0.01", "step": "0.0001",
                           "min_order_size": "0.001", "equity": str(MARGIN)})
        exchanges[market] = ex
        return ex

    pf = Portfolio(exfac, FakeSignals(), MemoryChain(), allow, MARGIN, venue=Venue.FAKE,
                   quote="USDT", n_markets=n_markets, min_vol_usd=Decimal(20_000_000))
    picks = await pf.launch()
    mixed = [path(0.0, 0.04, period=4), path(0.20, 0.03), path(-0.20, 0.03),
             path(0.0, 0.02), v_path(0.18, 0.03)]   # diversified: chop / up / down / range / V
    for i, p in enumerate(picks):
        ex, mults = exchanges[p.market], mixed[i % len(mixed)]
        eng = next(e for e in pf.engines.values() if e.cfg.market == p.market)
        for m in mults:
            for f in await ex.move_price(p.market, MID * m):
                await eng.handle_fill(f)
            await eng.maybe_recenter()
    await pf.close_all()
    return pf, picks


async def run_band_comparison() -> None:
    """Fix #1: a FLAT token (swings ~1.1%/day, like CAKE this run) through a fixed ±preset
    band vs a volatility-sized band. Same path, same margin — only the band width differs.
    The fixed wide band sits outside the price action (no fills); the vol-sized band hugs
    it (price crosses levels -> spreads banked)."""
    low_vol = path(0.0, 0.011, steps=80, period=6)            # ~±1.1% wiggle (CAKE-like)
    realized = float((max(low_vol) - min(low_vol)) * 100)     # peak-to-trough %, the "24h range"
    rows = []
    for label, rng in (("fixed (preset)", 0.0), ("volatility-sized", realized)):
        ex = FakeExchange({"mid": str(MID), "tick": "0.01", "step": "0.0001",
                           "min_order_size": "0.001", "equity": str(MARGIN)})
        breaker, profit = _guards()
        cfg = GridService.config_for_preset("cmp-band", "CAKE/USDT", RiskPreset.BALANCED,
                                            MARGIN, MID, Venue.FAKE, range_24h_pct=rng)
        eng = GridEngine(ex, cfg, breaker=breaker, profit_guard=profit, account_guard=AccountGuard())
        await eng.start()
        for m in low_vol:
            for f in await ex.move_price("CAKE/USDT", MID * m):
                await eng.handle_fill(f)
            await eng.maybe_recenter()
        out = eng.outcome()
        band_pct = float((cfg.upper - cfg.lower) / MID / 2 * 100)
        rows.append((label, band_pct, cfg.levels, eng.fill_count, out.trades, out.pnl_bps))
    print(f"\n=== BAND SIZING — fix #1: fixed vs volatility-sized band on a FLAT token (~{realized:.1f}% daily range) ===")
    print(f"{'band':<18}{'±%':>7}{'levels':>8}{'fills':>7}{'trades':>8}{'PnL bps':>9}")
    for label, bandp, lv, fills, trades, pnl in rows:
        print(f"{label:<18}{bandp:>6.2f}%{lv:>8}{fills:>7}{trades:>8}{pnl:>9}")


async def main() -> None:
    for regime_aware in (False, True):
        title = "REGIME-AWARE (signals match the path)" if regime_aware else "NEUTRAL grid (symmetric baseline)"
        print(f"\n=== {title} — BALANCED, margin {MARGIN}, DD cap 12% of deployed ===")
        print(f"{'scenario':<18}{'endΔ%':>7}{'PnL bps':>9}{'maxDD bps':>10}{'trades':>7}{'win%':>6}  exit")
        net = 0
        for name, fear, mom, mults in SCENARIOS:
            out, eng, last = await run_scenario(fear, mom, mults, regime_aware)
            endd = (last - 1) * 100
            exit_kind = eng.exit_kind or "—"
            print(f"{name:<18}{endd:>6.1f}%{out['pnl_bps']:>9}{out['max_drawdown_bps']:>10}"
                  f"{out['trades']:>7}{eng.winrate * 100:>5.0f}%  {exit_kind}")
            net += out["pnl_bps"]
        print(f"{'TOTAL':<18}{'':>7}{net:>9} bps across {len(SCENARIOS)} scenarios")

    # The real product: regime-adaptive + re-entry after stop-outs.
    print(f"\n=== AGENTIC supervisor (regime-adaptive + re-entry) — account return on {MARGIN} margin ===")
    print(f"{'scenario':<18}{'endΔ%':>7}{'PnL quote':>11}{'acct %':>8}{'episodes':>9}")
    acct = Decimal(0)
    for name, fear, mom, mults in SCENARIOS:
        state = await run_agentic_scenario(fear, mom, mults)
        endd = (mults[-1] - 1) * 100
        print(f"{name:<18}{endd:>6.1f}%{float(state.cum_realized):>11.3f}"
              f"{state.account_pnl_pct:>7.2f}%{state.episodes:>9}")
        acct += state.cum_realized
    print(f"{'TOTAL':<18}{'':>7}{float(acct):>11.3f} quote ({float(acct / MARGIN * 100):.2f}% of one margin/scenario)")

    # Multi-market portfolio: ONE margin spread across N selected liquid names, mixed regime.
    print(f"\n=== MULTI-MARKET PORTFOLIO (select N liquid+volatile → grid each → mixed regime) on {MARGIN} margin ===")
    pf, picks = await run_portfolio_scenario(n_markets=4)
    print(f"{'market':<14}{'bias':>5}{'weight':>8}{'7d%':>7}{'vol$M':>8}{'range%':>8}")
    for p in picks:
        print(f"{p.market:<14}{p.bias:>5}{float(p.weight):>8.2f}{p.pct_7d:>7.1f}"
              f"{float(p.vol_24h_usd)/1e6:>8.0f}{p.range_24h_pct:>8.1f}")
    print(f"PORTFOLIO PnL: {float(pf.total_realized):+.3f} quote ({float(pf.total_realized / MARGIN * 100):+.2f}% of margin) across {len(picks)} grids")

    # Fix #1 — band sizing: fixed preset band vs volatility-sized band on a FLAT token.
    await run_band_comparison()


async def sweep() -> None:
    """Grid-search the risk/recenter knobs on the AGENTIC path; print account % by group."""
    global RECENTER_BUF, PROFIT_ARM_FRAC, PROFIT_TRAIL, TREND_STOP
    configs = [
        ("buf.25 arm.02 trail.3 ts.05", "0.25", "0.02", "0.3", "0.05"),
        ("buf.10 arm.02 trail.3 ts.05", "0.10", "0.02", "0.3", "0.05"),
        ("buf.15 arm.05 trail.4 ts.05", "0.15", "0.05", "0.4", "0.05"),
        ("buf.10 arm.05 trail.4 ts.04", "0.10", "0.05", "0.4", "0.04"),
        ("buf.20 arm.08 trail.4 ts.06", "0.20", "0.08", "0.4", "0.06"),
        ("buf.10 arm.10 trail.5 ts.05", "0.10", "0.10", "0.5", "0.05"),
        ("buf.15 arm.06 trail.4 ts.08", "0.15", "0.06", "0.4", "0.08"),
    ]
    pct = lambda x: float(x / MARGIN * 100)  # noqa: E731
    print(f"{'config':<30}{'TOTAL%':>8}{'range%':>8}{'trend%':>8}{'tail%':>8}")
    for name, buf, arm, trail, ts in configs:
        RECENTER_BUF, PROFIT_ARM_FRAC, PROFIT_TRAIL, TREND_STOP = (
            Decimal(buf), Decimal(arm), Decimal(trail), Decimal(ts))
        tot = rng = trd = tail = Decimal(0)
        for sn, fear, mom, mults in SCENARIOS:
            st = await run_agentic_scenario(fear, mom, mults)
            tot += st.cum_realized
            if sn in ("range_calm", "range_wide", "sawtooth"):
                rng += st.cum_realized
            elif sn in ("uptrend_choppy", "downtrend_choppy"):
                trd += st.cum_realized
            else:
                tail += st.cum_realized
        print(f"{name:<30}{pct(tot):>7.1f}%{pct(rng):>7.1f}%{pct(trd):>7.1f}%{pct(tail):>7.1f}%")


if __name__ == "__main__":
    import sys
    asyncio.run(sweep() if "sweep" in sys.argv else main())
