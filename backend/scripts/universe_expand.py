"""Universe expansion sweep — turn the CMC API benefit into more tradeable dip candidates.

Protocol (mirrors the original bsc_tokens verification standard):
  1. allowlist 149 minus already-mapped minus stables.
  2. CMC quotes/latest -> 24h USD volume >= VOL_MIN (liquidity proxy).
  3. CMC v2/info -> the token's BNB Smart Chain contract address (skip if none).
  4. TWAK REST get_swap_quote $5 USDT->address: implied price within +/-3% of CMC
     (routability + resolver-landmine + depth check at our size).
  5. Gate pair SYM_USDT must exist (autopilot needs taker flow + candles).
Survivors are PRINTED as ready-to-paste BSC_TOKENS lines — a human (or the agent)
reviews and appends them to bsc_tokens.py; this script never edits code.

Run: TWAK REST server must be up (launchd :3900).
     .venv/bin/python -m scripts.universe_expand
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from gridora.adapters.exchanges.bsc_twak.bsc_tokens import BSC_TOKENS

VOL_MIN = 20_000_000  # $/24h on CMC — below this the BSC pool is almost surely unroutable
SKIP = {"USDT", "USDC", "FDUSD", "DAI", "USD1", "BUSD", "WBNB", "BNB", "BTCB", "WBTC", "STETH", "WSTETH", "WETH", "WBETH"}
CMC = "https://pro-api.coinmarketcap.com"
USDT = "0x55d398326f99059fF775485246999027B3197955"


def cmc(path: str, params: str) -> dict:
    key = os.environ.get("GRIDORA_CMC_API_KEY", "")
    req = urllib.request.Request(f"{CMC}{path}?{params}", headers={"X-CMC_PRO_API_KEY": key})
    return json.load(urllib.request.urlopen(req, timeout=20))


def gate_has(sym: str) -> bool:
    try:
        r = json.load(urllib.request.urlopen(
            f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={sym}_USDT", timeout=10))
        return isinstance(r, list) and len(r) > 0
    except Exception:  # noqa: BLE001
        return False


def twak_quote(addr: str) -> float | None:
    """Implied token price from a $5 USDT quote through the local TWAK REST server."""
    url = os.environ.get("TWAK_REST_URL", "http://127.0.0.1:3900")
    body = json.dumps({"fromChain": "bsc", "fromToken": USDT, "toChain": "bsc",
                       "toToken": addr, "amount": "5"}).encode()
    req = urllib.request.Request(f"{url}/actions/get_swap_quote", body, {
        "Authorization": f"Bearer {os.environ.get('TWAK_HMAC_SECRET', '')}",
        "Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=30))
        out = float(str(r.get("output", "0")).split()[0])
        return 5.0 / out if out > 0 else None
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    from gridora.config import settings  # noqa: F401 — loads .env into the environment
    allow = json.load(open(os.path.join(os.path.dirname(__file__), "..", "allowlist.149.json")))
    cands = [s for s in allow if s not in BSC_TOKENS and s not in SKIP and s.isascii()]
    print(f"{len(cands)} unmapped candidates from the 149 allowlist")

    quotes: dict[str, dict] = {}
    for i in range(0, len(cands), 100):
        chunk = urllib.parse.quote(",".join(cands[i:i + 100]))
        try:
            data = cmc("/v2/cryptocurrency/quotes/latest", f"symbol={chunk}").get("data", {})
        except Exception as e:  # noqa: BLE001
            print("quotes chunk failed:", str(e)[:80]); continue
        for sym, lst in data.items():
            best = max(lst, key=lambda x: (x.get("quote", {}).get("USD", {}).get("market_cap") or 0)) \
                if isinstance(lst, list) else lst
            usd = best.get("quote", {}).get("USD", {})
            quotes[sym] = {"px": usd.get("price") or 0, "vol": usd.get("volume_24h") or 0,
                           "id": best.get("id")}

    liquid = {s: q for s, q in quotes.items() if q["vol"] >= VOL_MIN and q["px"] > 0}
    print(f"{len(liquid)} pass vol >= ${VOL_MIN/1e6:.0f}M: {sorted(liquid)}")

    survivors = []
    for sym, q in sorted(liquid.items(), key=lambda kv: -kv[1]["vol"]):
        try:
            info = cmc("/v2/cryptocurrency/info", f"id={q['id']}").get("data", {}).get(str(q["id"]), {})
        except Exception as e:  # noqa: BLE001
            print(f"  {sym}: info failed {str(e)[:60]}"); continue
        addr = None
        for c in info.get("contract_address", []) or []:
            plat = (c.get("platform", {}).get("name") or "").lower()
            if "bnb" in plat or plat == "binance smart chain":
                addr = c.get("contract_address"); break
        if not addr:
            print(f"  {sym}: no BSC contract"); continue
        if not gate_has(sym):
            print(f"  {sym}: no Gate pair (no taker data) — skip"); continue
        implied = twak_quote(addr)
        if implied is None:
            print(f"  {sym}: TWAK can't route {addr[:12]}… — skip"); continue
        dev = abs(implied / q["px"] - 1)
        if dev > 0.03:
            print(f"  {sym}: TWAK ${implied:.6g} vs CMC ${q['px']:.6g} dev {dev*100:.1f}% — DIVERGED, skip")
            continue
        print(f"  ✅ {sym}: vol ${q['vol']/1e6:.0f}M, TWAK ${implied:.6g} ≈ CMC (dev {dev*100:.1f}%), Gate ok")
        survivors.append((sym, addr, q["vol"], dev))

    print("\n=== ready-to-paste BSC_TOKENS lines (verify decimals on BscScan!) ===")
    for sym, addr, vol, dev in survivors:
        print(f'    "{sym}": ("{addr}", 18),  # verified {os.popen("date +%F").read().strip()}: '
              f"TWAK quote-by-addr dev {dev*100:.1f}% vs CMC, vol ${vol/1e6:.0f}M, Gate pair ok")


if __name__ == "__main__":
    main()
