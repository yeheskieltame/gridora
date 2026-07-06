"""Entrypoint. `python -m gridora.runner --mode dry|live [--ui] [--brain claude|fake]`.

Paths:
  --mode dry            FakeExchange + FakeSignals + MemoryChain: scripted offline
                        rehearsal of SENSE->DECIDE->COMMIT->EXECUTE->ATTEST (no UI).
  --mode live           BscTwakExchange + CmcSignals(x402 via TWAK) + BscChain: every
                        order signed locally by TWAK, every data call paid via x402.
  --ui                  Launch the Textual TUI with the AGENTIC supervisor — Claude is
                        the strategy router (picks/switches/halts strategies from the
                        CMC regime). dry = fakes + a regime simulation so you can watch
                        Claude switch modes; live = real adapters.
  --brain claude|fake   claude = the LOCAL Claude Code CLI (subscription, no API key);
                        fake = deterministic offline. Default: dry->fake, live->claude.

Example demos:
  python -m gridora.runner --mode dry                 # quick offline loop check
  python -m gridora.runner --mode dry --ui            # TUI, deterministic brain
  python -m gridora.runner --mode dry --ui --brain claude   # TUI, REAL Claude routing
"""
from __future__ import annotations

import argparse
import asyncio
from decimal import Decimal, InvalidOperation

from .adapters.chain.memory_chain import MemoryChain
from .adapters.exchanges.fake import FakeExchange
from .adapters.signals.allowlist import load_allowlist
from .adapters.signals.fake import FakeSignals
from .agent.loop import LearningLoop
from .app.safety import AccountGuard, Allowlist, CircuitBreaker, ProfitGuard
from .app.service import GridService
from .config import settings
from .domain.models import RiskPreset, Venue

# Rough spot prices for the offline FakeExchange mid (only matters for the dry demo).
# All markets here are ELIGIBLE pairs (base + quote both on the 149-token allowlist) —
# BNB/BTC are NOT eligible, so they're deliberately absent.
_DRY_MID = {"CAKE/USDT": "2.5", "ETH/USDT": "3000", "XRP/USDT": "2.2",
            "LINK/USDT": "15", "AVAX/USDT": "30"}


# Auto drawdown cap as a fraction of deployed capital, per preset (all well under the
# 30% DQ). Used when GRIDORA_MAX_DRAWDOWN is 0 (auto) — the engine resolves it to a
# quote amount at episode start, so even dry runs get a real loss stop.
_DD_FRAC = {"SAFE": Decimal("0.08"), "BALANCED": Decimal("0.12"), "AGGRESSIVE": Decimal("0.18")}


def _build_guards() -> tuple[CircuitBreaker, ProfitGuard, AccountGuard]:
    dd = settings.dec("max_drawdown")
    if dd > 0:
        breaker = CircuitBreaker(max_inventory=settings.dec("max_inventory"), max_drawdown=dd)
    else:
        breaker = CircuitBreaker(
            max_inventory=settings.dec("max_inventory"),
            max_drawdown_frac=_DD_FRAC.get(settings.preset.upper(), Decimal("0.12")))
    # Ride a favorable move, lock it after a 40% give-back from a peak >= 5% of margin.
    # (5%/40% from the scripts/backtest sweep: arming at 2% locked tiny gains and churned
    # ranging markets to ~breakeven; 5% lets the grid keep banking spreads → +15% in range.)
    margin = settings.dec("quote_margin")
    profit_guard = ProfitGuard(trail_frac=Decimal("0.4"), trail_arm=margin * Decimal("0.05"))
    account_guard = AccountGuard(max_drop=settings.dec("account_drawdown"))
    return breaker, profit_guard, account_guard


