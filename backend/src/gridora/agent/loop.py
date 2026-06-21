"""The Verifiable Learning Loop.

plan_and_launch:  SENSE -> RECALL -> DECIDE -> COMMIT(on-chain) -> EXECUTE
close_and_learn:  flatten -> ATTEST(on-chain) -> write StrategyMemory (LEARN)

The chain is the hinge: recall reads verified experience, attestation makes the
record auditable, the same records train the policy. ADAPTED FROM perps-agent
agent/loop.py — slimmed to a single-instance agent over Gridora's CMC regime.

For Gridora the wiring is:
  - exchange = BscTwakExchange (signs via TWAK)   [FakeExchange in --mode dry]
  - signals  = CmcSignals (CMC Agent Hub, x402)   [FakeSignals in --mode dry]
  - chain    = BscChain (Identity/Journal/Ledger)  [MemoryChain in --mode dry]
  - breaker  = CircuitBreaker tuned for the 30% DQ (hard halt well before it)
  - allowlist guard: reject any market not in the 149-token list BEFORE COMMIT
"""
from __future__ import annotations

import time
from decimal import Decimal

from ..app.engine import GridEngine
from ..app.safety import AccountGuard, Allowlist, CircuitBreaker, ProfitGuard
from ..domain.models import GridConfig, MemoryRecord, RegimeFingerprint
from ..domain.ports import ChainPort, ExchangePort, SignalPort


