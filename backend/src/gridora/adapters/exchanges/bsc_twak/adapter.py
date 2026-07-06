"""BSC spot-grid ExchangePort via TWAK-native actions — the sole, self-custodial executor.

Primary = maker LIMIT orders: place_order -> `twak automate add` (BUY rests from=quote
to=base condition=below at the level USD price; SELL mirrors). A `twak serve --watch`
watcher executes them; stream_fills polls `twak automate list` and emits a Fill when an
order disappears. Fallback (`use_limit_orders=False`): synthetic grid via `twak swap` on
cross. TWAK resolves symbols + signs locally — no router calldata, no local key.
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import AsyncIterator, Sequence

from ....domain.models import BalanceView, Fill, MarketMeta, Order, Position, Side
from .bsc_tokens import UnknownToken, resolve_token, split_market, tokens_for
from .twak_client import TwakClient, TwakError


def _lead_decimal(*vals) -> Decimal:
    """TWAK quote/swap 'output' is '<number> <SYMBOL>' (e.g. '0.81 USDT'), not a bare
    number — take the leading numeric token. Returns the first value that parses."""
    for v in vals:
        if v is None:
            continue
        head = str(v).strip().split()
        if head:
            try:
                return Decimal(head[0])
            except Exception:  # noqa: BLE001
                continue
    return Decimal(0)


class BscTwakExchange:
    """ExchangePort. venue == 'BSC_TWAK'. Every on-chain action signed by TWAK."""

    venue = "BSC_TWAK"
    _EXECUTED_STATES = frozenset({"executed", "filled", "completed", "done", "success"})
    _DEAD_STATES = frozenset({"expired", "cancelled", "canceled", "failed", "rejected", "deleted"})
    _USD_QUOTES = frozenset({"USDT", "USDC", "FDUSD", "DAI", "TUSD", "USD", "BUSD"})

    def __init__(self, twak: TwakClient, chain_id: int = 56, chain_key: str = "bsc",
                 use_limit_orders: bool = True, slippage_pct: float = 0.5,
                 tick: Decimal = Decimal("0.0001"), qty_step: Decimal = Decimal("0.0001"),
                 min_qty: Decimal = Decimal("0.001"), base_decimals: int = 18,
                 quote_decimals: int = 18, poll_interval: float = 5.0,
                 confirm_unlisted_as_fill: bool = True) -> None:
        self.twak = twak
        self.chain_id = chain_id
        self.chain_key = chain_key
        self.use_limit_orders = use_limit_orders
        self.slippage_pct = slippage_pct
        self.tick = tick
        self.qty_step = qty_step
        self.min_qty = min_qty
        self.base_decimals = base_decimals
        self.quote_decimals = quote_decimals
        self.poll_interval = poll_interval
        # When an order leaves `automate list` with NO status field, TWAK gives no way to
        # tell executed from expired (there is no per-order status verb). True keeps the
        # legacy "vanished == filled" assumption (warned); set False for strict mode once
        # live TWAK is confirmed to report a terminal status in the list entry.
        self.confirm_unlisted_as_fill = confirm_unlisted_as_fill
        self._tokens = tokens_for(chain_id)      # symbol -> (verified address, decimals) for this chain
        self._resting: dict[str, Order] = {}     # order_id -> resting order
        self._cancelled: set[str] = set()          # ids we deleted (so they aren't read as fills)
        self._seq = 0

    def _resolve(self, symbol: str) -> tuple[str, int]:
        """Symbol -> (verified BSC contract address, decimals). We pass the ADDRESS to TWAK,
        not the symbol: a raw address makes TWAK trade EXACTLY that token, whereas its symbol
        resolver silently swaps unknowns into BNB (verified 2026-06-22: DOGE/LTC/AVAX/SOL/TRX
        as bare symbols → BNB). Anything not in the verified address book is REFUSED — we never
        risk a wrong-token swap with real funds."""
        try:
            return resolve_token(symbol.upper(), self._tokens)
        except UnknownToken as e:
            raise TwakError(
                f"refusing {symbol}: not in the verified BSC address map (bsc_tokens.BSC_TOKENS). "
                f"TWAK's symbol resolver misroutes unknown symbols to BNB, so trading by an "
                f"unverified symbol could buy the wrong asset. Add a BscScan-verified address first."
            ) from e

    async def market_meta(self, market: str) -> MarketMeta:
        return MarketMeta(market=market, tick=self.tick, qty_step=self.qty_step,
                          min_qty=self.min_qty, base_decimals=self.base_decimals,
                          quote_decimals=self.quote_decimals)

    async def best_bid_ask(self, market: str) -> tuple[Decimal, Decimal]:
        """Executable DEX mid from a TWAK swap quote (no execution)."""
        base, quote = split_market(market)
        b_addr, b_dec = self._resolve(base)
        q_addr, _ = self._resolve(quote)
        q = await self.twak.swap("1", b_addr, q_addr, chain=self.chain_key,
                                 slippage_pct=self.slippage_pct, quote_only=True, decimals=b_dec)
        mid = _lead_decimal(q.get("output"), q.get("price"), q.get("amountOut"))
        if mid <= 0:
            raise RuntimeError(f"no quote for {market}: {q}")
        half = mid * Decimal(str(self.slippage_pct)) / Decimal(200)  # half the slippage band
        return (mid - half, mid + half)

    async def place_order(self, order: Order) -> Order:
        base, quote = split_market(order.market)
        b_addr, b_dec = self._resolve(base)
        q_addr, q_dec = self._resolve(quote)
        if self.use_limit_orders:
            if quote.upper() not in self._USD_QUOTES:   # --price is a USD target; a non-USD quote misprices the trigger
                raise TwakError(f"limit grid needs a USD-pegged quote (got {quote}); the limit --price "
                                f"is a USD target. Use swap_fallback for a non-USD quote.")
            if b_dec != 18 or q_dec != 18:   # automate add has no --decimals; non-18 amounts would misparse
                raise TwakError(f"limit grid supports 18-decimal tokens only (got {base}={b_dec}d, "
                                f"{quote}={q_dec}d); use swap_fallback for non-18-decimal tokens.")
            if order.side is Side.BUY:
                # spend `quote` to buy `base` when price dips to the level
                res = await self.twak.add_limit_order(
                    frm=q_addr, to=b_addr, amount=str(order.qty * order.price),
                    price_usd=str(order.price), condition="below", chain=self.chain_key)
            else:
                # sell `base` for `quote` when price rises to the level
                res = await self.twak.add_limit_order(
                    frm=b_addr, to=q_addr, amount=str(order.qty),
                    price_usd=str(order.price), condition="above", chain=self.chain_key)
            oid = res.get("id") or res.get("automationId")
            if not oid:   # a malformed response must not become an order keyed "" that polls as a phantom fill
                raise TwakError(f"automate add returned no id: {res}")
            order.order_id = str(oid)
        else:
            self._seq += 1
            order.order_id = order.external_id or f"syn-{self._seq}"
        self._resting[order.order_id] = order
        return order

    async def cancel_order(self, market: str, order_id: str) -> None:
        if self.use_limit_orders:
            try:
                await self.twak.delete_automation(order_id)
            except Exception as e:  # noqa: BLE001
                # Could be already-executed (fine) or transient (the automation may still be
                # LIVE). Don't drop local state yet — keep it tracked so the next poll
                # reconciles instead of orphaning a possibly-live order.
                print(f"  ! delete automation {order_id} failed ({e}) — keeping it tracked to reconcile")
                return
        self._cancelled.add(order_id)
        self._resting.pop(order_id, None)

    async def cancel_all(self, market: str) -> None:
        for oid, o in list(self._resting.items()):
            if o.market == market:
                await self.cancel_order(market, oid)

    async def open_orders(self, market: str) -> Sequence[Order]:
        return [o for o in self._resting.values() if o.market == market]

    async def flatten(self, market: str) -> None:
        """Emergency exit: cancel resting orders, then market-swap all base -> quote
        via TWAK (tight slippage)."""
        await self.cancel_all(market)
        base, quote = split_market(market)
        b_addr, b_dec = self._resolve(base)
        q_addr, _ = self._resolve(quote)
        free = await self._free_balance(base)
        if free > 0:
            await self.twak.swap(str(free), b_addr, q_addr, chain=self.chain_key,
                                 slippage_pct=self.slippage_pct, decimals=b_dec)

    async def balance(self) -> BalanceView:
        """TRADING equity in USD = quote stables (@$1) + held base tokens (priced via a
        quote-only swap to USDT). DELIBERATELY EXCLUDES native BNB: BNB is gas, not trading
        capital and not a scored asset, and TWAK's `totalUsd` (BNB-only, ~$3) is flaky
        between polls — counting it made the portfolio account guard trip on a gas-sized
        swing when the cap ($2.40) was smaller than the BNB value ($2.92) (verified live
        2026-06-22). Raises on a read/valuation error so the guard SKIPS that poll (None)
        rather than undercounting and tripping."""
        b = await self.twak.balance(self.chain_key)   # propagate read errors -> guard skips poll
        equity = Decimal(0)
        quote_free = Decimal(0)
        q_addr, _ = self._resolve("USDT")
        for t in b.get("tokens", []):
            sym = str(t.get("symbol", "")).upper()
            bal = Decimal(str(t.get("balance", 0) or 0))
            if bal <= 0:
                continue
            if sym in self._USD_QUOTES:               # stablecoin ≈ $1
                equity += bal
                quote_free += bal
            else:                                     # value held base via its realizable USDT
                addr, dec = self._resolve(sym)        # unmapped held token -> raise -> guard skips
                q = await self.twak.swap(str(bal), addr, q_addr, chain=self.chain_key,
                                         slippage_pct=self.slippage_pct, quote_only=True, decimals=dec)
                equity += _lead_decimal(q.get("output"))
        return BalanceView(quote_free=quote_free, base_free=Decimal(0), equity_quote=equity)

    async def _free_balance(self, symbol: str) -> Decimal:
        try:
            b = await self.twak.balance(self.chain_key)
        except Exception:  # noqa: BLE001
            return Decimal(0)
        for t in b.get("tokens", []):
            if str(t.get("symbol", "")).upper() == symbol.upper():
                return Decimal(str(t.get("balance", 0) or 0))
        if str(b.get("symbol", "")).upper() == symbol.upper():
            return Decimal(str(b.get("available", 0) or 0))
        return Decimal(0)

    async def positions(self) -> Sequence[Position]:
        return []  # inventory is tracked in the engine, not the venue

    async def stream_fills(self) -> AsyncIterator[Fill]:
        """Limit-order mode: poll `twak automate list`; a tracked order that vanished
        executed -> emit its Fill. Swap-fallback mode: poll price and execute crossed
        levels via `twak swap`."""
        while True:
            await asyncio.sleep(self.poll_interval)
            if self.use_limit_orders:
                async for f in self._poll_limit_fills():
                    yield f
            else:
                async for f in self._poll_synthetic_fills():
                    yield f

    async def _poll_limit_fills(self) -> AsyncIterator[Fill]:
        try:
            entries = {str(a.get("id")): a for a in await self.twak.list_automations()}
        except Exception as e:  # noqa: BLE001 — a bad read must not kill the stream
            print(f"  ! automate list failed: {e}")
            return
        self._cancelled &= set(entries)   # a cancelled id can't reappear; prune so the set can't grow unbounded
        for oid, o in list(self._resting.items()):
            if oid in self._cancelled:
                continue
            entry = entries.get(oid)
            status = str((entry or {}).get("status") or (entry or {}).get("state") or "").lower()
            if status in self._DEAD_STATES:                # terminal NON-execution — drop, never a fill
                self._resting.pop(oid, None)
                print(f"  ~ limit {oid} {status} — dropped (not a fill)")
                continue
            if entry is not None and status not in self._EXECUTED_STATES:
                continue                                   # still listed (active/paused) — not a fill yet
            # Either listed as executed, or gone with no status. Only the former is a
            # CONFIRMED fill; the latter is unconfirmable (TWAK has no per-order status read).
            confirmed = status in self._EXECUTED_STATES
            if not confirmed and not self.confirm_unlisted_as_fill:
                self._resting.pop(oid, None)
                print(f"  ~ limit {oid} vanished; execution UNCONFIRMED — dropped (strict mode)")
                continue
            if not confirmed:
                print(f"  ! limit {oid} vanished without a status — assuming filled "
                      f"(UNVERIFIED; confirm live TWAK reports a terminal status in `automate list`)")
            self._resting.pop(oid, None)
            yield Fill(instance_id=o.instance_id, market=o.market, side=o.side, price=o.price,
                       qty=o.qty, external_id=o.external_id, level=o.level, ts=int(time.time()),
                       tx_hash=str((entry or {}).get("txHash") or (entry or {}).get("hash") or ""))

    async def _poll_synthetic_fills(self) -> AsyncIterator[Fill]:
        for oid, o in list(self._resting.items()):
            try:
                bid, ask = await self.best_bid_ask(o.market)
                mid = (bid + ask) / 2
            except Exception:  # noqa: BLE001
                continue
            crossed = (o.side is Side.BUY and mid <= o.price) or (o.side is Side.SELL and mid >= o.price)
            if not crossed:
                continue
            self._resting.pop(oid, None)
            base, quote = split_market(o.market)
            b_addr, b_dec = self._resolve(base)
            q_addr, q_dec = self._resolve(quote)
            try:
                if o.side is Side.BUY:
                    spent = o.qty * o.price                 # quote spent buying at/below the level
                    res = await self.twak.swap(str(spent), q_addr, b_addr, chain=self.chain_key,
                                               slippage_pct=self.slippage_pct, decimals=q_dec)
                    got = _lead_decimal(res.get("output"), res.get("amountOut"))
                    fqty = got if got > 0 else o.qty        # real base received (>= o.qty when filled on a dip)
                    fprice = spent / fqty if fqty > 0 else o.price
                else:
                    res = await self.twak.swap(str(o.qty), b_addr, q_addr, chain=self.chain_key,
                                               slippage_pct=self.slippage_pct, decimals=b_dec)
                    got = _lead_decimal(res.get("output"), res.get("amountOut"))
                    fqty = o.qty
                    fprice = got / o.qty if (got > 0 and o.qty > 0) else o.price
            except Exception as e:  # noqa: BLE001
                print(f"  ! synthetic swap for {oid} failed: {e}")
                continue
            yield Fill(instance_id=o.instance_id, market=o.market, side=o.side, price=fprice,
                       qty=fqty, external_id=o.external_id, level=o.level, ts=int(time.time()),
                       tx_hash=str(res.get("hash", "")))