async def run_dry(market: str, preset: RiskPreset, margin: Decimal) -> None:
    mid = Decimal(_DRY_MID.get(market, "100"))
    exchange = FakeExchange({"mid": str(mid), "tick": "0.0001", "step": "0.0001",
                             "min_order_size": "0.001", "equity": str(margin)})
    # A mildly bullish-but-calm regime → symmetric grid (mean-reversion in a range).
    signals = FakeSignals(fear_greed=54, momentum="0.05")
    chain = MemoryChain(agent=settings.agent_address or "0xGRIDORA_AGENT")
    allowlist = Allowlist(symbols=load_allowlist())
    breaker, profit_guard, account_guard = _build_guards()
    loop = LearningLoop(exchange, signals, chain, allowlist, breaker, profit_guard,
                        recenter_interval=0.0, account_guard=account_guard)
    service = GridService(loop, venue=Venue.FAKE)

    print(f"\n=== Gridora DRY RUN — {market} @ ~{mid} | preset {preset.value} | margin {margin} ===")
    await chain.register_identity(chain.agent)
    print(f"identity      : {chain.agent}  (register tx {chain.identity_tx})")

    res = await service.launch(market, preset, margin)
    print(f"SENSE/DECIDE  : {res.rationale}")
    print(f"COMMIT (chain): config hash pinned  tx={res.commit_tx}")
    print(f"EXECUTE       : {res.orders_placed} grid orders placed via {loop.exchange.venue}")
    iid = res.instance_id
    eng = loop.engine(iid)

    # Scripted oscillating path: dip/bounce repeatedly (bank spread) -> a rip just past
    # the band that settles back (re-center, then the round-trip closes in profit).
    path = [mid * Decimal(f) for f in
            ("0.97", "1.0", "1.03", "1.0", "0.97", "1.0", "1.08", "1.02", "1.0")]
    for px in path:
        fills = await exchange.move_price(market, px)
        for f in fills:
            await eng.handle_fill(f)
        recentered = await eng.maybe_recenter()
        tag = "  <- re-center (price left band)" if recentered else ""
        print(f"  price {px:.4f}: {len(fills):>2} fill(s) | net {eng.net_inventory():+.4f} base "
              f"| realized {eng.realized:+.5f} quote{tag}")

    st = await service.status(iid)
    print(f"STATUS        : state={st['state']} band={st['band']} bias={st['bias']} "
          f"fills={st['fills']} resting={st['resting_orders']}")

    out = await service.close(iid)
    print(f"CLOSE/LEARN   : realized {out['realized_pnl_quote']} quote ({out['pnl_bps']}bps) "
          f"over {out['trades']} round-trips, maxDD {out['max_drawdown_bps']}bps")
    print(f"ATTEST (chain): {'ok' if out['attest_ok'] else 'FAILED'}  tx={out['attest_tx']}")
    print(f"fills root    : {out['fills_root']}")
    print("=== dry run complete — the same loop runs live with TWAK + CMC + BSC ===\n")


def _live_chain(twak):
    """Live ChainPort. When the custom verifier contracts are deployed (GRIDORA_*_ADDR set),
    mirror proofs there with the agent key via `cast` (BscMirror) — that's what the public
    verifier page reads. Otherwise fall back to TWAK-native ERC-8004 metadata (BscChain)."""
    import os
    if os.environ.get("GRIDORA_IDENTITY_ADDR"):
        from .adapters.chain.bsc_mirror import BscMirror
        return BscMirror.from_env()
    from .adapters.chain.client import BscChain
    return BscChain(twak, chain_key=settings.chain_key, agent_uri=settings.agent_uri)


