"""AgentSupervisor — the agentic control loop: brain decides, engine + guardrails execute.

Each cycle: refresh the CMC regime, ask the brain to route (launch/switch/tune/halt),
apply it — commit config on-chain BEFORE trading, attest AFTER. Guardrails hard-enforce
risk regardless of the brain. The DECIDE step is Claude, not a fixed classifier.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Callable, Optional

from ..app.engine import GridEngine
from ..app.safety import AccountGuard, Allowlist, CircuitBreaker, ProfitGuard
from ..domain.grid import vol_half_band
from ..domain.models import GridConfig, GridState, MemoryRecord, Venue
from ..domain.ports import ChainPort, ExchangePort, SignalPort
from ..domain.universe import select_markets
from .brain import AgentDecision, ClaudeBrain
from .loop import new_instance_id
from .state import GridoraState
from .strategies import STRATEGIES, Strategy, build_config, with_overrides


class AgentSupervisor:
    def __init__(
        self,
        exchange: ExchangePort,
        signals: SignalPort,
        chain: ChainPort,
        brain: ClaudeBrain,
        state: GridoraState,
        breaker: CircuitBreaker,
        profit_guard: ProfitGuard,
        allowlist: Allowlist,
        margin: Decimal,
        venue: Venue = Venue.FAKE,
        agent_address: str = "",
        account_guard: AccountGuard | None = None,
        registry: dict[str, Strategy] | None = None,
        recenter_interval: float = 0.0,
        trend_stop_frac: Decimal = Decimal("0.05"),
        auto_select: bool = False,   # re-pick the focus market from the live universe while flat
        exchange_for: Optional[Callable[[str], ExchangePort]] = None,  # per-market exchange factory (enables token rotation)
    ) -> None:
        self.exchange = exchange
        self.signals = signals
        self.chain = chain
        self.brain = brain
        self.state = state
        self.breaker = breaker
        self.profit_guard = profit_guard
        self.allowlist = allowlist
        self.margin = margin
        self.venue = venue
        self.agent_address = agent_address or "0xGRIDORA_AGENT"
        self.account_guard = account_guard
        self.registry = registry or STRATEGIES
        self.recenter_interval = recenter_interval
        self.trend_stop_frac = trend_stop_frac
        self.auto_select = auto_select
        self.exchange_for = exchange_for
        self.engine: Optional[GridEngine] = None
        self.cfg: Optional[GridConfig] = None
        self._seq = 0
        self._last_label: Optional[str] = None  # last regime label decided on (#6 gate)
        self._focus_range = 0.0                  # focus token's 24h range %, sizes the band (0 = preset)
        self._flat_ticks = 0                     # cycles spent flat (throttles re-decide while flat)
        self.state.start_equity = margin          # starting capital for the account view
        self.autopilot = True  # operator can pause autonomous decisions (TUI 'a')
        self.state.autopilot = True
        self.state.strategies = {k: s.enabled for k, s in self.registry.items()}

    async def boot(self) -> AgentDecision:
        """Register identity (best-effort), pick the focus token, sense, make the first call."""
        try:
            self.state.identity_tx = await self.chain.register_identity(self.agent_address)
        except Exception as e:  # noqa: BLE001 — identity is best-effort for the demo
            self.state.add_log(f"identity register skipped: {e}")
        if self.auto_select:
            await self._select_focus()
        await self.refresh_regime()
        return await self.decide_and_apply()

    async def _select_focus(self) -> None:
        """Pick the single best LIQUID, relative-strength token from the WHOLE allowlist and
        focus on it — so the agent always trades the best eligible name, re-picking each time
        it's flat (it rotates across the allowlist as momentum shifts). Only acts when flat
        (never yanks a live grid). With an exchange factory, also swaps to that market's
        exchange. No-op if no liquid candidate or the metrics feed is down."""
        if self.engine is not None:
            return
        try:
            metrics = await self.signals.market_metrics(sorted(self.allowlist.symbols))
            picks = select_markets(metrics, self.allowlist.symbols, n=1)
        except Exception as e:  # noqa: BLE001 — never brick on a metrics hiccup
            self.state.add_log(f"market metrics unavailable ({e}) — keeping {self.state.market}")
            return
        if not picks:
            self.state.add_log("no liquid eligible token right now — holding")
            return
        self._focus_range = picks[0].range_24h_pct   # size the next grid's band to its volatility
        market = picks[0].market
        if market != self.state.market:
            self.state.add_log(f"focus → {market} (7d {picks[0].pct_7d:+.1f}%, "
                               f"vol ${float(picks[0].vol_24h_usd) / 1e6:.0f}M)")
        self.state.market = market
        if self.exchange_for is not None:
            self.exchange = self.exchange_for(market)   # swap to the new market's exchange instance

    async def refresh_regime(self) -> None:
        self.state.regime = await self.signals.snapshot(self.state.market)

    async def decide_and_apply(self) -> AgentDecision:
        """One control cycle: reap any self-exited grid → snapshot → brain decides →
        apply (launch/switch/halt)."""
        await self._reap_if_halted()
        if self.state.regime is not None and getattr(self.state.regime, "stale", False):
            self.state.add_log("regime feed stale (CMC/x402 down) — holding, not trading on blind data")
            hold = AgentDecision(strategy_key=self.state.active_strategy or "flat",
                                 reasoning="regime feed unavailable — hold", confidence="LOW", source="guard")
            self.state.record_decision(hold)
            return hold
        if self.engine is not None:
            self.state.snapshot_engine(self.engine, await self._mid())
        decision = await self.brain.decide(self.state)
        self.state.record_decision(decision)
        await self._apply(decision)
        return decision

    async def _apply(self, decision: AgentDecision) -> None:
        strat = self.registry.get(decision.strategy_key)
        if strat is None or not strat.enabled:
            self.state.add_log(f"decision {decision.strategy_key} not available — ignored")
            return
        if strat.is_flat:
            await self._halt("brain chose FLAT (sit in stablecoin)")
            return
        # Same strategy already running. With no tuning overrides, let the engine's monitor
        # keep adapting (no churn). If the brain asked to re-shape band/levels/deploy, fall
        # through and relaunch so the tuning takes effect (it was previously dropped silently).
        if self.engine is not None and self.cfg is not None and self.cfg.policy_version == f"v0:{strat.key}":
            if decision.half_band is None and decision.levels is None and decision.deploy_frac is None:
                self.state.status = f"running {strat.key}"
                return
        # Switch: book+attest the old episode. CARRY its inventory into the new grid only
        # when the new regime can hold it (don't drag a long into a downtrend — flatten it
        # at the switch, near the top, instead of riding it into the drop).
        new_bias = decision.bias if decision.bias is not None else strat.bias
        carried = (await self._close_current(carry=self._carry_compatible(new_bias))
                   if self.engine is not None else None)
        # Band: the brain's explicit half_band wins; otherwise size to the focus token's
        # daily range (capped by the strategy's half_band), so a flat token gets a tight grid.
        half_band = decision.half_band
        if half_band is None and self._focus_range > 0:
            half_band = vol_half_band(self._focus_range, strat.half_band)
        await self._launch(with_overrides(
            strat, half_band=half_band, levels=decision.levels,
            deploy_frac=decision.deploy_frac, bias=decision.bias), carried=carried)

    def _carry_compatible(self, new_bias: int) -> bool:
        """Carry inventory across a switch only if the new strategy's lean can hold it.
        A long is fine into a neutral/long grid (it works off via reducers), but carrying
        it into a short-biased grid means riding it down — flatten at the switch instead."""
        if self.engine is None:
            return False
        net = self.engine.net_inventory()
        if net == 0:
            return False
        if net > 0 and new_bias < 0:
            return False
        if net < 0 and new_bias > 0:
            return False
        return True

    async def _launch(self, strat: Strategy, carried: tuple[Decimal, Decimal] | None = None) -> None:
        mid = await self._mid()
        self._seq += 1
        iid = new_instance_id(self.state.market, self._seq)
        cfg = build_config(strat, iid, self.state.market, mid, self.margin, self.venue)
        self.allowlist.assert_permits(cfg.market)              # reject off-list BEFORE commit
        if self.breaker.max_inventory <= 0:
            self.breaker.max_inventory = cfg.order_size * cfg.levels * 3
        self.breaker.reset()                                   # fresh per-episode guard state
        self.profit_guard.reset()
        self.state.commit_tx = await self.chain.commit_strategy(iid, cfg)  # pin hash BEFORE trading
        self.engine = GridEngine(
            self.exchange, cfg, breaker=self.breaker, profit_guard=self.profit_guard,
            account_guard=self.account_guard, monitor_interval=self.recenter_interval,
            trend_stop_frac=self.trend_stop_frac)
        self.cfg = cfg
        if carried is not None:
            self.engine.seed_position(*carried)                # inherit inventory BEFORE laying the grid
        orders = await self.engine.start()
        self.state.is_running = True
        self.state.status = f"running {strat.key}"
        self.state.snapshot_engine(self.engine, mid)
        carry_note = f", carried {carried[0]:+} base" if carried is not None else ""
        self.state.add_log(
            f"launched {strat.key}: {len(orders)} orders, commit {self.state.commit_tx[:14]}…{carry_note}")

    async def _close_current(self, carry: bool = False) -> tuple[Decimal, Decimal] | None:
        """Stop the live episode, book+attest it, clear it. With carry=True (a switch)
        the open inventory is marked to market and returned for the next grid to inherit
        rather than market-flattened."""
        if self.engine is None or self.cfg is None:
            return None
        if carry:
            carried = await self.engine.stop_carry()
        else:
            await self.engine.stop()
            carried = None
        await self._finalize()
        return carried

    async def _reap_if_halted(self) -> None:
        """A grid can self-exit (breaker / profit-lock / trend-stop) between cycles. Book
        + clear it so the supervisor doesn't think a halted engine is still running."""
        if self.engine is None or self.engine.state is not GridState.HALTED:
            return
        self.state.add_log(f"grid self-exited: {self.engine.exit_reason or self.engine.exit_kind or 'halted'}")
        await self._finalize()
        self.state.is_running = False
        self.state.active_strategy = "flat"
        self.state.status = "flat (auto-exit)"

    async def _finalize(self) -> None:
        """Book the current engine's outcome on-chain + memory, then clear it."""
        if self.engine is None or self.cfg is None:
            return
        outcome = self.engine.outcome()
        self.state.note_close(self.engine.realized)
        self.state.record_episode(  # roll into the cumulative account view + history
            self.cfg.policy_version.split(":")[-1], outcome.pnl_bps, self.engine.realized)
        try:
            self.state.attest_tx = await self.chain.attest(self.cfg.instance_id, outcome)
            self.state.journal_count += 1
            self.state.add_log(f"attested {self.cfg.instance_id[-12:]}: {outcome.pnl_bps}bps")
        except Exception as e:  # noqa: BLE001
            self.state.add_log(f"attest failed: {e}")
        try:
            label = self.state.regime.label if self.state.regime else "RANGING"
            await self.chain.write_memory(MemoryRecord(label, self.cfg, outcome, meta={}))
        except Exception:  # noqa: BLE001
            pass
        self.engine, self.cfg = None, None

    async def force_strategy(self, key: str, *, half_band: Decimal | None = None,
                             levels: int | None = None, deploy_frac: Decimal | None = None,
                             bias: int | None = None) -> None:
        """Operator override from the TUI: switch to `key`, relaunching even if it's the
        SAME strategy so band/level/bias tweaks take effect. Guardrails still clamp the
        overrides and hard-enforce risk — this only chooses, it can't break a limit."""
        strat = self.registry.get(key)
        decision = AgentDecision(
            strategy_key=key, half_band=half_band, levels=levels, deploy_frac=deploy_frac,
            bias=bias, reasoning="operator override (TUI)", confidence="HIGH", source="operator")
        self.state.record_decision(decision)
        if strat is None or not strat.enabled:
            self.state.add_log(f"operator: {key} unavailable")
            return
        if strat.is_flat:
            await self._halt("operator chose FLAT")
            return
        new_bias = bias if bias is not None else strat.bias
        carried = (await self._close_current(carry=self._carry_compatible(new_bias))
                   if self.engine is not None else None)
        await self._launch(with_overrides(
            strat, half_band=half_band, levels=levels, deploy_frac=deploy_frac, bias=bias),
            carried=carried)

    def set_autopilot(self, on: bool) -> None:
        self.autopilot = on
        self.state.autopilot = on
        self.state.add_log(f"operator: autopilot {'ON' if on else 'OFF — manual'}")

    async def halt(self, why: str = "manual kill") -> None:
        """Operator/keybinding entry point to flatten + sit in stablecoin."""
        await self._halt(why)

    async def _halt(self, why: str) -> None:
        if self.engine is not None:
            await self._close_current()
        self.state.is_running = False
        self.state.status = "flat"
        self.state.active_strategy = "flat"
        self.state.add_log(f"HALT → flat: {why}")

    async def _mid(self) -> Decimal:
        bid, ask = await self.exchange.best_bid_ask(self.state.market)
        return (bid + ask) / 2

    # ---- live control loop ----

    async def _drive(self, engine: GridEngine) -> None:
        """Run ONE engine's fill consumer + re-center monitor until cancelled (on a
        switch/halt) or both finish. Tied to this engine instance so a strategy switch
        doesn't leave the loop parked in the old engine's consume()."""
        await asyncio.gather(engine.consume(), engine.monitor(), return_exceptions=True)

    async def run_forever(self, decide_interval: float = 60.0) -> None:
        """Live: run the engine's fill consumer + re-center monitor, and re-decide on a
        cadence. The brain can switch/halt the grid between decisions."""
        await self.boot()
        async def decide_loop():
            while True:  # keep ticking so toggling autopilot back ON resumes decisions
                await asyncio.sleep(decide_interval)
                if not self.autopilot:
                    await self.refresh_regime()      # operator is driving — observe, don't switch
                    continue
                await self._reap_if_halted()
                if self.engine is None and self.auto_select:
                    await self._select_focus()       # FLAT + auto → re-pick the best token (rotates the allowlist)
                await self.refresh_regime()          # regime for the current / just-picked focus market
                # Re-decide only on a regime LABEL change (a working grid keeps banking
                # spreads); when flat, re-enter on a change or every 5th idle cycle.
                label = self.state.regime.label if self.state.regime else None
                if self.engine is not None:
                    self._flat_ticks = 0
                    if label == self._last_label:
                        continue
                else:
                    self._flat_ticks += 1
                    if label == self._last_label and self._flat_ticks % 5 != 0:
                        continue
                self._last_label = label
                try:
                    await self.decide_and_apply()
                except Exception as e:  # noqa: BLE001 — a bad cycle must not kill the agent
                    self.state.add_log(f"decide cycle failed: {e}")
        async def engine_loop():
            current = None    # the engine instance we're currently driving
            task = None       # its consume+monitor task
            try:
                while True:
                    if self.engine is not current:        # launched / switched / reaped
                        if task is not None:
                            task.cancel()
                            try:
                                await task
                            except asyncio.CancelledError:
                                pass
                        current = self.engine
                        task = asyncio.ensure_future(self._drive(current)) if current is not None else None
                    await asyncio.sleep(1.0)
            finally:
                if task is not None:
                    task.cancel()
        await asyncio.gather(decide_loop(), engine_loop())
