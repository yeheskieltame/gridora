"""Gridora PRO — a DRAWDOWN-FIRST MOMENTUM picker. LIVE on BSC via TWAK (real money).

The competition is won by the SMALLEST drawdown, not the flashiest PnL, and arb_scan PROVED
there's no arbitrage edge at our size (every round trip costs ~3%: LiquidMesh spread ~1.5% +
gas) — so the only thing that beats the fee is a DIRECTIONAL move bigger than ~3%. This strategy
rides that, with hard risk caps:

  • Capital PRESERVATION first. Default state = USDT (zero drawdown). Deploy only into a clear
    momentum setup, never more than DEPLOY_FRAC per position, always keep a USDT buffer.
  • Buy STRENGTH, not weakness: the strongest liquid token that's IGNORING the crash — holding up
    on 24h, OUTPERFORMING the market (vs BTC), in a real 7d uptrend. Ride a move that clears the
    ~3% cost. Data = CoinMarketCap (F&G + quotes via x402-through-TWAK), the competition-native path.
    (This replaces the old dip-buyer, which kept catching falling knives in extreme fear.)
  • DON'T over-trade. 10-minute cadence; a position is only touched at take-profit, stop-loss,
    or the portfolio drawdown guard. No 1%-wiggle churn.
  • HARD risk caps: per-position stop (SL) bounds each trade's loss; a portfolio drawdown guard
    flattens EVERYTHING to USDT and cools down if equity falls MAX_DD from its peak. That guard
    is the whole game — keep the drawdown the smallest in the field.
  • Every settled position is journaled on-chain to OUR TradeJournal (real PnL on the verifier).
  • A daily heartbeat guarantees the ≥1-trade/day rule even in a dead-quiet market.

Reuses the hardened Trader (RPC balances, receipt-verified swaps, $-notional pricing, journaling).

Run (mainnet env): python -u -m scripts.live_pro
"""
from __future__ import annotations

import asyncio
import time
from decimal import Decimal

from gridora.adapters.exchanges.bsc_twak.twak_client import TwakClient
from gridora.adapters.payments.x402 import X402Payments
from gridora.adapters.signals.cmc import CmcSignals, _fng_value
from gridora.config import settings
from scripts.live_active import Trader, USDT  # hardened swap/balance/equity/journal

# Tradeable + eligible (verified in BSC_TOKENS). Quality first.
# 2026-06-25 night: LOCKED to a single all-in LAB position (user "trade only 1 tonight, all-in").
# One-shot — no rotation; after LAB exits the bot sits cash (REENTRY_COOLDOWN huge). Restore the
# full list below for normal multi-token momentum mode.
UNIVERSE = ["LAB"]
# UNIVERSE = ["ETH", "UNI", "LINK", "LTC", "DOT", "ADA", "CAKE", "TWT", "DOGE", "XRP",
#             "PENGU", "FIL", "ASTER", "AXS", "WLFI", "BONK", "SLX", "RAVE", "ZRO", "AVAX", "LAB"]

# MANUAL picks: a token here is managed by a FIXED take-profit price (a chart target), NOT the
# momentum trailing — and the momentum picker won't auto-buy it. Set by a human call (e.g. "buy LAB,
# TP +3%"); cleared when it exits.
FIXED_TP: dict[str, Decimal] = {}   # cleared 2026-06-25: all-in LAB is managed by PROFIT-PROTECT (arm
                                    # +3%/trail 2.5%/floor +0.5%, disaster SL -7%), not a stale fixed target.
# Tokens here have NO stop-loss — a human "buy without SL" call. They exit ONLY at their FIXED_TP
# (or the portfolio drawdown guard). Used for a small volatile scalp where a tight SL would whipsaw.
NO_SL: set[str] = set()

POLL_S = 600              # 10 min — calm, no churn
MAX_DD = Decimal("0.070")  # flatten ALL + cooldown if equity falls this from its peak. Widened
                           # with SL so an oversold dip isn't whipsawed out before it bounces —
                           # extreme-fear dips wiggle hard. Still 4× below the 30% DQ.
TP = Decimal("0.030")     # PROFIT-PROTECT ARM. Once a position's PEAK reaches this gain (+3% = covers
                          # the round-trip cost), its stop ratchets UP into profit (see manage) — the
                          # −7% SL no longer applies. User rule: "naik kenceng → SL naik ke profit,
                          # 3% biar apapun yg terjadi always profit." So past +3% it can only exit GREEN.
TRAIL = Decimal("0.040")  # once armed, the profit-stop trails this far below the PEAK (ratchets up as
                          # the peak rises — rides a runner, books the gain on the reversal).
                          # 2026-06-25 LAB ride-to-#1: widened 2.5%->4% so the 37%-range token isn't
                          # clipped on a normal swing before it runs toward $18.5-21 (Binance-Alpha pop).