async def run_live(market: str, preset: RiskPreset, margin: Decimal) -> None:
    settings.guard()
    settings.assert_twak_creds()   # fail before any on-chain commit / signing if creds are missing
    from .adapters.exchanges.bsc_twak.adapter import BscTwakExchange
    from .adapters.exchanges.bsc_twak.twak_client import TwakClient
    from .adapters.payments.x402 import X402Payments
    from .adapters.signals.cmc import CmcSignals

    twak = TwakClient(chain_key=settings.chain_key)
    exchange = BscTwakExchange(twak, chain_id=settings.chain_id, chain_key=settings.chain_key,
                               use_limit_orders=not settings.swap_fallback, slippage_pct=settings.slippage_pct)
    signals = CmcSignals(X402Payments(twak, max_payment=settings.x402_max_payment,
                                      prefer_network=settings.chain_key), base_url=settings.cmc_base_url, api_key=settings.cmc_api_key)
    chain = _live_chain(twak)
    allowlist = Allowlist(symbols=load_allowlist())
    breaker, profit_guard, account_guard = _build_guards()
    loop = LearningLoop(exchange, signals, chain, allowlist, breaker, profit_guard,
                        recenter_interval=settings.recenter_interval_s, account_guard=account_guard)
    service = GridService(loop, venue=Venue.BSC_TWAK)
    print(f"=== Gridora LIVE — {market} on BSC chainId {settings.chain_id} via TWAK ({settings.chain_key}) ===")
    res = await service.launch(market, preset, margin)
    print(f"launched {res.instance_id}: {res.orders_placed} orders, commit {res.commit_tx}")
    print(f"rationale: {res.rationale}")
    await loop.supervise(res.instance_id)  # runs until interrupted


def _build_brain(kind: str):
    """Brain LLM: 'claude' = the LOCAL Claude Code CLI (subscription, no API key);
    'fake' = deterministic offline stand-in."""
    from .agent.brain import ClaudeBrain, ClaudeCodeCLI, FakeLLM
    llm = ClaudeCodeCLI(model="claude-sonnet-4-6") if kind == "claude" else FakeLLM()
    return ClaudeBrain(llm)


async def _dry_simulate(sup, base_mid: Decimal) -> None:
    """Drive the FakeExchange through a realistic market cycle so the brain visibly
    adapts in the TUI: ranging -> uptrend -> selloff -> recovery -> calm, looping. Each
    phase oscillates enough for the grid to complete round-trips (bank spread), and the
    selloff is bounded by the trend-stop instead of catching a falling knife."""
    ex, signals = sup.exchange, sup.signals
    # Each phase OSCILLATES around its drift (real markets wiggle; a grid banks the
    # wiggles). Pure one-way legs make a symmetric grid open the wrong side and stop out,
    # so trend legs drift while still oscillating — trend modes buy dips / sell rips.
    phases = [
        ("ranging",  52, "0.05", ["0.98", "1.0", "1.02", "1.0", "0.98", "1.0", "1.02", "1.0"]),
        ("uptrend",  62, "0.40", ["1.01", "1.04", "1.02", "1.05", "1.03", "1.06", "1.04", "1.07"]),
        ("selloff",  36, "-0.4", ["1.04", "1.01", "1.03", "0.99", "1.01", "0.97", "0.99", "0.96"]),
        ("recovery", 50, "0.35", ["0.97", "1.0", "0.98", "1.02", "1.0", "1.03", "1.01", "1.04"]),
        ("calm",     50, "0.0",  ["1.02", "1.0", "1.01", "0.99", "1.0", "1.01", "0.99", "1.0"]),
    ]
    while True:
        for label, fg, mom, mults in phases:
            signals.fear_greed, signals.momentum = fg, Decimal(mom)
            sup.state.add_log(f"market phase: {label} (F&G {fg}, mom {mom})")
            await sup.refresh_regime()
            if sup.autopilot:                 # operator can pause auto-switching ('a')
                await sup._reap_if_halted()
                rl = sup.state.regime.label if sup.state.regime else None
                if rl != sup._last_label or sup.engine is None:  # #6: decide on regime change
                    sup._last_label = rl
                    await sup.decide_and_apply()
            for m in mults:
                px = base_mid * Decimal(m)
                fills = await ex.move_price(sup.state.market, px)
                if sup.engine is not None:
                    for f in fills:
                        await sup.engine.handle_fill(f)
                    await sup.engine.maybe_recenter()
                    await sup._reap_if_halted()   # a mid-phase self-exit clears the engine
                    if sup.engine is not None:
                        sup.state.snapshot_engine(sup.engine, px)
                await asyncio.sleep(1.5)


