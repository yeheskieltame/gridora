"""GridEngine — one supervised grid over an ExchangePort (spot; PnL in the quote).

Places the grid, reacts to fills (pair + accumulate PnL); a `monitor` loop re-centers
to follow price out-of-band and enforces the guards (breaker = inventory/drawdown,
profit guard = TP/trailing, account guard = 30%-DQ kill-switch). Re-center is
inventory-aware (never averages up). Pure math in domain/grid.py; drive from fills.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import time
from decimal import Decimal

from ..domain.grid import (
    _external_id,
    build_grid_orders,
    compute_paired_order,
    levels_for,
    plan_levels,
    quantize,
    quantize_qty,
)
from ..domain.models import EpisodeOutcome, Fill, GridConfig, GridState, Order, Side
from ..domain.pnl import bps
from .safety import AccountGuard, CircuitBreaker, ProfitGuard


class GridEngine:
    _exit_retry_delay = 1.0  # backoff base for emergency-exit retries (tests set 0)
    ACCOUNT_CHECK_EVERY = 4  # monitor ticks between equity reads
    # Re-center only when price leaves the band by this fraction of the band WIDTH, not the
    # instant it touches the edge. Without it, an oscillation whose amplitude ~ the band
    # width thrashes — re-centering on every swing, cancelling/replacing orders and bleeding.
    # 0.10 from the scripts/backtest sweep: kills the thrash without lagging trend-following.
    RECENTER_BUFFER_FRAC = Decimal("0.10")

    def __init__(self, exchange, cfg: GridConfig, store=None, breaker: CircuitBreaker | None = None,
                 monitor_interval: float = 0.0, profit_guard: ProfitGuard | None = None,
                 account_guard: AccountGuard | None = None, bias_fn=None,
                 trend_stop_frac: Decimal = Decimal("0.05")) -> None:
        self.ex = exchange
        self.cfg = cfg
        self.store = store
        self.breaker = breaker
        self.profit_guard = profit_guard
        self.account_guard = account_guard
        self.monitor_interval = monitor_interval  # seconds; <=0 disables re-center/monitor
        # Trend-stop: if price leaves the band on the side that's against our inventory
        # by >= this fraction of avg entry, FLATTEN instead of re-centering — a grid must
        # not average into a sustained trend (catch a falling knife). <=0 disables.
        self.trend_stop_frac = trend_stop_frac
        # Grid mode (see domain/grid.py build_grid_orders): starts at the committed
        # cfg.bias; an optional async `bias_fn(current_bias) -> int` re-evaluates the
        # regime on every re-center, so one episode can flow ranging -> trending.
        self.bias = cfg.bias
        self.bias_fn = bias_fn
        self._guard_tick = 0
        self.state = GridState.INITIALIZING
        self._halted = asyncio.Event()  # set when the grid leaves RUNNING, to wake consume()
        self.levels: list[Decimal] = []
        self.realized = Decimal(0)
        self.fill_count = 0
        self.closes = 0
        self.wins = 0
        self.pos_qty = Decimal(0)  # SIGNED net position (>0 long, <0 short)
        self.pos_avg = Decimal(0)  # average entry of the current position (0 when flat)
        self._nonce = 0
        self._oid = 0
        self._fills: list[Fill] = []
        self._resting: dict[str, Order] = {}
        # re-center state (live band; cfg stays the committed/original band)
        self._gen = 0
        self.tick = Decimal(0)
        self.center = Decimal(0)
        self.lower = cfg.lower
        self.upper = cfg.upper
        self._lower_ratio = Decimal(1)
        self._upper_ratio = Decimal(1)
        # outcome accounting: deployed capital + PnL high-water for drawdown bps
        self.deployed = Decimal(0)
        self.peak_pnl = Decimal(0)
        self.max_dd_quote = Decimal(0)
        self.exit_reason = ""
        self.exit_kind = ""

    async def start(self) -> list[Order]:
        meta = await self.ex.market_meta(self.cfg.market)
        self.tick = meta.tick
        # Align order size to the venue qty step + min, or every order is rejected.
        self.cfg.order_size = quantize_qty(self.cfg.order_size, meta.qty_step, meta.min_qty)
        if self.account_guard is not None and self.account_guard.max_drop > 0:
            equity = await self._read_start_equity()
            if equity is not None and equity > 0:
                self.account_guard.arm(equity)
            else:  # a configured kill-switch that can't arm is silent risk — fail closed
                raise RuntimeError(
                    "account guard is armed (max_drop set) but start equity could not be read "
                    "— refusing to launch with the 30%-DQ kill-switch inert")
        bid, ask = await self.ex.best_bid_ask(self.cfg.market)
        mid = (bid + ask) / 2
        self.center = mid
        self.deployed = self.cfg.order_size * self.cfg.levels * mid  # nominal capital at work
        # Auto drawdown cap: tie the breaker's loss limit to this episode's deployed
        # capital (e.g. 12% of it) when it wasn't set to an absolute quote amount.
        if self.breaker is not None and self.breaker.max_drawdown_frac > 0:
            self.breaker.max_drawdown = self.deployed * self.breaker.max_drawdown_frac
        self._lower_ratio = self.cfg.lower / mid  # preserve exact band shape on re-center
        self._upper_ratio = self.cfg.upper / mid
        self.lower, self.upper = self.cfg.lower, self.cfg.upper
        self.levels = [quantize(p, self.tick) for p in plan_levels(self.cfg)]
        orders = build_grid_orders(self.cfg, self.levels, mid, self._gen, bias=self.bias)
        orders = self._inventory_aware(orders, mid)   # shape around any seeded carry (no-op when flat)
        await self._place_many(orders)
        self.state = GridState.RUNNING
        return orders

    async def _place(self, order: Order) -> None:
        """Place one order with a globally-unique external_id (a reused id can be
        rejected by a venue even after cancel). One rejected order is logged, never
        fatal — it must not orphan the grid."""
        if self.state is GridState.HALTED:   # a concurrent fill must not place onto a halted grid
            return
        self._oid += 1
        order.external_id = _external_id(self.cfg.instance_id, order.level, self._oid)
        try:
            await self.ex.place_order(order)
            self._resting[order.external_id] = order
        except Exception as e:  # noqa: BLE001
            print(f"  ! place {order.side.value} L{order.level} @ {order.price} failed: {e}")

    async def _place_many(self, orders: list[Order]) -> None:
        """Stamp unique external_ids on the batch, then place it (venue batch endpoint
        when present, else one-by-one). Tolerant of partial failure."""
        for o in orders:
            self._oid += 1
            o.external_id = _external_id(self.cfg.instance_id, o.level, self._oid)
        batch = getattr(self.ex, "place_orders", None)
        if batch is None:
            for o in orders:
                if self.state is GridState.HALTED:
                    break
                try:
                    await self.ex.place_order(o)
                    self._resting[o.external_id] = o
                except Exception as e:  # noqa: BLE001
                    print(f"  ! place {o.side.value} L{o.level} @ {o.price} failed: {e}")
            return
        if self.state is GridState.HALTED:
            return
        try:
            placed = await batch(orders)
            accepted = [o for o in (placed or orders) if getattr(o, "order_id", None)]
            for o in accepted:
                self._resting[o.external_id] = o
            if orders and not accepted:
                print(f"  ! grid {self.cfg.instance_id}: venue accepted 0/{len(orders)} orders "
                      f"(size={self.cfg.order_size}) — check qty step / min order value")
        except Exception as e:  # noqa: BLE001
            print(f"  ! batch place ({len(orders)} orders) failed: {e}")

    # ---- live adaptation: re-center to follow price ----

    async def maybe_recenter(self) -> bool:
        """One supervisory check: enforce guards on unrealized PnL, then re-center the
        grid if price has left the band. Returns True if re-centered."""
        if self.state is not GridState.RUNNING:
            return False
        bid, ask = await self.ex.best_bid_ask(self.cfg.market)
        mid = (bid + ask) / 2
        await self._check_guards(mid)
        if self.state is not GridState.RUNNING:
            return False
        if await self._account_break(mid):
            return False
        buf = (self.upper - self.lower) * self.RECENTER_BUFFER_FRAC   # hysteresis: don't chase every swing
        if (self.lower - buf) <= mid <= (self.upper + buf):
            return False
        await self._recenter(mid)
        return True

    def _adverse_breakout(self, mark: Decimal) -> Decimal:
        """How far price has broken BEYOND the band on the side that hurts our inventory
        (0 when flat or price is inside the band). Long + price below the band lower, or
        short + price above the band upper. Measuring from the band EDGE (not pos_avg) means
        normal in-band oscillation never trips the trend-stop — only a real breakout does."""
        if self.pos_qty > 0 and self.lower > 0 and mark < self.lower:
            return (self.lower - mark) / self.lower
        if self.pos_qty < 0 and self.upper > 0 and mark > self.upper:
            return (mark - self.upper) / self.upper
        return Decimal(0)

    async def _account_break(self, mark: Decimal) -> bool:
        if self.account_guard is None or self.account_guard.max_drop <= 0:
            return False
        self._guard_tick += 1
        if self._guard_tick % self.ACCOUNT_CHECK_EVERY != 1:
            return False
        try:
            equity = (await self.ex.balance()).equity_quote
        except Exception:  # noqa: BLE001
            return False
        reason = self.account_guard.check(equity)
        if reason:
            await self._exit(reason, "account-guard", mark)
            return True
        return False

    async def _recenter(self, new_mid: Decimal) -> None:
        """Cancel the stale grid and re-lay it around `new_mid`, same band shape.
        Inventory-aware: when net long, never place a BUY above the average entry (no
        averaging up into a pump). Inventory + realized PnL carry over; a fresh
        generation namespaces the new external_ids."""
        self.state = GridState.REBALANCING
        try:
            await self._cancel_own()
            if self.bias_fn is not None:  # dynamic mode: re-read the regime each re-center
                try:
                    new_bias = await self.bias_fn(self.bias)
                    if new_bias != self.bias:
                        print(f"  ~ bias {self.bias:+d} -> {new_bias:+d} (regime shift)")
                        self.bias = new_bias
                except Exception as e:  # noqa: BLE001 — a dead signal must not stop the re-center
                    print(f"  ! bias_fn failed ({e}) — keeping bias {self.bias:+d}")
            self.center = new_mid
            self.lower = new_mid * self._lower_ratio
            self.upper = new_mid * self._upper_ratio
            self.levels = [quantize(p, self.tick)
                           for p in levels_for(self.lower, self.upper, self.cfg.levels, self.cfg.spacing)]
            self._gen += 1
            orders = build_grid_orders(self.cfg, self.levels, new_mid, self._gen, bias=self.bias)
            net = self.net_inventory()
            orders = self._inventory_aware(orders, new_mid)   # shape around carried inventory
            await self._place_many(orders)
            print(f"  ~ recenter -> mid {new_mid} band [{self.lower}, {self.upper}] gen {self._gen} "
                  f"({len(orders)} orders, net_inv {net}, bias {self.bias:+d})")
        finally:
            if self.state is GridState.REBALANCING:  # ALWAYS leave a runnable state
                self.state = GridState.RUNNING

    async def _read_start_equity(self) -> Decimal | None:
        """Read wallet equity to arm the account guard, retrying with backoff. Returns
        None if every attempt fails — start() then refuses to launch rather than running
        with the kill-switch silently inert."""
        delay = self._exit_retry_delay
        for attempt in range(1, 4):
            try:
                eq = (await self.ex.balance()).equity_quote
                if eq > 0:
                    return eq
            except Exception as e:  # noqa: BLE001
                print(f"    start-equity read failed (attempt {attempt}/3): {e}")
            if attempt < 3:
                await asyncio.sleep(delay)
                delay *= 2
        return None

    def _inventory_aware(self, orders: list[Order], mid: Decimal) -> list[Order]:
        """Shape a freshly-built grid around carried inventory: re-arm with-trend
        take-profit reducers, never average into the bag, and clamp the growing side to
        the inventory cap. No-op when flat. Shared by start() (seeded carry) + _recenter."""
        net = self.net_inventory()
        if net == 0:
            return orders
        if self.bias != 0 and (net > 0) == (self.bias > 0):
            # Trend mode holds with-trend inventory: re-arm its take-profit reducers so a
            # biased ladder at the cap never re-lays ZERO orders.
            reduce_side = Side.SELL if net > 0 else Side.BUY
            full = build_grid_orders(self.cfg, self.levels, mid, self._gen)
            reducers = [o for o in full if o.side is reduce_side]
            reducers.sort(key=lambda o: o.price, reverse=(reduce_side is Side.BUY))
            if self.cfg.order_size > 0:
                need = math.ceil(abs(net) / self.cfg.order_size)   # ceil: cover the whole bag, not a truncated part
                orders += reducers[:min(len(reducers), need)]
        if net > 0:    # long
            if mid < self.pos_avg:   # underwater → don't grow the bag: drop ALL buys
                orders = [o for o in orders if o.side is not Side.BUY]
            else:                    # in profit → no averaging UP (buy only at/below entry)
                orders = [o for o in orders if not (o.side is Side.BUY and o.price > self.pos_avg)]
        else:          # short: mirror
            if mid > self.pos_avg:
                orders = [o for o in orders if o.side is not Side.SELL]
            else:
                orders = [o for o in orders if not (o.side is Side.SELL and o.price < self.pos_avg)]
        return self._respect_inventory_cap(orders, net)

    def _respect_inventory_cap(self, orders: list[Order], net: Decimal) -> list[Order]:
        """A re-center must never re-arm the side that grows |inventory| past the
        breaker cap. Keeps only as many inventory-increasing orders as the remaining
        cap room allows, nearest to mid first (they fill first)."""
        cap = self.breaker.max_inventory if self.breaker is not None else Decimal(0)
        if cap <= 0 or self.cfg.order_size <= 0 or net == 0:
            return orders
        room = int((cap - abs(net)) / self.cfg.order_size)
        growing = Side.BUY if net > 0 else Side.SELL
        ladder = [o for o in orders if o.side is growing]
        if len(ladder) <= room:
            return orders
        ladder.sort(key=lambda o: o.price, reverse=(growing is Side.BUY))
        keep = {id(o) for o in ladder[:max(room, 0)]}
        dropped = len(ladder) - len(keep)
        print(f"  ~ thinned {dropped} {growing.value} order(s): inventory {net} near cap {cap}")
        return [o for o in orders if o.side is not growing or id(o) in keep]

    async def _cancel_own(self) -> None:
        """Cancel only THIS instance's resting orders. Falls back to market-wide
        cancel_all when the venue cannot enumerate open orders."""
        self._resting.clear()
        try:
            resting = await self.ex.open_orders(self.cfg.market)
        except Exception as e:  # noqa: BLE001
            print(f"  ! open_orders failed ({e}) — falling back to cancel_all")
            await self.ex.cancel_all(self.cfg.market)
            return
        mine = [o for o in resting if o.instance_id == self.cfg.instance_id]
        for o in mine:
            try:
                await self.ex.cancel_order(self.cfg.market, o.order_id or o.external_id)
            except Exception as e:  # noqa: BLE001 — an order that just filled is fine
                print(f"  ! cancel {o.external_id} failed: {e}")

    async def monitor(self) -> None:
        """Background loop: re-center on band-exit and enforce guards until stop."""
        if self.monitor_interval <= 0:
            return
        while self.state in (GridState.RUNNING, GridState.REBALANCING):
            await asyncio.sleep(self.monitor_interval)
            try:
                await self.maybe_recenter()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — a bad tick must not kill the loop
                print(f"  ! monitor tick failed: {e}")

    # ---- risk + profit ----

    def net_inventory(self) -> Decimal:
        return self.pos_qty

    def unrealized(self, mark: Decimal) -> Decimal:
        return (mark - self.pos_avg) * self.pos_qty  # correct for both long and short

    def _track_equity(self, pnl: Decimal) -> None:
        """Maintain the PnL high-water mark and the worst peak-to-trough drop, both in
        quote units, for the episode's max_drawdown_bps."""
        if pnl > self.peak_pnl:
            self.peak_pnl = pnl
        dd = self.peak_pnl - pnl
        if dd > self.max_dd_quote:
            self.max_dd_quote = dd

    async def _check_guards(self, mark: Decimal | None = None) -> None:
        if self.state not in (GridState.RUNNING, GridState.REBALANCING):
            return
        pnl = self.realized + (self.unrealized(mark) if mark is not None else Decimal(0))
        self._track_equity(pnl)
        if self.breaker is not None:
            reason = self.breaker.check(self.net_inventory(), pnl)
            if reason:
                await self._exit(reason, "circuit-breaker", mark)
                return
        if self.profit_guard is not None and mark is not None:
            reason = self.profit_guard.check(pnl)
            if reason:
                await self._exit(reason, "profit-lock", mark)
        # Trend-stop: flatten when price breaks the BAND against our inventory by more than
        # trend_stop_frac — a real trend/falling-knife, not an in-band dip (which the grid is
        # built to hold, and which the re-center follows). Band-relative so normal oscillation
        # never trips it; the circuit breaker still backstops a slow grind.
        if self.trend_stop_frac > 0 and mark is not None and self.state is GridState.RUNNING:
            breakout = self._adverse_breakout(mark)
            if breakout >= self.trend_stop_frac:
                await self._exit(f"price broke the band {breakout:.1%} against {self.net_inventory():+} "
                                 f"inventory — trend, flatten don't average in", "trend-stop", mark)

    async def _exit(self, reason: str, kind: str, mark: Decimal | None = None) -> None:
        """Emergency/profit exit: stop the grid, cancel everything, flatten."""
        if self.state is GridState.HALTED:
            return
        self.state = GridState.HALTED
        self._halted.set()                 # wake consume() — resting orders are gone, no more fills
        self.exit_reason = reason
        self.exit_kind = kind
        self._resting.clear()
        print(f"  !! {kind}: {reason} — cancel_all + flatten")
        await self._retry_exit_step("cancel_all", self.ex.cancel_all)
        if await self._retry_exit_step("flatten", self.ex.flatten):
            self._realize_exit(mark, self._exit_haircut_bps())

    def _exit_haircut_bps(self) -> int:
        """Cost of crossing the spread to flatten, in bps, so the attested exit PnL is
        conservative (never optimistic). A fake/dry venue has no swap cost -> 0."""
        slip = getattr(self.ex, "slippage_pct", None)
        if not slip:
            return 0
        return int(round(slip * 100)) + 25  # venue slippage + ~0.25% PancakeSwap taker fee

    def _realize_exit(self, mark: Decimal | None, haircut_bps: int = 0) -> None:
        """Book a flatten into realized PnL so the attested number matches the venue
        (a breaker flatten that outcome() never saw is how an episode attests a profit
        while the account lost money). `haircut_bps` worsens the exit price by the venue's
        swap cost so the booked number is conservative; the synthetic exit fill is added
        to the fills root so the verifiable history covers the settle."""
        if self.pos_qty == 0:
            return
        if mark is None:
            print(f"  ! flatten not priced — realized PnL excludes the last {self.pos_qty} position")
            self.pos_qty, self.pos_avg = Decimal(0), Decimal(0)
            return
        side = Side.SELL if self.pos_qty > 0 else Side.BUY
        eff = mark
        if haircut_bps > 0:                # crossing the spread to flatten costs us
            adj = mark * Decimal(haircut_bps) / Decimal(10_000)
            eff = mark - adj if side is Side.SELL else mark + adj
        qty = abs(self.pos_qty)
        self._fills.append(Fill(
            instance_id=self.cfg.instance_id, market=self.cfg.market, side=side, price=eff,
            qty=qty, external_id=f"exit-{self._gen}-{self.exit_kind or 'close'}", level=-1,
            ts=int(time.time())))
        self._apply_fill(side, eff, qty)
        self._track_equity(self.realized)  # fold the exit into the drawdown curve

    async def _retry_exit_step(self, what: str, op, attempts: int = 3) -> bool:
        """An exit step MUST land: once HALTED the monitor stops, so a tripped breaker
        with a live position is unsupervised risk. Retry with backoff; shout if the
        venue still refuses."""
        delay = self._exit_retry_delay
        for attempt in range(1, attempts + 1):
            try:
                await op(self.cfg.market)
                return True
            except Exception as e:  # noqa: BLE001
                print(f"    {what} failed (attempt {attempt}/{attempts}): {e}")
                if attempt < attempts:
                    await asyncio.sleep(delay)
                    delay *= 2
        print(f"  !!! {what} did not land after {attempts} attempts — "
              f"POSITION MAY STILL BE OPEN on {self.cfg.market}; close it manually")
        return False

    async def handle_fill(self, fill: Fill) -> Order | None:
        if fill.instance_id != self.cfg.instance_id:
            return None
        if self.store is not None:
            await self.store.record_fill(fill)  # persist BEFORE placing the pair
        self._fills.append(fill)
        self.fill_count += 1
        self._resting.pop(fill.external_id, None)
        self._apply_fill(fill.side, fill.price, fill.qty)
        await self._check_guards(fill.price)  # guards before placing more orders
        if self.state is not GridState.RUNNING:
            return None
        self._nonce += 1
        paired = compute_paired_order(self.cfg, self.levels, fill.level, fill.side, self._nonce)
        if paired is not None and not self._fits_cap(paired):
            print(f"  ~ skipped paired {paired.side.value} L{paired.level}: "
                  f"resting ladder would exceed inventory cap")
            return None
        if paired is not None:
            await self._place(paired)
        return paired

    def _fits_cap(self, order: Order) -> bool:
        """Would |position| stay within the breaker cap even if EVERY resting
        inventory-increasing order — plus this one — filled in a single sweep?
        Reducing orders always fit."""
        cap = self.breaker.max_inventory if self.breaker is not None else Decimal(0)
        if cap <= 0:
            return True
        pos = self.pos_qty
        growing = Side.BUY if pos >= 0 else Side.SELL
        if order.side is not growing:
            return True
        resting_growth = sum((o.qty for o in self._resting.values() if o.side is growing), Decimal(0))
        return abs(pos) + resting_growth + order.qty <= cap

    def _apply_fill(self, side: Side, price: Decimal, qty: Decimal) -> None:
        """Signed-position accounting: average in when a fill extends the position,
        realize PnL when it reduces it, re-base the average when it flips."""
        signed = qty if side is Side.BUY else -qty
        pos, avg = self.pos_qty, self.pos_avg
        if pos == 0 or (pos > 0) == (signed > 0):
            new_qty = pos + signed                      # opening / adding same side
            self.pos_avg = (avg * abs(pos) + price * qty) / abs(new_qty)
            self.pos_qty = new_qty
            return
        closed = min(qty, abs(pos))                     # opposite side -> close (maybe flip)
        pnl = (price - avg) * closed if pos > 0 else (avg - price) * closed
        self.realized += pnl
        self.closes += 1
        if pnl > 0:
            self.wins += 1
        new_qty = pos + signed
        self.pos_qty = new_qty
        if new_qty == 0:
            self.pos_avg = Decimal(0)
        elif (new_qty > 0) != (pos > 0):
            self.pos_avg = price                        # flipped: remainder opens here

    def rehydrate(self, fills) -> None:
        """Replay persisted fills to restore realized PnL / winrate / inventory after a
        restart. Does not place orders (assumes venue orders are reconciled)."""
        for f in fills:
            self._fills.append(f)
            self.fill_count += 1
            self._apply_fill(f.side, f.price, f.qty)
        self.state = GridState.RUNNING

    async def consume(self) -> None:
        """React to streamed fills until the grid leaves RUNNING. Each fill is raced
        against a stop Event so a guard HALT — which clears resting orders, after which
        the stream may never yield again — unblocks the loop instead of hanging forever
        (an agent that looks alive but has stopped reacting)."""
        stream = self.ex.stream_fills()
        stop = asyncio.ensure_future(self._halted.wait())
        try:
            while self.state in (GridState.RUNNING, GridState.REBALANCING):
                nxt = asyncio.ensure_future(stream.__anext__())
                done, _ = await asyncio.wait({nxt, stop}, return_when=asyncio.FIRST_COMPLETED)
                if nxt not in done:                         # halt fired before a fill arrived
                    nxt.cancel()
                    try:
                        await nxt                           # let the generator settle before aclose()
                    except BaseException:  # noqa: BLE001 — CancelledError/StopAsyncIteration on shutdown
                        pass
                    break
                try:
                    fill = nxt.result()
                except StopAsyncIteration:
                    break
                if self.state not in (GridState.RUNNING, GridState.REBALANCING):
                    break
                await self.handle_fill(fill)
        finally:
            stop.cancel()
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except BaseException:  # noqa: BLE001 — best-effort stream teardown
                    pass

    async def run(self) -> None:
        await self.start()
        await self.consume()

    def pause(self) -> None:
        if self.state is GridState.RUNNING:
            self.state = GridState.HALTED
            self._halted.set()

    async def stop(self) -> None:
        self.state = GridState.EXITING
        self._halted.set()                 # wake consume()
        await self._cancel_own()
        await self._close_flat()
        self.state = GridState.HALTED

    async def stop_carry(self) -> tuple[Decimal, Decimal] | None:
        """Stop on a strategy SWITCH without crossing the spread to flatten: cancel
        resting orders, mark the open position to market (book it into realized so this
        episode's outcome stays honest), and return (qty, mark) for the next grid to
        inherit. The tokens are physically retained; the next episode measures PnL from
        `mark`, so nothing is double-counted. Returns None when flat or unpriceable."""
        self.state = GridState.EXITING
        self._halted.set()                 # wake consume()
        await self._cancel_own()
        if self.pos_qty == 0:
            self.state = GridState.HALTED
            return None
        mark = await self._mid_safe()
        if mark is None:
            await self._close_flat()          # can't price → never carry an unpriced bag
            self.state = GridState.HALTED
            return None
        qty = self.pos_qty
        self._realize_exit(mark)              # books (mark-avg)*qty, zeroes pos for outcome()
        self.state = GridState.HALTED
        return (qty, mark)

    def seed_position(self, qty: Decimal, avg: Decimal) -> None:
        """Inherit inventory carried from a previous episode (a switch), so the new grid
        works it off via its reducers instead of the old grid crossing the spread to
        flatten. `avg` is the carry-in mark; PnL is measured from here."""
        self.pos_qty = qty
        self.pos_avg = avg

    async def _mid_safe(self) -> Decimal | None:
        try:
            bid, ask = await self.ex.best_bid_ask(self.cfg.market)
            return (bid + ask) / 2
        except Exception:  # noqa: BLE001
            return None

    async def _close_flat(self) -> None:
        """A graceful close must not leave an unsupervised position behind. Flatten and
        book the exit at the current mid so the attested PnL includes it."""
        if self.pos_qty == 0:
            return
        mark: Decimal | None = None
        try:
            bid, ask = await self.ex.best_bid_ask(self.cfg.market)
            mark = (bid + ask) / 2
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.ex.flatten(self.cfg.market)
        except Exception as e:  # noqa: BLE001
            print(f"  !!! close flatten failed — POSITION MAY STILL BE OPEN on "
                  f"{self.cfg.market}: {e}")
            return
        self._realize_exit(mark, self._exit_haircut_bps())

    @property
    def winrate(self) -> float:
        return (self.wins / self.closes) if self.closes else 0.0

    def fills_root(self) -> str:
        h = hashlib.sha256()
        for f in sorted(self._fills, key=lambda f: (f.ts, f.external_id)):  # canonical, order-independent
            h.update(f"{f.external_id}:{f.side.value}:{f.price}:{f.qty}".encode())
        return "0x" + h.hexdigest()

    def outcome(self) -> EpisodeOutcome:
        return EpisodeOutcome(
            instance_id=self.cfg.instance_id,
            realized_pnl_quote=self.realized,
            pnl_bps=bps(self.realized, self.deployed),
            trades=self.closes,
            max_drawdown_bps=bps(self.max_dd_quote, self.deployed),
            closed_at=int(time.time()),
            fills_root=self.fills_root(),
        )
