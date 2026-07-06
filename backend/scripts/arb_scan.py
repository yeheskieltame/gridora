"""Real arbitrage / dislocation scanner: TWAK (LiquidMesh) on-chain price vs CMC reference.

Why not triangular arb? `twak swap` routes through the LiquidMesh AGGREGATOR, which already
picks the best path — so a USDT→A→B→USDT loop inside it can never beat the direct USDT→B route
(no internal inefficiency to harvest). Verified: priceImpact 0, optimal routing.

The only edge a DEX-only tiny account can actually capture is a DEX-vs-reference DISLOCATION:
a token whose live on-chain price (what TWAK really fills) sits BELOW its CMC reference by more
than the round-trip friction. Buy it cheap on-chain, let it converge up to fair value, sell.
That's mean-reversion on the basis, measured with REAL executable quotes — not an assumption.

For each tradeable token we quote a REAL round trip ($N USDT → token → USDT) to measure friction,
and compare the on-chain buy/sell price to CMC. Edge exists only when |dislocation| > friction.

Run:  GRIDORA_TESTNET=false GRIDORA_CHAIN_ID=56 GRIDORA_CMC_API_KEY=… python -u -m scripts.arb_scan
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
import urllib.request
from decimal import Decimal

from gridora.adapters.exchanges.bsc_twak.bsc_tokens import BSC_TOKENS
from gridora.adapters.exchanges.bsc_twak.twak_client import TwakClient
from gridora.config import settings

NOTIONAL = Decimal("10")            # probe size (≈ our position size, so friction is realistic)
USDT = BSC_TOKENS["USDT"][0]
STABLE = {"USDT", "USDC", "FDUSD", "DAI"}
# tradeable eligible (verified addresses) minus stables — the only things we can safely route
TOKENS = [s for s in BSC_TOKENS if s not in STABLE and s not in ("WBNB", "BNB", "BTCB")]

KEY = os.getenv("GRIDORA_CMC_API_KEY", "")
BASE = os.getenv("GRIDORA_CMC_URL", "https://pro-api.coinmarketcap.com")


def _num(s: str) -> Decimal:
    """'7.5255 CAKE' -> Decimal('7.5255')."""
    try:
        return Decimal(str(s).strip().split()[0])
    except Exception:  # noqa: BLE001
        return Decimal(0)


def cmc_prices(symbols):
    out = {}
    url = f"{BASE}/v2/cryptocurrency/quotes/latest?symbol={urllib.parse.quote(','.join(symbols))}&convert=USD"
    req = urllib.request.Request(url, headers={"X-CMC_PRO_API_KEY": KEY, "Accept": "application/json"})
    data = json.load(urllib.request.urlopen(req, timeout=40)).get("data", {})
    for sym, entries in data.items():
        if entries:
            best = max(entries, key=lambda c: (c.get("quote", {}).get("USD", {}).get("market_cap") or 0))
            out[sym.upper()] = best["quote"]["USD"]["price"]
    return out


async def main():
    settings.guard()
    twak = TwakClient(chain_key=settings.chain_key)
    cmc = cmc_prices(TOKENS) if KEY else {}

    async def quote(amount, frm, to, dec=None):
        try:
            q = await twak.swap(str(amount), frm, to, slippage_pct=1, quote_only=True, decimals=dec)
            return _num(q.get("output", "0"))
        except Exception:  # noqa: BLE001
            return Decimal(0)

    async def probe(sym):
        addr, dec = BSC_TOKENS[sym]
        amt_tok = await quote(NOTIONAL, USDT, addr)             # BUY: $10 USDT -> token
        if amt_tok <= 0:
            return None
        back = await quote(amt_tok, addr, USDT, dec)            # SELL: that token -> USDT
        buy_px = NOTIONAL / amt_tok                             # on-chain ask (pay per token)
        sell_px = (back / amt_tok) if amt_tok else Decimal(0)   # on-chain bid (get per token)
        retention = back / NOTIONAL                            # USDT back per $1 in
        friction = (1 - retention) * 100                       # round-trip cost %
        ref = Decimal(str(cmc.get(sym, 0)))
        # dislocation: how far the on-chain MID sits below CMC fair (positive = cheap on-chain)
        mid = (buy_px + sell_px) / 2
        disloc = float((ref - mid) / ref * 100) if ref > 0 else 0.0
        edge = disloc - float(friction)                        # net of the round-trip cost
        return {"sym": sym, "ref": float(ref), "buy": float(buy_px), "sell": float(sell_px),
                "friction": float(friction), "disloc": disloc, "edge": edge}

    rows = [r for r in await asyncio.gather(*(probe(s) for s in TOKENS)) if r]
    rows.sort(key=lambda r: -r["edge"])

    print(f"\nprobe ${NOTIONAL} | provider LiquidMesh (aggregated) | {len(rows)} tradeable tokens\n")
    print(f"{'sym':<7}{'CMC$':>11}{'onchain_buy':>13}{'onchain_sell':>13}{'rt_fric%':>9}{'disloc%':>9}{'EDGE%':>8}")
    for r in rows:
        flag = "  <-- EDGE" if r["edge"] > 0.3 else ""
        print(f"{r['sym']:<7}{r['ref']:>11.5g}{r['buy']:>13.5g}{r['sell']:>13.5g}"
              f"{r['friction']:>9.2f}{r['disloc']:>+9.2f}{r['edge']:>+8.2f}{flag}")
    best = rows[0]
    print(f"\nmedian round-trip friction: {sorted(r['friction'] for r in rows)[len(rows)//2]:.2f}%")
    print(f"best net edge: {best['sym']} {best['edge']:+.2f}%  "
          f"({'EXPLOITABLE' if best['edge'] > 0.3 else 'NONE clears friction — arb not viable, see verdict'})")


if __name__ == "__main__":
    asyncio.run(main())