async def run_agentic(mode: str, market: str, margin: Decimal, brain_kind: str,
                      auto_select: bool = False, serve_port: int | None = None,
                      headless: bool = False) -> None:
    """Claude-routed agent (the agentic path) behind the Textual TUI and/or the web
    console (--serve exposes the control API; headless = no TUI, web cockpit only)."""
    from .agent.state import GridoraState
    from .agent.supervisor import AgentSupervisor
    from .tui.app import GridoraTUI

    breaker, profit_guard, account_guard = _build_guards()
    allowlist = Allowlist(symbols=load_allowlist())
    brain = _build_brain(brain_kind)
    state = GridoraState(market=market, mode=mode, chain_id=settings.chain_id,
                         agent_address=settings.agent_address or "0xGRIDORA_AGENT")
    simulate = None
    exchange_for = None   # per-market exchange factory (set for paper/live → enables --auto rotation)

    if mode == "dry":
        base_mid = Decimal(_DRY_MID.get(market, "100"))
        exchange = FakeExchange({"mid": str(base_mid), "tick": "0.0001", "step": "0.0001",
                                 "min_order_size": "0.001", "equity": str(margin)})
        signals = FakeSignals(fear_greed=52, momentum="0.02")
        chain = MemoryChain(agent=state.agent_address)
        state.venue = "FAKE"
        venue = Venue.FAKE

        async def simulate(sup):  # noqa: ANN001 — local closure
            await _dry_simulate(sup, base_mid)
    elif mode == "paper":
        from .adapters.exchanges.paper import PaperExchange, coingecko_price_fn
        from .adapters.signals.coingecko import CoinGeckoSignals
        from .adapters.store.sqlite_store import SqliteStore
        signals = CoinGeckoSignals()
        chain = MemoryChain(agent=state.agent_address)
        _store = SqliteStore("gridora_paper.db")

        def exchange_for(mkt):  # one PaperExchange per market → --auto can rotate the allowlist
            return PaperExchange(mkt, price_fn=coingecko_price_fn(signals, mkt),
                                 config={"tick": "0.00000001", "step": "0.00000001",
                                         "min_order_size": "0.000001", "equity": str(margin)},
                                 poll_interval=settings.recenter_interval_s or 10.0, store=_store)
        exchange = exchange_for(market)
        state.venue = "PAPER"
        venue = Venue.FAKE
    else:
        settings.guard()
        settings.assert_twak_creds()   # fail before any on-chain commit / signing if creds are missing
        from .adapters.exchanges.bsc_twak.adapter import BscTwakExchange
        from .adapters.exchanges.bsc_twak.twak_client import TwakClient
        from .adapters.payments.x402 import X402Payments
        from .adapters.signals.cmc import CmcSignals
        twak = TwakClient(chain_key=settings.chain_key)

        def exchange_for(mkt):  # one exchange per market, sharing the one TWAK wallet → --auto can rotate
            return BscTwakExchange(twak, chain_id=settings.chain_id, chain_key=settings.chain_key,
                                   use_limit_orders=not settings.swap_fallback, slippage_pct=settings.slippage_pct)
        exchange = exchange_for(market)
        signals = CmcSignals(X402Payments(twak, max_payment=settings.x402_max_payment,
                                          prefer_network=settings.chain_key), base_url=settings.cmc_base_url, api_key=settings.cmc_api_key)
        chain = _live_chain(twak)
        state.venue = "BSC_TWAK"
        venue = Venue.BSC_TWAK

    sup = AgentSupervisor(
        exchange, signals, chain, brain, state, breaker, profit_guard, allowlist, margin,
        venue=venue, agent_address=state.agent_address, account_guard=account_guard,
        # paper/live need a periodic monitor (recenter + guard checks); dry drives it via the sim.
        recenter_interval=(settings.recenter_interval_s or (15.0 if mode in ("paper", "live") else 0.0)),
        auto_select=auto_select, exchange_for=exchange_for)   # factory enables token rotation across the allowlist   # paper/dry exchange is bound to one market

    if serve_port:
        from .control_api import ControlServer
        api = ControlServer(sup, asyncio.get_running_loop(),
                            host=settings.control_host, port=serve_port,
                            owners=settings.owner_address,
                            cors_origin=settings.control_cors_origin)
        api.start()
        lock = "" if settings.owner_address else " (GRIDORA_OWNER allowlist empty — read-only)"
        state.add_log(f"control API on http://{settings.control_host}:{serve_port}{lock}")

    if headless:
        # Web-console-only cockpit: same lifecycle the TUI drives, minus Textual.
        if simulate is not None:
            await sup.boot()
            await simulate(sup)
        else:
            await sup.run_forever(decide_interval=60.0)
    else:
        await GridoraTUI(sup, simulate=simulate).run_async()


