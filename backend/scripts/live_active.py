"""ACTIVE rotating rebalancer — LIVE on BSC via TWAK (real money). Fixes the passive grid's
fatal flaws (starts flat → only dips fill; sits idle on a quiet token):

  1. SEED: market-buy ~half the USDT into the chosen token → a guaranteed first trade, capital
     deployed, AND inventory so we can sell on upticks (not just buy dips). Skipped if we
     already hold a position in that token (e.g. after a restart).
  2. REBALANCE: sell a slice every +STEP from the reference, buy a slice every −STEP. Each move
     is a real on-chain trade (the scored portfolio IS the proof).
  3. ROTATE: if a token goes quiet (no trade for ROTATE_MIN), flatten it to USDT and switch to
     the next mover. Never sit idle.
  4. STOP: flatten + halt if equity drops STOP_DD from the start (DQ protection).

Balances are read straight from the chain via RPC `balanceOf` — NOT from TWAK's `balance`,
which lags after a swap AND omits non-major tokens entirely (it never lists SLX even when we
hold it; verified live 2026-06-22). RPC is ground truth and immediate once a swap tx is mined.
All swaps go by VERIFIED CONTRACT ADDRESS.

Run (mainnet env): python -u -m scripts.live_active SLX UNI PENGU BONK FIL ASTER
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import time
from decimal import Decimal

import httpx

from gridora.adapters.exchanges.bsc_twak.bsc_tokens import resolve_token, tokens_for
from gridora.adapters.exchanges.bsc_twak.twak_client import TwakClient
from gridora.config import settings

USDT = "0x55d398326f99059fF775485246999027B3197955"
# Journal each settled episode to OUR TradeJournal (deployer key via cast — TWAK can't call it).
JOURNAL = os.environ.get("GRIDORA_JOURNAL_ADDR", "0xE946C28ea10bf29AcA9a094f66079De84a50d409")
AGENT_ID = os.environ.get("GRIDORA_AGENT_ID", "1")
DEPLOYER_KEY = os.environ.get("DEPLOYER_PRIVATE_KEY", "")
SLIPPAGE = 1.5               # % — higher than the grid's 0.5 to avoid reverts on smaller-cap sells
STEP = Decimal("0.010")      # trade on a ±1% move from ref (clears the ~0.6% round-trip cost)
SLICE_USD = Decimal("3")     # USD per rebalance trade
SEED_FRAC = Decimal("0.50")  # seed this fraction of free USDT into a fresh token
POLL_S = 20
ROTATE_MIN = 20              # no trade for this long → rotate to the next mover
STOP_DD = Decimal("0.12")    # flatten + halt if equity falls this fraction from start


def _num(s) -> Decimal:
    try:
        return Decimal(str(s).strip().split()[0])
    except Exception:  # noqa: BLE001
        return Decimal(0)


class Trader:
    """Reads holdings from the chain via RPC balanceOf (TWAK's balance is unreliable)."""

    def __init__(self, tw: TwakClient, chain: str, wallet: str, rpc: str, candidates: list[str]):
        self.tw, self.chain, self.wallet, self.rpc = tw, chain, wallet, rpc
        self.toks = tokens_for(56)
        self.candidates = candidates

    async def rpc_balance(self, addr: str, decimals: int) -> Decimal:
        data = "0x70a08231" + self.wallet[2:].lower().rjust(64, "0")
        async with httpx.AsyncClient(timeout=15) as h:
            r = await h.post(self.rpc, json={"jsonrpc": "2.0", "method": "eth_call",
                             "params": [{"to": addr, "data": data}, "latest"], "id": 1})
            hexv = r.json().get("result") or "0x0"
        return Decimal(int(hexv, 16)) / (Decimal(10) ** decimals)

    async def tx_ok(self, tx_hash: str) -> bool:
        """True only if the tx mined with status 1. TWAK's CLI reports a swap 'output' even
        when the on-chain tx REVERTS (verified live: a flatten that returned 11.76 USDT but
        status 0) — so we must check the receipt, not trust the output."""
        if not tx_hash or not tx_hash.startswith("0x") or len(tx_hash) < 66:
            return False
        for _ in range(8):   # wait for the receipt to appear
            async with httpx.AsyncClient(timeout=15) as h:
                r = await h.post(self.rpc, json={"jsonrpc": "2.0", "method": "eth_getTransactionReceipt",
                                 "params": [tx_hash], "id": 1})
            rec = r.json().get("result")
            if rec:
                return rec.get("status") == "0x1"
            await asyncio.sleep(2)
        return False

    async def bal(self, sym: str) -> Decimal:
        addr, dec = (USDT, 18) if sym == "USDT" else resolve_token(sym, self.toks)
        return await self.rpc_balance(addr, dec)

    async def price(self, sym: str) -> Decimal:
        """USD price = quote $5 of USDT -> token, inverted. Quoting "1 unit" is unreliable for
        cheap tokens (a dust amount mis-rates by 20×+ — verified live: BONK), so price a
        meaningful notional instead."""
        addr, _ = resolve_token(sym, self.toks)
        q = await self.tw.swap("5", USDT, addr, chain=self.chain, slippage_pct=0.5,
                               quote_only=True, decimals=18)
        out = _num(q.get("output"))
        return Decimal("5") / out if out > 0 else Decimal(0)

    async def equity(self) -> Decimal:
        """USDT + the REALIZABLE USD value of every candidate token held (quote the ACTUAL
        held amount -> USDT, not a per-unit extrapolation). Counts ALL holdings so an orphan
        from a failed rotation isn't dropped (which would under-read equity and trip the stop)."""
        eq = await self.bal("USDT")
        for sym in self.candidates:
            amt = await self.bal(sym)
            if amt <= 0:
                continue
            addr, dec = resolve_token(sym, self.toks)
            val = None
            for _ in range(3):    # RETRY — a transient quote glitch must NOT read as $0; that once
                try:              # "halved" equity and false-tripped the drawdown guard (sold WLFI).
                    q = await self.tw.swap(str(amt), addr, USDT, chain=self.chain, slippage_pct=0.5,
                                           quote_only=True, decimals=dec)
                    v = _num(q.get("output"))
                    if v > 0:
                        val = v
                        break
                except Exception:  # noqa: BLE001
                    val = None
            if val is None:
                # can't value a held token after retries — RAISE so the caller SKIPS this poll rather
                # than under-reading equity and panic-flattening on a phantom drawdown.
                raise RuntimeError(f"equity: cannot value {sym} ({float(amt):.4g}) held — skip poll")
            eq += val
        return eq

    async def journal(self, pnl_bps: int, tag: str) -> None:
        """Record a settled episode to OUR TradeJournal via `cast send` (deployer key; TWAK
        can't call custom contracts). Non-fatal: a journal failure never stops trading."""
        if not DEPLOYER_KEY:
            return
        th = "0x" + hashlib.sha256(f"{tag}|{pnl_bps}|{time.time()}".encode()).hexdigest()
        args = ["cast", "send", JOURNAL, "record(uint256,bytes32,int256,uint64)",
                AGENT_ID, th, str(int(pnl_bps)), str(int(time.time())),
                "--rpc-url", self.rpc, "--private-key", DEPLOYER_KEY, "--json"]
        try:
            p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                     stderr=asyncio.subprocess.PIPE)
            out, err = await p.communicate()
            if p.returncode == 0:
                print(f"    📒 journaled {tag} pnl {pnl_bps}bps -> TradeJournal", flush=True)
            else:
                print(f"    ! journal failed: {(err or out).decode().strip()[:80]}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"    ! journal error: {str(e)[:80]}", flush=True)

    async def swap(self, frm_sym: str, to_sym: str, amount: Decimal) -> Decimal:
        """Real swap of `amount` (frm units) frm->to by VERIFIED ADDRESS. Returns received
        (0 on failure). Retries the flaky TWAK 'could not register testnet node' hiccup."""
        f_addr, f_dec = (USDT, 18) if frm_sym == "USDT" else resolve_token(frm_sym, self.toks)
        t_addr, _ = (USDT, 18) if to_sym == "USDT" else resolve_token(to_sym, self.toks)
        for attempt in range(3):
            try:
                r = await self.tw.swap(str(amount), f_addr, t_addr, chain=self.chain,
                                       slippage_pct=SLIPPAGE, decimals=f_dec)
                got = _num(r.get("output"))
                tx = r.get("hash", "")
                if got <= 0:
                    raise RuntimeError(f"no output: {r}")
                if not await self.tx_ok(tx):           # CLI 'success' but the tx reverted on-chain
                    raise RuntimeError(f"tx {tx[:14]} reverted/unconfirmed")
                print(f"    ✓ {amount} {frm_sym} -> {got} {to_sym}  tx {tx}", flush=True)
                return got
            except Exception as e:  # noqa: BLE001
                print(f"    swap attempt {attempt+1} failed: {str(e)[:90]}", flush=True)
                await asyncio.sleep(4)
        return Decimal(0)


async def main() -> None:
    cands = [s.upper() for s in (sys.argv[1:] or ["SLX", "UNI", "PENGU", "BONK", "FIL", "ASTER"])]
    settings.guard()
    settings.assert_twak_creds()
    wallet = settings.agent_address or (await TwakClient(chain_key=settings.chain_key).address())
    tr = Trader(TwakClient(chain_key=settings.chain_key), settings.chain_key, wallet,
                settings.bsc_rpc_url, cands)

    start_eq = await tr.equity()
    floor_eq = start_eq * (Decimal(1) - STOP_DD)
    print(f"=== ACTIVE rebalancer | wallet {wallet} | start equity ${start_eq:.2f} | stop ${floor_eq:.2f} "
          f"| step ±{STEP*100}% slice ${SLICE_USD} | candidates {cands} ===", flush=True)

    idx = 0
    while True:
        sym = cands[idx % len(cands)]
        px = await tr.price(sym)
        held = await tr.bal(sym)
        held_usd = held * px
        eq = await tr.equity()
        print(f"\n>>> {sym} @ ${px:.6g} | hold {held:.4g} (${held_usd:.2f}) "
              f"| USDT ${await tr.bal('USDT'):.2f} | eq ${eq:.2f}", flush=True)

        # SEED only if we aren't already positioned in this token (e.g. fresh rotate)
        if held_usd < eq * Decimal("0.25"):
            seed = (await tr.bal("USDT")) * SEED_FRAC
            if seed >= Decimal("1"):
                print(f"  seed ${seed:.2f} -> {sym}", flush=True)
                await tr.swap("USDT", sym, seed)
        ref = await tr.price(sym)
        last_trade = time.time()
        episode_start_eq = await tr.equity()   # equity when this token episode began

        while True:
            await asyncio.sleep(POLL_S)
            try:
                px = await tr.price(sym)
                eq = await tr.equity()
                usdt = await tr.bal("USDT")
                held = await tr.bal(sym)
            except Exception:  # noqa: BLE001
                continue
            if eq <= floor_eq:
                print(f"!! STOP — equity ${eq:.2f} <= floor ${floor_eq:.2f}; flatten + halt", flush=True)
                if held > 0:
                    await tr.swap(sym, "USDT", held)
                final_eq = await tr.equity()
                await tr.journal(int((final_eq / episode_start_eq - 1) * 10000), f"{sym}-stop")
                print("=== HALTED ===", flush=True)
                return
            up = px >= ref * (Decimal(1) + STEP)
            dn = px <= ref * (Decimal(1) - STEP)
            if up and held * px > Decimal("1"):                 # banked a rip: sell a slice
                qty = min(held, SLICE_USD / px)
                print(f"  +{float((px/ref-1)*100):.2f}% → SELL {sym} @ ${px:.6g} (eq ${eq:.2f})", flush=True)
                if await tr.swap(sym, "USDT", qty) > 0:
                    ref = px; last_trade = time.time()
            elif dn and usdt >= Decimal("1"):                   # bought a dip: add a slice
                spend = min(usdt, SLICE_USD)
                print(f"  {float((px/ref-1)*100):.2f}% → BUY {sym} @ ${px:.6g} (eq ${eq:.2f})", flush=True)
                if await tr.swap("USDT", sym, spend) > 0:
                    ref = px; last_trade = time.time()
            elif (time.time() - last_trade) > ROTATE_MIN * 60:  # quiet → rotate
                print(f"  {sym} quiet {ROTATE_MIN}min — flatten + rotate", flush=True)
                if held * px > Decimal("1"):
                    await tr.swap(sym, "USDT", held)
                ep_eq = await tr.equity()
                await tr.journal(int((ep_eq / episode_start_eq - 1) * 10000), sym)   # settle on-chain
                idx += 1
                break


if __name__ == "__main__":
    asyncio.run(main())