LOCK_MIN = Decimal("0.005")  # ...but the armed stop never sits below +0.5% (covers the exit fee) — so
                          # the moment it's armed at +3% the worst case is still a small GREEN exit.
# PROFIT-LOCK on momentum exhaustion (user rule: "play safe — once in profit covering fees AND the
# rise slows / volume fades, SELL to keep the profit for something new"). Fires BEFORE the trailing
# arm, so a net-green position is banked the moment its momentum stalls instead of round-tripping back.
PROFIT_LOCK = Decimal("0.060")  # min gross gain before the on-stall lock may fire. 2026-06-25 LAB ride-to-#1:
                                # raised 3%->6% so a stall at the first +3% breather does NOT bank us BELOW
                                # #1 (+5.34%) — only force-bank once we've already beaten #1 (+6%) and stall.
EXHAUST_1H = 0.3          # ...and the token's 1h rise has stalled to ≤ this % (no longer pushing up)
SL = Decimal("0.070")     # stop loss per position — wide on purpose; the only forced exit (we never
                          # rotate OUT of a red position to chase — hold it to recover)
# --- MOMENTUM entry (replaces the old dip-buyer): buy STRENGTH that's ignoring the crash, not
# weakness. arb_scan proved there's no arbitrage at our size, so the only edge is a directional
# move bigger than the ~3% round-trip cost — i.e. ride a token already trending up + outperforming. ---
MOM_24H_MIN = -2.0       # don't buy anything dropping more than this on 24h (must be holding up)
MOM_24H_MAX = 9.0        # ...but DON'T buy a token already up more than this on 24h — that's a
                         # parabolic/blow-off top (the bot bought SLX at +14% 24h right at its peak).
MOM_7D_MIN = 2.0         # require a real 7d UPTREND (the momentum we're riding)
REENTRY_COOLDOWN = 99999  # 2026-06-25 one-shot: after LAB exits, do NOT auto-rebuy — sit 100% cash and
                          # let the 2h cron + monitor wake me to reassess. (Normal value: 6 ≈ 1h anti-churn.)
MIN_VOL = 20e6           # liquidity floor
MAX_POS = 2              # at most this many open positions (rest stays USDT) — concentrated; more
                         # positions = smaller size = more gas drag on a tiny account
DEPLOY_FRAC = Decimal("0.45")  # fraction of equity per entry (always leaves a USDT buffer)
STABLES_SYM = {"USDT", "USDC", "FDUSD", "DAI"}   # never "buy" a stable as a momentum pick
USDT_FLOOR = Decimal("2")
HEARTBEAT_S = 22 * 3600   # force a tiny trade if none in this long (≥1-trade/day rule)
COOLDOWN_CYCLES = 6       # after a drawdown-guard trip, pause entries this many polls