class LearningLoop:
    def __init__(
        self,
        exchange: ExchangePort,
        signals: SignalPort,
        chain: ChainPort,
        allowlist: Allowlist,
        breaker: CircuitBreaker,
        profit_guard: ProfitGuard,
        recenter_interval: float = 0.0,
        account_guard: AccountGuard | None = None,
        store=None,
    ) -> None:
        self.exchange = exchange
        self.signals = signals
        self.chain = chain
        self.allowlist = allowlist
        self.breaker = breaker
        self.profit_guard = profit_guard
        self.recenter_interval = recenter_interval
        self.account_guard = account_guard
        self.store = store
        self._engines: dict[str, GridEngine] = {}
        self._regimes: dict[str, RegimeFingerprint] = {}
        self._rationales: dict[str, str] = {}
        self.last_attest_ok: bool = False

    def engine(self, instance_id: str) -> GridEngine:
        eng = self._engines.get(instance_id)
        if eng is None:
            raise KeyError(instance_id)
        return eng

    def rationale(self, instance_id: str) -> str:
        return self._rationales.get(instance_id, "")

    async def plan_and_launch(self, cfg: GridConfig) -> dict:
        """SENSE -> RECALL -> DECIDE -> COMMIT(on-chain) -> EXECUTE.

        The allowlist + commit happen BEFORE any order is placed: a market outside the
        149-token set never trades, and the strategy hash is pinned on-chain first so
        the run is verifiable after the fact."""
        self.allowlist.assert_permits(cfg.market)  # reject off-list markets BEFORE commit

        # SENSE — read the CMC regime fingerprint for this market.
        regime = await self.signals.snapshot(cfg.market)
        if getattr(regime, "stale", False):  # no live data → don't trade on a synthesized neutral
            raise RuntimeError(
                f"regime feed unavailable for {cfg.market} (CMC/x402 down) — refusing to launch "
                f"on synthesized neutral data; retry when the feed is back")

        # Derive the per-level base size from the stablecoin knob, if not pre-sized.
        bid, ask = await self.exchange.best_bid_ask(cfg.market)
        mid = (bid + ask) / 2
        if cfg.order_size <= 0 and cfg.quote_per_grid > 0 and mid > 0:
            cfg.order_size = cfg.quote_per_grid / mid

        # RECALL — best verified episodes for this regime (ranked risk-adjusted).
        recalled = list(await self.chain.recall(regime, k=5))

        # DECIDE — lean the grid with the regime; adopt recalled shape when available.
        cfg.bias = regime.bias
        if recalled:
            self._apply_recall(cfg, recalled[0])
        rationale = self._explain(regime, cfg, recalled)
        self._rationales[cfg.instance_id] = rationale

        # Default the inventory cap to 3x the nominal one-sided notional if unset.
        if self.breaker.max_inventory <= 0:
            self.breaker.max_inventory = cfg.order_size * cfg.levels * 3

        # COMMIT — pin the config hash on-chain BEFORE trading.
        commit_tx = await self.chain.commit_strategy(cfg.instance_id, cfg)

        async def bias_fn(_current: int) -> int:
            """Re-read the regime on each re-center so the grid morphs ranging<->trend
            mid-episode (a dead/stale signal keeps the current bias)."""
            live = await self.signals.snapshot(cfg.market)
            if getattr(live, "stale", False):
                return _current   # feed down → freeze bias, don't re-shape on blind data
            return live.bias

        # EXECUTE — place the grid through the ExchangePort (TWAK signs in live mode).
        engine = GridEngine(
            self.exchange, cfg, store=self.store, breaker=self.breaker,
            monitor_interval=self.recenter_interval, profit_guard=self.profit_guard,
            account_guard=self.account_guard, bias_fn=bias_fn,
        )
        orders = await engine.start()
        self._engines[cfg.instance_id] = engine
        self._regimes[cfg.instance_id] = regime
        if self.store is not None:
            await self.store.save_instance(cfg)
        return {
            "instance_id": cfg.instance_id,
            "commit_tx": commit_tx,
            "orders_placed": len(orders),
            "regime": regime,
            "bias": cfg.bias,
            "rationale": rationale,
        }

    async def supervise(self, instance_id: str) -> None:
        """Run the engine's fill consumer + re-center monitor until stop. Guarantees
        >=1 trade/day implicitly: resting levels fire as price oscillates."""
        import asyncio

        engine = self.engine(instance_id)
        await asyncio.gather(engine.consume(), engine.monitor())

    async def close_and_learn(self, instance_id: str) -> dict:
        """flatten -> ATTEST(on-chain) -> write StrategyMemory. Closing always
        completes even if one chain write fails; callers report from `last_attest_ok`
        so we never claim 'attested' when the tx didn't land."""
        engine = self.engine(instance_id)
        await engine.stop()
        outcome = engine.outcome()
        regime = self._regimes.get(instance_id)

        attest_tx = ""
        try:
            attest_tx = await self.chain.attest(instance_id, outcome)
            self.last_attest_ok = True
        except Exception as e:  # noqa: BLE001 — keep closing even if a step fails
            self.last_attest_ok = False
            print(f"  ! attest failed: {e}")

        if regime is not None:
            try:
                await self.chain.write_memory(MemoryRecord(
                    regime_label=regime.label, config=engine.cfg, outcome=outcome,
                    meta={"fear_greed": regime.fear_greed, "bias": regime.bias},
                ))
            except Exception as e:  # noqa: BLE001
                print(f"  ! write_memory failed: {e}")
        if self.store is not None:
            await self.store.mark_closed(instance_id)
        return {"outcome": outcome, "attest_tx": attest_tx, "attest_ok": self.last_attest_ok}

    # ---- pure DECIDE helpers ----

    @staticmethod
    def _apply_recall(cfg: GridConfig, best: MemoryRecord) -> None:
        """Adopt the recalled best episode's shape (level count + relative band width)
        re-centered on the current band — reuse what verifiably worked in this regime."""
        b = best.config
        span = b.upper + b.lower
        if span <= 0:
            return
        half = (b.upper - b.lower) / span                     # recalled relative half-band
        center = (cfg.upper + cfg.lower) / 2
        cfg.lower = center * (Decimal(1) - half)
        cfg.upper = center * (Decimal(1) + half)
        cfg.levels = min(b.levels, cfg.max_levels)

    @staticmethod
    def _explain(regime: RegimeFingerprint, cfg: GridConfig, recalled: list) -> str:
        lean = {1: "long-bias (buy ladder)", -1: "short-bias (sell ladder)", 0: "symmetric"}[cfg.bias]
        src = f"recall×{len(recalled)}" if recalled else "defaults"
        return (f"{regime.label} (F&G {regime.fear_greed}, funding {regime.funding_bps}bps, "
                f"mom {regime.momentum}) -> {lean}; band [{cfg.lower:.4f},{cfg.upper:.4f}] "
                f"× {cfg.levels} levels, size {cfg.order_size} ({src})")


def new_instance_id(market: str, seq: int = 0) -> str:
    return f"{market}-{int(time.time() * 1000)}-{seq}"