def _paper_adapters(margin: Decimal):
    """Paper-trade wiring: FREE real data (CoinGecko) + simulated PaperExchange + MemoryChain
    + SQLite record. No TWAK, no on-chain, no real money. Returns (signals, chain, store,
    exchange_for) — exchange_for(market) makes one PaperExchange per market."""
    from .adapters.exchanges.paper import PaperExchange, coingecko_price_fn
    from .adapters.signals.coingecko import CoinGeckoSignals
    from .adapters.store.sqlite_store import SqliteStore
    signals = CoinGeckoSignals()
    chain = MemoryChain(agent=settings.agent_address or "0xGRIDORA_AGENT")
    store = SqliteStore("gridora_paper.db")
    poll = settings.recenter_interval_s or 10.0

    def exchange_for(market):  # one PaperExchange per market, all on the free CoinGecko feed
        return PaperExchange(market, price_fn=coingecko_price_fn(signals, market),
                             config={"tick": "0.00000001", "step": "0.00000001",
                                     "min_order_size": "0.000001", "equity": str(margin)},
                             poll_interval=poll, store=store)
    return signals, chain, store, exchange_for


async def run_portfolio(mode: str, margin: Decimal, n_markets: int) -> None:
    """Multi-market portfolio: select N liquid names from the allowlist via live CMC
    liquidity+momentum, grid each with its OWN exchange + guards, under one portfolio
    account kill-switch. The data-driven competition shape (vs one hardcoded pair)."""
    from .app.portfolio import Portfolio

    allowlist = Allowlist(symbols=load_allowlist())
    if mode == "dry":
        signals = FakeSignals(fear_greed=52, momentum="0.02")
        chain = MemoryChain(agent=settings.agent_address or "0xGRIDORA_AGENT")
        venue = Venue.FAKE

        def exchange_for(market):  # local FakeExchange factory — one per market
            return FakeExchange({"mid": str(_DRY_MID.get(market, "100")), "tick": "0.0001",
                                 "step": "0.0001", "min_order_size": "0.001", "equity": str(margin)})
    elif mode == "paper":
        signals, chain, _store, exchange_for = _paper_adapters(margin)
        venue = Venue.FAKE
    else:
        settings.guard()
        settings.assert_twak_creds()
        from .adapters.exchanges.bsc_twak.adapter import BscTwakExchange
        from .adapters.exchanges.bsc_twak.twak_client import TwakClient
        from .adapters.payments.x402 import X402Payments
        from .adapters.signals.cmc import CmcSignals
        twak = TwakClient(chain_key=settings.chain_key)
        signals = CmcSignals(X402Payments(twak, max_payment=settings.x402_max_payment,
                                          prefer_network=settings.chain_key),
                             base_url=settings.cmc_base_url, api_key=settings.cmc_api_key)
        chain = _live_chain(twak)
        venue = Venue.BSC_TWAK

        def exchange_for(market):  # one exchange per market, sharing the one TWAK wallet
            return BscTwakExchange(twak, chain_id=settings.chain_id, chain_key=settings.chain_key,
                                   use_limit_orders=not settings.swap_fallback,
                                   slippage_pct=settings.slippage_pct)

    pf = Portfolio(exchange_for, signals, chain, allowlist, margin, venue=venue, quote="USDT",
                   n_markets=n_markets, recenter_interval=settings.recenter_interval_s,
                   agent_address=settings.agent_address or "0xGRIDORA_AGENT")
    print(f"=== Gridora PORTFOLIO — {mode} | margin {margin} across up to {n_markets} liquid markets ===")
    picks = await pf.launch()
    for p in picks:
        print(f"  {p.market:<12} bias {p.bias:+d}  weight {float(p.weight):.2f}  "
              f"7d {p.pct_7d:+.1f}%  vol ${float(p.vol_24h_usd) / 1e6:.0f}M")
    if mode == "dry":
        await pf.close_all()
        print("(dry) launched + closed — use `python -m scripts.backtest` for a simulated portfolio P&L.")
    else:
        print("running — Ctrl-C to stop (flattens + books every grid)…")
        try:
            await pf.supervise()                       # until Ctrl-C / the portfolio guard trips
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nstopping — flattening + booking all grids…")
        try:
            await asyncio.wait_for(pf.close_all(), timeout=20)   # graceful finalize
        except Exception:  # noqa: BLE001 — best-effort under shutdown
            pass
        print(f"=== PORTFOLIO STOPPED — realized {float(pf.total_realized):+.3f} quote "
              f"({float(pf.total_realized / margin * 100):+.2f}%) across {len(picks)} grids ===")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Gridora adaptive-grid agent")
    ap.add_argument("--mode", choices=["dry", "live", "paper"], default="dry",
                    help="dry=offline sim · paper=REAL prices + simulated fills (no money) · live=real TWAK")
    ap.add_argument("--market", default="CAKE/USDT")
    ap.add_argument("--portfolio", action="store_true",
                    help="multi-market: select N liquid names from the allowlist + grid each")
    ap.add_argument("--markets", type=int, default=4, help="portfolio size (with --portfolio)")
    ap.add_argument("--preset", default=None, choices=[p.value for p in RiskPreset])
    ap.add_argument("--margin", default=None, help="quote (stablecoin) margin to deploy")
    ap.add_argument("--ui", action="store_true", help="launch the Textual TUI (agentic, Claude-routed)")
    ap.add_argument("--serve", type=int, nargs="?", const=8317, default=None, metavar="PORT",
                    help="expose the web control API (default port 8317) for the /console "
                         "cockpit; without --ui the agent runs headless behind it")
    ap.add_argument("--auto", action="store_true",
                    help="auto-select the focus token from the allowlist by liquidity+momentum (agentic)")
    ap.add_argument("--brain", choices=["fake", "claude"], default=None,
                    help="strategy router: 'claude' = local Claude Code, 'fake' = offline. "
                         "default: dry->fake, live->claude")
    args = ap.parse_args()

    preset_name = (args.preset or settings.preset or "BALANCED").upper()
    try:
        preset = RiskPreset(preset_name)
    except ValueError:
        raise SystemExit(f"invalid preset {preset_name!r}; choose one of {[p.value for p in RiskPreset]}")
    try:
        margin = Decimal(args.margin or settings.quote_margin)
    except InvalidOperation:
        raise SystemExit(f"invalid --margin / GRIDORA_QUOTE_MARGIN: {args.margin or settings.quote_margin!r}")
    if margin <= 0:
        raise SystemExit("margin must be > 0 (quote/stablecoin units to deploy)")
    brain_kind = args.brain or ("claude" if args.mode == "live" else "fake")  # dry/paper offline, live=Claude

    if args.portfolio:
        await run_portfolio(args.mode, margin, args.markets)
    elif args.ui or args.serve is not None:
        await run_agentic(args.mode, args.market, margin, brain_kind, auto_select=args.auto,
                          serve_port=args.serve, headless=not args.ui)
    elif args.mode == "dry":
        await run_dry(args.market, preset, margin)
    elif args.mode == "paper":
        await run_agentic("paper", args.market, margin, brain_kind, auto_select=args.auto)   # paper → live TUI; --auto rotates tokens
    else:
        await run_live(args.market, preset, margin)


if __name__ == "__main__":
    asyncio.run(main())
