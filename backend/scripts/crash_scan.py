"""Crash-resilience + pump-setup scanner over the 149 eligible tokens (CMC direct key).

Market is in extreme fear (F&G ~20). Goal: find tokens that AREN'T dumping with the market
(low correlation / independent catalyst) and that look ready to turn up — then rotate into the
best TWAK-tradeable one. Pure read-only research; trades nothing.

Run:  GRIDORA_CMC_API_KEY=… python -u -m scripts.crash_scan
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from gridora.adapters.exchanges.bsc_twak.bsc_tokens import BSC_TOKENS

KEY = os.getenv("GRIDORA_CMC_API_KEY", "")
BASE = os.getenv("GRIDORA_CMC_URL", "https://pro-api.coinmarketcap.com")

# eligible 149 (ASCII tickers; non-ASCII/junk skipped — can't query/­trade them anyway)
ELIGIBLE = ("ETH USDT USDC XRP TRX DOGE ZEC ADA LINK BCH DAI TON USD1 USDe M LTC AVAX SHIB XAUt "
            "WLFI H DOT UNI ASTER DEXE USDD ETC AAVE ATOM U STABLE FIL INJ NIGHT FET TUSD BONK "
            "PENGU CAKE SIREN LUNC ZRO KITE FDUSD BEAT PIEVERSE BTT NFT EDGE FLOKI LDO B FF PENDLE "
            "NEX STG AXS TWT HOME RAY COMP GWEI XCN GENIUS XPL BAT SKYAI APE IP SFP TAG NXPC AB "
            "SAHARA 1INCH CHEEMS BANANAS31 RIVER MYX RAVE SNX FORM LAB HTX USDf CTM BDX SLX UB DUCKY "
            "FRAX BILL WFI KOGE ALE FRXUSD USDF GOMINING VCNT GUA DUSD SMILEK 0G BEAM MY SOON REAL Q "
            "AIOZ ZIG YFI TAC lisUSD CYS ZAMA TRIA HUMA PLUME ZIL XPR ZETA BabyDoge NILA ROSE VELO "
            "UAI BRETT OPEN BSB TOSHI BAS ACH AXL LUR ELF KAVA APR IRYS EURI XUSD BARD DUSK SUSHI "
            "PEAQ COAI BDCA XAUM").split()

STABLE = {"USDT", "USDC", "DAI", "USD1", "USDe", "USDD", "TUSD", "FDUSD", "FRAX", "FRXUSD", "USDf",
          "USDF", "DUSD", "lisUSD", "XUSD", "EURI", "BILL", "STABLE"}
# eligible tokens we can actually trade safely through TWAK (verified contract in BSC_TOKENS)
TRADEABLE = {s for s in BSC_TOKENS} & set(ELIGIBLE)


def cmc(symbols):
    out = {}
    for i in range(0, len(symbols), 60):
        chunk = ",".join(symbols[i:i + 60])
        url = f"{BASE}/v2/cryptocurrency/quotes/latest?symbol={urllib.parse.quote(chunk)}&convert=USD"
        req = urllib.request.Request(url, headers={"X-CMC_PRO_API_KEY": KEY, "Accept": "application/json"})
        try:
            data = json.load(urllib.request.urlopen(req, timeout=40)).get("data", {})
        except Exception as e:  # noqa: BLE001
            print(f"  ! chunk {i} failed: {str(e)[:80]}")
            continue
        for sym, entries in data.items():
            if not entries:
                continue
            # v2 returns a LIST per symbol — pick the highest market-cap match (the real listed coin)
            best = max(entries, key=lambda c: (c.get("quote", {}).get("USD", {}).get("market_cap") or 0))
            q = best.get("quote", {}).get("USD", {})
            out[sym.upper()] = {
                "p": q.get("price") or 0, "h1": q.get("percent_change_1h"),
                "h24": q.get("percent_change_24h"), "d7": q.get("percent_change_7d"),
                "vol": q.get("volume_24h") or 0, "mc": q.get("market_cap") or 0,
            }
    return out


def main():
    if not KEY:
        raise SystemExit("set GRIDORA_CMC_API_KEY")
    q = cmc(ELIGIBLE)
    btc = cmc(["BTC"]).get("BTC", {})
    mkt = btc.get("h24") or 0.0
    print(f"\nMARKET ref: BTC 24h {mkt:+.1f}% | got {len(q)}/{len(ELIGIBLE)} eligible quotes\n")

    rows = []
    for s, d in q.items():
        if s in STABLE or d["h24"] is None or d["h1"] is None:
            continue
        h1, h24, d7, vol = d["h1"], d["h24"], d["d7"] or 0.0, d["vol"]
        if vol < 5e6:                                  # too illiquid to trade
            continue
        # RESILIENCE = how much it beats the market on 24h (positive = not dumping with the crash)
        resil = h24 - mkt
        # PUMP setup = turning up now (1h>0) AND holding up on 24h, with real volume
        pump = h1 + 0.5 * h24
        rows.append({"s": s, **d, "resil": resil, "pump": pump,
                     "trade": s in TRADEABLE})
    rows.sort(key=lambda r: -(r["resil"] + 0.3 * r["pump"]))

    def show(title, rs):
        print(title)
        print(f"  {'sym':<8}{'1h':>7}{'24h':>8}{'7d':>8}{'vsBTC':>8}{'vol$M':>8}  trade?")
        for r in rs:
            t = "✅ TWAK" if r["trade"] else "— (no TWAK addr)"
            print(f"  {r['s']:<8}{r['h1']:>+6.1f}%{r['h24']:>+7.1f}%{(r['d7'] or 0):>+7.1f}%"
                  f"{r['resil']:>+7.1f}%{r['vol']/1e6:>7.0f}  {t}")

    elig_trade = [r for r in rows if r["trade"]]
    show("=== TOP RESILIENT/PUMP — TRADEABLE via TWAK (actionable) ===", elig_trade[:10])
    print()
    show("=== TOP RESILIENT overall (incl. NOT-tradeable, info only) ===", rows[:12])
    print(f"\ntradeable eligible universe ({len(TRADEABLE)}): {sorted(TRADEABLE)}")


if __name__ == "__main__":
    main()