async def main() -> None:
    settings.guard()
    settings.assert_twak_creds()
    twak = TwakClient(chain_key=settings.chain_key)
    tr = Trader(twak, settings.chain_key, settings.agent_address, settings.bsc_rpc_url, UNIVERSE)
    cmc = CmcSignals(X402Payments(twak, max_payment=settings.x402_max_payment, prefer_network="bsc"),
                     base_url=settings.cmc_base_url, api_key=settings.cmc_api_key)

    async def fng() -> int:
        try:
            return _fng_value(await cmc._fetch("/v3/fear-and-greed/latest", {}))
        except Exception:  # noqa: BLE001
            return 50

    async def metrics() -> dict:
        try:
            return await cmc.market_metrics(UNIVERSE + ["BTC"])   # +BTC = market resilience baseline
        except Exception:  # noqa: BLE001
            return {}

    # ---- startup: ADOPT existing holdings as live positions (re-anchored to the current price),
    # so a restart keeps good dips instead of flattening them. A fresh wallet (USDT only) just
    # starts flat and the loop deploys on the first qualifying dip. ----
    print("=== Gridora PRO — drawdown-first MOMENTUM picker | adopting existing holdings ===", flush=True)
    positions: dict[str, dict] = {}     # sym -> {"entry": Decimal, "qty": Decimal, "peak": Decimal}
    for sym in UNIVERSE:
        b = await tr.bal(sym)
        if b > 0:
            px = await tr.price(sym)
            if b * px > Decimal("0.5"):
                positions[sym] = {"entry": px, "qty": b, "peak": px}
                print(f"  adopt {sym}: {float(b):.4g} @ ${float(px):.5f} (entry re-anchored to now)", flush=True)

    start_eq = await tr.equity()
    peak_eq = start_eq
    last_trade = time.time()
    cooldown = 0
    dd_breaches = 0          # consecutive polls below the drawdown line — guard needs 2 to fire
    token_cd: dict[str, int] = {}   # sym -> polls remaining before it may be re-bought (anti-churn)
    print(f"=== running | equity ${start_eq:.2f} | MAX_DD {MAX_DD*100}% | arm {TP*100}% trail {TRAIL*100}% "
          f"SL {SL*100}% | 24h-band [{MOM_24H_MIN},{MOM_24H_MAX}] | poll {POLL_S//60}min ===", flush=True)

    while True:
        await asyncio.sleep(POLL_S)
        try:
            fg = await fng()
            mk = await metrics()
            eq = await tr.equity()
        except Exception as e:  # noqa: BLE001
            print(f"  ! data error: {str(e)[:80]}", flush=True)
            continue
        peak_eq = max(peak_eq, eq)
        dd = float((peak_eq - eq) / peak_eq * 100) if peak_eq > 0 else 0.0
        print(f"\n[{time.strftime('%H:%M')}] F&G {fg} | equity ${eq:.2f} | peak ${peak_eq:.2f} | "
              f"dd {dd:.2f}% | positions {list(positions)}", flush=True)

        # 1) DRAWDOWN GUARD — the prime directive. Flatten everything, cool down. REQUIRES
        #    CONFIRMATION: a single anomalous equity read (a transient TWAK quote glitch valued a held
        #    token at ~$0 and "halved" equity → a 43% false dd that needlessly flattened WLFI) must
        #    NOT trigger a panic-flatten. Only act when the breach persists across 2 consecutive polls.
        if eq <= peak_eq * (Decimal(1) - MAX_DD):
            dd_breaches += 1
            if dd_breaches < 2:
                print(f"  ~ drawdown breach {dd:.2f}% (1/2 — confirming next poll before flattening)", flush=True)
            else:
                print(f"  !! DRAWDOWN GUARD {dd:.2f}% ≥ {MAX_DD*100}% (confirmed) — flatten ALL + cooldown", flush=True)
                for sym in list(positions):
                    m = mk.get(sym)
                    exit_px = Decimal(str(m.price_usd)) if m else await tr.price(sym)
                    entry = positions[sym]["entry"]
                    b = await tr.bal(sym)
                    if b > 0 and await tr.swap(sym, "USDT", b) > 0:
                        await tr.journal(int((exit_px / entry - 1) * 10000) if entry > 0 else 0, f"{sym}-ddguard")
                    positions.pop(sym, None)
                peak_eq = eq
                cooldown = COOLDOWN_CYCLES
                last_trade = time.time()
                dd_breaches = 0
                continue
        else:
            dd_breaches = 0      # equity back above the line — reset the confirmation counter

        # 2) MANAGE open positions — TRAILING take-profit + stop-loss. Once a position is up TP, we
        #    don't clip it — we ride and exit only when it falls TRAIL from its PEAK. So a strong runner
        #    keeps running, and a parabolic top is sold on the reversal (not clipped early then chased
        #    back in). SL is the hard floor. An exited token goes into REENTRY_COOLDOWN (no churn).
        for sym in list(positions):
            m = mk.get(sym)
            px = Decimal(str(m.price_usd)) if m else await tr.price(sym)
            entry = positions[sym]["entry"]
            if entry <= 0:
                continue
            positions[sym]["peak"] = peak = max(positions[sym].get("peak", entry), px)
            ret = px / entry - 1
            # PROFIT-LOCK on momentum exhaustion (play safe): once NET-GREEN (≥ round-trip cost) AND the
            # token's 1h rise has STALLED (≤ EXHAUST_1H, no longer pushing up), bank it NOW — keep the
            # profit for something new instead of round-tripping the gain back. Applies to every position.
            if m is not None and ret >= PROFIT_LOCK and m.pct_1h <= EXHAUST_1H:
                b = await tr.bal(sym)
                print(f"  LOCK {sym} +{float(ret)*100:.2f}% @ ${float(px):.6g} (1h {m.pct_1h:+.1f}% stalled) -> USDT", flush=True)
                if b > 0 and await tr.swap(sym, "USDT", b) > 0:
                    await tr.journal(int(ret * 10000), f"{sym}-lock")
                    last_trade = time.time()
                token_cd[sym] = REENTRY_COOLDOWN
                positions.pop(sym, None)
                continue
            # MANUAL pick → FIXED take-profit at the chart target (sell when price touches it), not
            # momentum trailing. SL still the floor UNLESS the token is in NO_SL (a human "no-SL" scalp).
            ftp = FIXED_TP.get(sym)
            if ftp is not None:
                hit_sl = (sym not in NO_SL) and ret <= -SL
                if px >= ftp or hit_sl:
                    kind = "TP-fixed" if px >= ftp else "SL"
                    b = await tr.bal(sym)
                    print(f"  {kind} {sym} {float(ret)*100:+.2f}% @ ${float(px):.6g} "
                          f"(target ${float(ftp):.4g}) -> USDT", flush=True)
                    if b > 0 and await tr.swap(sym, "USDT", b) > 0:
                        await tr.journal(int(ret * 10000), f"{sym}-{kind}")
                        last_trade = time.time()
                    token_cd[sym] = REENTRY_COOLDOWN
                    positions.pop(sym, None)
                continue                                        # fixed-TP token never uses trailing
            # PROFIT-PROTECT stop: once PEAK ≥ +TP (+3%), the stop ratchets UP into profit — it trails
            # TRAIL below the peak but never below break-even+ (LOCK_MIN), so a position that has rallied
            # past +3% can ONLY exit GREEN. The −7% SL applies ONLY before it ever reaches +3% (a trade
            # that never worked). "Naik kenceng → SL naik ke profit, always profit."
            armed = peak >= entry * (Decimal(1) + TP)
            if armed:
                stop = max(peak * (Decimal(1) - TRAIL), entry * (Decimal(1) + LOCK_MIN))
                hit_sl, hit_trail = False, px <= stop
            else:
                hit_sl, hit_trail = ret <= -SL, False
            if hit_sl or hit_trail:
                kind = "SL" if hit_sl else "TP-trail"
                b = await tr.bal(sym)
                print(f"  {kind} {sym} {float(ret)*100:+.2f}% @ ${float(px):.6g} "
                      f"(peak {float((peak/entry-1)*100):+.1f}%) -> USDT", flush=True)
                if b > 0 and await tr.swap(sym, "USDT", b) > 0:
                    await tr.journal(int(ret * 10000), f"{sym}-{kind}")
                    last_trade = time.time()
                token_cd[sym] = REENTRY_COOLDOWN               # lock re-entry → no TP-then-rebuy churn
                positions.pop(sym, None)

        # 3) ENTRY — MOMENTUM-picker: deploy free USDT into the STRONGEST resilient riser (holding
        #    up on 24h, outperforming the market, in a real 7d uptrend), F&G-agnostic. We never
        #    rotate OUT of a red position to fund this (manage step only exits at TP/SL) — momentum
        #    governs only what we BUY when a slot is already free.
        if cooldown > 0:
            cooldown -= 1
        for s in list(token_cd):                           # age out per-token re-entry cooldowns
            token_cd[s] -= 1
            if token_cd[s] <= 0:
                del token_cd[s]
        usdt = await tr.bal("USDT")
        if cooldown == 0 and len(positions) < MAX_POS and usdt >= USDT_FLOOR and mk:
            btc = mk.get("BTC")
            mkt_24h = btc.pct_24h if btc else 0.0          # market crash baseline
            cands = []
            for sym, m in mk.items():
                if sym in positions or sym in STABLES_SYM or sym == "BTC" or sym in token_cd:
                    continue                               # token_cd = just exited → don't churn back in
                rs = m.pct_24h - mkt_24h                    # relative strength vs market on 24h
                if (MOM_24H_MIN <= m.pct_24h <= MOM_24H_MAX and rs >= 0 and m.pct_7d >= MOM_7D_MIN
                        and float(m.vol_24h_usd) >= MIN_VOL):   # in the momentum band, not parabolic
                    cands.append((sym, m, rs))
            # strongest = 7d trend weighted by CURRENT relative strength (skip a faded 7d pump)
            cands.sort(key=lambda c: c[1].pct_7d + 2 * c[2], reverse=True)
            if cands:
                sym, m, rs = cands[0]
                spend = min(usdt, eq * DEPLOY_FRAC)
                if spend >= USDT_FLOOR:
                    print(f"  BUY-MOM {sym} 24h {m.pct_24h:+.1f}% (vsMkt {rs:+.1f}) 7d {m.pct_7d:+.1f}% "
                          f"(F&G {fg}) — deploy ${float(spend):.2f}", flush=True)
                    got = await tr.swap("USDT", sym, spend)
                    if got > 0:
                        px0 = Decimal(str(m.price_usd))
                        positions[sym] = {"entry": px0, "qty": got, "peak": px0}
                        last_trade = time.time()

        # 4) DAILY HEARTBEAT — guarantee ≥1 trade/day even if nothing above fired
        if time.time() - last_trade > HEARTBEAT_S:
            print("  heartbeat — tiny round-trip to satisfy ≥1 trade/day", flush=True)
            g = await tr.swap("USDT", "ETH", Decimal("2"))   # ETH = deepest, lowest-slippage
            if g > 0:
                await asyncio.sleep(5)
                await tr.swap("ETH", "USDT", g)
            last_trade = time.time()


if __name__ == "__main__":
    asyncio.run(main())
