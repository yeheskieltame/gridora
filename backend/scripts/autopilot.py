"""AUTOPILOT — the one unified autonomous dip-turn trader (replaces all scripts-local/ one-offs).

Distilled from the full live-trade audit (docs/superpowers/specs/2026-07-07-autopilot-design.md):
every win = dip-turn entry + anti-rugi ratchet exit; every loss = knife-catch, top-chase, or
gas-churn chop. This bot only ever does the winning pattern:

  FLAT    scan all verified tokens (one Gate tickers call) behind a BTC regime gate;
          dip filter (discounted, not parabolic, enough range/room) + turn confirm
          (higher-low base, 5m up, taker>=54) on 2 consecutive scans.
  ENTRY   TWAK quote-sanity (+/-3% vs Gate = landmine/divergence guard) then ALL-IN
          (USDT - reserve) by verified contract address. One position max.
  HOLDING CAP banks a +12% spike; fast-reversal (price-confirmed) once decently green;
          trailing ratchet arms at break-even (+$0.02) and never gives a green back;
          whipsaw guard (floor >= net >= -0.03) = never realize red on a gap.
          RED = HOLD to recover (hard rule). Red/stuck >24h -> notify user, don't sell.

State survives restarts (~/.gridora/autopilot.json, chain balance is truth in live mode);
every trade appends to ~/.gridora/trades.jsonl. Keepalive = launchd (com.gridora.autopilot).

Run:  python -u -m scripts.autopilot --paper     # simulated wallet, real market data
      python -u -m scripts.autopilot             # LIVE (mainnet env + TWAK creds)
      python -u -m scripts.autopilot --selfcheck # assert the decision math
      python -u -m scripts.autopilot --once      # single scan tick, then exit (debug)
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
from decimal import Decimal

# ---- calibration knobs (all in one place; tune here, not in forked copies) ----
SCAN_POLL = 120          # s between scans while flat
HOLD_POLL = 15           # s between manage ticks while holding
CONFIRM_SCANS = 2        # candidate must survive this many consecutive scans
BTC_1H_MIN = -1.2        # % — BTC dumping harder than this = stay cash
BTC_24H_MIN = -5.0
VOL_MIN = 1_000_000      # $ Gate 24h quote volume (dead books have useless taker flow)
CHG24_MAX = 9.0          # % — above = parabolic top, never buy (MOM_24H_MAX lesson)
CHG24_MIN = -20.0        # % — below = collapsing, not a dip
RANGE_MIN = 4.5          # % 24h high/low — tighter ranges can't clear the ~3% round-trip
POS_MAX = 0.45           # buy only in the lower 45% of the 24h range (a real discount)
ROOM_MIN = 3.5           # % to the 24h high — need profit room before the wall
TAKER_MIN = 54.0         # % buy-side of last 200 trades — buyers actually back
QUOTE_DEV_MAX = 0.03     # TWAK implied price may deviate this much from Gate
RESERVE = Decimal("0.5") # USDT dust buffer on all-in
BNB_MIN = 0.0012         # live: below this we can't pay gas — alert instead of buying
SELL_FRIC = 0.008        # round-trip LiquidMesh friction on the sell side
GAS_USD = 0.20           # both swaps' BNB gas, in USD, charged against net
ARM = 0.02               # $ net — ratchet arms the moment we're past break-even (anti-rugi)
FLOOR_MIN = 0.02         # $ net — the floor never sits below break-even
RIDE_GAP_PCT = 0.015     # trail gap as fraction of cost while BTC is strong (rides runners)
LOCK_GAP_PCT = 0.007     # tighter gap when BTC turns weak (lock fast)
CAP_PCT = 0.12           # +12% from fill -> bank the spike instantly
FAST_NET_PCT = 0.03      # fast-reversal only once net >= 3% of cost
FAST_5M = -1.5           # % 5m candle drop that confirms a real reversal
FAST_TAKER = 30.0        # taker collapse + price off peak also confirms
FAST_PX_OFF = 0.99       # ...price must be below 99% of its peak (not a bare taker blip)
STUCK_H = 24             # red/stuck this long -> notify the user (never auto-sell red)
REALERT_H = 12
COOLDOWN_MIN = 30        # per-token re-entry cooldown after an exit (no churn loops)
PAPER_USDT = 30.88       # paper wallet seed (mirrors the real one, 2026-07-07)

STATE_DIR = os.path.expanduser("~/.gridora")
STATE_F = os.path.join(STATE_DIR, "autopilot.json")
LEDGER_F = os.path.join(STATE_DIR, "trades.jsonl")

STABLES = {"USDT", "USDC", "FDUSD", "DAI", "WBNB", "BNB"}
THIN_TRAPS = {"AXS", "ZRO"}   # verified untradeable via TWAK (BSC liq $32-49k, price lags CEX)
GATE_PAIR = {"BTCB": "BTC"}   # Gate ticker symbol overrides
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}


def log(msg: str) -> None:
    print(f"{time.strftime('%m-%d %H:%M:%S')} {msg}", flush=True)


def get(url: str, post: bytes | None = None):
    try:
        return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA, data=post), timeout=15))
    except Exception:  # noqa: BLE001
        return None


def notify(title: str, body: str) -> None:
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification "{body}" with title "{title}"'],
                       capture_output=True, timeout=10)
    except Exception:  # noqa: BLE001
        pass


# ---------- state + ledger ----------
def load_state() -> dict:
    try:
        with open(STATE_F) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"pos": None, "peak": None, "px_peak": None, "pending": None,
                "cooldown": {}, "paper": {"USDT": PAPER_USDT}, "last_alert": 0}


def save_state(st: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_F + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1)
    os.replace(tmp, STATE_F)


def ledger(event: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    event["ts"] = time.time()
    event["at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LEDGER_F, "a") as f:
        f.write(json.dumps(event) + "\n")


# ---------- pure decision math (covered by --selfcheck) ----------
def net_of(qty: float, px: float, cost: float) -> float:
    return qty * px * (1 - SELL_FRIC) - GAS_USD - cost


def ratchet_floor(peak: float, gap: float) -> float:
    return max(FLOOR_MIN, peak - gap)


def should_lock(net: float, floor: float) -> bool:
    """Whipsaw guard: lock only at/near break-even or above — a gap below BE = HOLD."""
    return floor >= net >= -0.03


def dip_score(chg24: float, rng: float, pos: float, room: float, vol: float) -> float | None:
    """Prefilter a Gate ticker row; None = not a dip candidate."""
    if not (CHG24_MIN <= chg24 <= CHG24_MAX):
        return None
    if rng < RANGE_MIN or pos > POS_MAX or room < ROOM_MIN or vol < VOL_MIN:
        return None
    return (POS_MAX - pos) * 40 + room * 2


def base_formed(lows: list[float]) -> bool:
    """Higher-low check on 15m lows (oldest first): last 4 lows hold above the prior 8's
    floor -> a base, not a falling knife."""
    if len(lows) < 12:
        return False
    return min(lows[-4:]) >= min(lows[-12:-4]) * 0.998


# ---------- market data (Gate + Hyperliquid, the proven stack) ----------
def universe() -> list[str]:
    from gridora.adapters.exchanges.bsc_twak.bsc_tokens import BSC_TOKENS
    return [s for s in BSC_TOKENS if s not in STABLES | THIN_TRAPS]


def btc_gate() -> tuple[bool, str]:
    k = get("https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair=BTC_USDT&interval=1h&limit=25")
    if not isinstance(k, list) or len(k) < 25:
        return False, "no BTC data"
    closes = [float(c[2]) for c in k]
    c1h = (closes[-1] / closes[-2] - 1) * 100
    c24 = (closes[-1] / closes[0] - 1) * 100
    ok = c1h >= BTC_1H_MIN and c24 >= BTC_24H_MIN
    return ok, f"BTC ${closes[-1]:,.0f} 1h {c1h:+.1f}% 24h {c24:+.1f}%"


def all_tickers() -> dict[str, dict]:
    rows = get("https://api.gateio.ws/api/v4/spot/tickers")
    if not isinstance(rows, list):
        return {}
    pairs = {(GATE_PAIR.get(s, s) + "_USDT"): s for s in universe()}
    out = {}
    for r in rows:
        sym = pairs.get(r.get("currency_pair", ""))
        if not sym:
            continue
        try:
            px, hi, lo = float(r["last"]), float(r["high_24h"]), float(r["low_24h"])
            out[sym] = {"px": px, "chg24": float(r["change_percentage"]),
                        "rng": (hi / lo - 1) * 100 if lo else 0.0,
                        "pos": (px - lo) / (hi - lo) if hi > lo else 1.0,
                        "room": (hi / px - 1) * 100 if px else 0.0,
                        "vol": float(r["quote_volume"])}
        except (KeyError, ValueError, ZeroDivisionError):
            continue
    return out


def deep(sym: str) -> dict | None:
    pair = GATE_PAIR.get(sym, sym) + "_USDT"
    k15 = get(f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={pair}&interval=15m&limit=16")
    k5 = get(f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={pair}&interval=5m&limit=3")
    trd = get(f"https://api.gateio.ws/api/v4/spot/trades?currency_pair={pair}&limit=200")
    if not (isinstance(k15, list) and isinstance(k5, list) and isinstance(trd, list)):
        return None
    lows = [float(c[4]) for c in k15]          # gate candle: [ts, vol, close, high, low, open, ...]
    cl5 = [float(c[2]) for c in k5]
    m5 = (cl5[-1] / cl5[-2] - 1) * 100 if len(cl5) >= 2 else 0.0
    up = len(cl5) >= 2 and cl5[-1] >= cl5[-2]
    bb = sum(float(x["amount"]) * float(x["price"]) for x in trd if x["side"] == "buy")
    ss = sum(float(x["amount"]) * float(x["price"]) for x in trd if x["side"] == "sell")
    taker = bb / (bb + ss) * 100 if (bb + ss) else 50.0
    return {"base": base_formed(lows), "up": up, "m5": m5, "taker": taker}


def spot_px(sym: str) -> float | None:
    t = get(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={GATE_PAIR.get(sym, sym)}_USDT")
    return float(t[0]["last"]) if isinstance(t, list) and t else None


# ---------- execution (live = Trader/TWAK; paper = simulated fills at Gate px) ----------
class Live:
    def __init__(self):
        from gridora.adapters.exchanges.bsc_twak.twak_client import TwakClient
        from gridora.config import settings
        from scripts.live_active import Trader
        settings.guard(); settings.assert_twak_creds()
        wallet = settings.agent_address or asyncio.run(TwakClient(chain_key=settings.chain_key).address())
        self.tr = Trader(TwakClient(chain_key=settings.chain_key), settings.chain_key,
                         wallet, settings.bsc_rpc_url, universe())
        self.wallet = wallet

    def usdt(self) -> float:
        return float(asyncio.run(self.tr.bal("USDT")))

    def bal(self, sym: str) -> float:
        return float(asyncio.run(self.tr.bal(sym)))

    def bnb_ok(self) -> bool:
        from gridora.config import settings
        data = "0x"  # native balance via eth_getBalance
        import httpx
        r = httpx.post(settings.bsc_rpc_url, json={"jsonrpc": "2.0", "method": "eth_getBalance",
                       "params": [self.wallet, "latest"], "id": 1}, timeout=15)
        return int(r.json().get("result", "0x0"), 16) / 1e18 >= BNB_MIN

    def quote_ok(self, sym: str, gate_px: float) -> bool:
        """The landmine + divergence guard: TWAK's implied price must track Gate."""
        implied = float(asyncio.run(self.tr.price(sym)))
        if implied <= 0:
            return False
        dev = abs(implied / gate_px - 1)
        if dev > QUOTE_DEV_MAX:
            log(f"!! {sym} TWAK ${implied:.6g} vs Gate ${gate_px:.6g} dev {dev*100:.1f}% > {QUOTE_DEV_MAX*100:.0f}% — refuse")
            return False
        return True

    def buy(self, sym: str) -> tuple[float, float, float] | None:
        amt = Decimal(str(self.usdt())) - RESERVE
        if amt < 5:
            log(f"!! only ${float(amt)+float(RESERVE):.2f} USDT — cannot enter"); return None
        got = float(asyncio.run(self.tr.swap("USDT", sym, amt)))
        if got <= 0:
            log("!! BUY FAILED"); return None
        return got, float(amt), float(amt) / got

    def sell(self, sym: str, cost: float, reason: str) -> float | None:
        bal = asyncio.run(self.tr.bal(sym))
        if bal <= 0:
            return None
        u0 = asyncio.run(self.tr.bal("USDT"))
        got = float(asyncio.run(self.tr.swap(sym, "USDT", bal)))
        if got <= 0:
            log(f"!! SELL FAILED ({reason}) — still holding"); return None
        proceeds = float(asyncio.run(self.tr.bal("USDT"))) - float(u0)
        bps = int((proceeds - cost) / cost * 10000)
        asyncio.run(self.tr.journal(bps, f"{sym}-auto"))
        return proceeds


class Paper:
    def __init__(self, st: dict):
        self.st = st

    def usdt(self) -> float:
        return self.st["paper"].get("USDT", 0.0)

    def bal(self, sym: str) -> float:
        return self.st["paper"].get(sym, 0.0)

    def bnb_ok(self) -> bool:
        return True

    def quote_ok(self, sym: str, gate_px: float) -> bool:
        return True

    def buy(self, sym: str) -> tuple[float, float, float] | None:
        px = spot_px(sym)
        amt = self.usdt() - float(RESERVE)
        if px is None or amt < 5:
            return None
        qty = amt * (1 - SELL_FRIC) / px       # entry friction mirrors live fills
        self.st["paper"]["USDT"] -= amt
        self.st["paper"][sym] = qty
        return qty, amt, amt / qty

    def sell(self, sym: str, cost: float, reason: str) -> float | None:
        px = spot_px(sym)
        qty = self.bal(sym)
        if px is None or qty <= 0:
            return None
        proceeds = qty * px * (1 - SELL_FRIC) - GAS_USD
        self.st["paper"]["USDT"] += proceeds
        self.st["paper"].pop(sym, None)
        return proceeds


# ---------- the state machine ----------
def scan_tick(st: dict, ex) -> None:
    ok, btc = btc_gate()
    if not ok:
        st["pending"] = None
        log(f"REGIME OFF — {btc} — holding cash"); return
    tk = all_tickers()
    now = time.time()
    ranked = []
    for sym, r in tk.items():
        if now - st["cooldown"].get(sym, 0) < COOLDOWN_MIN * 60:
            continue
        s = dip_score(r["chg24"], r["rng"], r["pos"], r["room"], r["vol"])
        if s is not None:
            ranked.append((s, sym, r))
    ranked.sort(reverse=True)
    best = None
    for s, sym, r in ranked[:5]:
        d = deep(sym)
        if d and d["base"] and d["up"] and d["taker"] >= TAKER_MIN:
            best = (sym, r, d); break
    if not best:
        top = f" | best prefilter: {ranked[0][1]} (turn unconfirmed)" if ranked else ""
        st["pending"] = None
        log(f"scan: no dip-turn ({len(tk)} tickers, {len(ranked)} dips) | {btc}{top}"); return

    sym, r, d = best
    p = st.get("pending")
    count = p["count"] + 1 if p and p["sym"] == sym else 1
    st["pending"] = {"sym": sym, "count": count}
    log(f"CANDIDATE {sym} ${r['px']:.6g} | pos {r['pos']*100:.0f}% rng {r['rng']:.1f}% room {r['room']:.1f}% "
        f"| taker {d['taker']:.0f}% 5m {d['m5']:+.1f}% base ok | confirm {count}/{CONFIRM_SCANS} | {btc}")
    if count < CONFIRM_SCANS:
        return

    st["pending"] = None
    if not ex.bnb_ok():
        notify("Gridora Autopilot", "BNB gas habis — top up dulu, entry di-skip")
        log("!! BNB below gas floor — entry skipped"); return
    if not ex.quote_ok(sym, r["px"]):
        st["cooldown"][sym] = now; return
    fill = ex.buy(sym)
    if not fill:
        return
    qty, cost, eff = fill
    st["pos"] = {"sym": sym, "qty": qty, "cost": cost, "eff": eff, "ts": now}
    st["peak"] = None; st["px_peak"] = None; st["last_alert"] = 0
    ledger({"event": "buy", "sym": sym, "qty": qty, "cost": cost, "eff": eff,
            "taker": d["taker"], "pos": r["pos"]})
    notify("Gridora Autopilot", f"BUY {sym} ${cost:.2f} @ {eff:.6g}")
    log(f"✅ ALL-IN {qty:.6g} {sym} for ${cost:.2f} @ eff ${eff:.6g} | BE ~${(cost+GAS_USD)/(qty*(1-SELL_FRIC)):.6g}")


def manage_tick(st: dict, ex) -> None:
    pos = st["pos"]
    sym, qty, cost, eff = pos["sym"], pos["qty"], pos["cost"], pos["eff"]
    d = deep(sym)
    px = spot_px(sym)
    if px is None:
        return
    n = net_of(qty, px, cost)
    st["peak"] = n if st["peak"] is None else max(st["peak"], n)
    st["px_peak"] = px if st["px_peak"] is None else max(st["px_peak"], px)
    peak, px_peak = st["peak"], st["px_peak"]
    m5, taker = (d["m5"], d["taker"]) if d else (0.0, 50.0)
    btc_ok, btc = btc_gate()

    def close(reason: str) -> None:
        proceeds = ex.sell(sym, cost, reason)
        if proceeds is None:
            return
        pnl = proceeds - cost
        bps = int(pnl / cost * 10000)
        st["pos"] = None; st["peak"] = None; st["px_peak"] = None
        st["cooldown"][sym] = time.time()
        ledger({"event": "sell", "sym": sym, "qty": qty, "proceeds": proceeds,
                "net": pnl, "bps": bps, "reason": reason})
        notify("Gridora Autopilot", f"SELL {sym} net ${pnl:+.2f} ({bps:+d}bps) — {reason}")
        log(f"💰 SOLD {sym} -> ${proceeds:.2f} | net ${pnl:+.2f} ({bps:+d}bps) | {reason}")

    if px >= eff * (1 + CAP_PCT):
        log(f"TRIGGER=CAP {sym} ${px:.6g} (+{CAP_PCT*100:.0f}%) net ${n:+.2f}"); close("cap-bank"); return
    if n >= FAST_NET_PCT * cost and (m5 <= FAST_5M or (taker <= FAST_TAKER and px <= px_peak * FAST_PX_OFF)):
        log(f"TRIGGER=FAST-REVERSAL {sym} ${px:.6g} net +${n:.2f} 5m {m5:+.1f}% taker {taker:.0f}%")
        close("fast-reversal"); return
    if peak >= ARM:
        gap = cost * (RIDE_GAP_PCT if btc_ok else LOCK_GAP_PCT)
        floor = ratchet_floor(peak, gap)
        if should_lock(n, floor):
            log(f"TRIGGER=LOCK {sym} ${px:.6g} net +${n:.2f} (floor +${floor:.2f} peak +${peak:.2f})")
            close("trail-lock"); return

    age_h = (time.time() - pos["ts"]) / 3600
    if n < 0 and age_h >= STUCK_H and time.time() - st["last_alert"] >= REALERT_H * 3600:
        st["last_alert"] = time.time()
        msg = f"{sym} merah ${n:+.2f} sudah {age_h:.0f}h (px ${px:.6g}, taker {taker:.0f}%) — cut atau hold?"
        notify("Gridora Autopilot — STUCK", msg)
        log(f"⚠️ STUCK ALERT: {msg} | HOLDING (never-red; keputusan di user)")

    if int(time.time()) % 300 < HOLD_POLL:
        sl = f"+${ratchet_floor(peak, cost*(RIDE_GAP_PCT if btc_ok else LOCK_GAP_PCT)):.2f}" if peak is not None and peak >= ARM else "arms at BE"
        log(f"[hold {age_h:.1f}h] {sym} ${px:.6g} net ${n:+.2f} SL {sl} peak +${peak or 0:.2f} "
            f"| 5m {m5:+.1f}% taker {taker:.0f}% | {btc}")


def reconcile(st: dict, ex, paper: bool) -> None:
    """Chain is truth (live): a position sold/changed outside the bot must not ghost on."""
    if paper or not st["pos"]:
        return
    sym = st["pos"]["sym"]
    bal = ex.bal(sym)
    if bal * st["pos"]["eff"] < 2:
        log(f"reconcile: state had {sym} but chain shows none — clearing to FLAT")
        st["pos"] = None; st["peak"] = None; st["px_peak"] = None
    elif abs(bal - st["pos"]["qty"]) / st["pos"]["qty"] > 0.05:
        log(f"reconcile: {sym} qty {st['pos']['qty']:.6g} -> {bal:.6g} (chain)")
        st["pos"]["qty"] = bal


def selfcheck() -> None:
    # ratchet + whipsaw guard
    assert ratchet_floor(0.05, 0.45) == FLOOR_MIN                # tiny peak -> floor pinned at BE+
    assert ratchet_floor(1.00, 0.45) == 0.55                     # runner -> floor trails the peak
    assert should_lock(0.03, 0.55) and should_lock(-0.02, 0.02)  # fade to floor / BE -> lock
    assert not should_lock(-0.11, 0.02)                          # gapped below BE -> HOLD, never red
    assert not should_lock(0.80, 0.55)                           # above floor -> keep riding
    # net math: 1.5 qty @ $20 -> 30*(1-fric) - gas - 29 cost
    assert abs(net_of(1.5, 20.0, 29.0) - (30 * (1 - SELL_FRIC) - GAS_USD - 29.0)) < 1e-9
    # dip prefilter
    assert dip_score(2.0, 8.0, 0.30, 5.0, 5e6) is not None       # discounted, roomy, liquid = candidate
    assert dip_score(12.0, 8.0, 0.30, 5.0, 5e6) is None          # parabolic top = never
    assert dip_score(2.0, 3.0, 0.30, 5.0, 5e6) is None           # tight range = gas churn, skip
    assert dip_score(2.0, 8.0, 0.80, 2.0, 5e6) is None           # top of range = not a dip
    assert dip_score(2.0, 8.0, 0.30, 5.0, 1e5) is None           # illiquid = skip
    # knife vs base
    assert base_formed([10, 9.5, 9, 8.5, 8, 8, 8, 8, 8.1, 8.05, 8.2, 8.1])       # floor held = base
    assert not base_formed([10, 9.5, 9, 8.5, 8, 7.8, 7.6, 7.4, 7.2, 7.0, 6.8, 6.6])  # falling knife
    print("selfcheck OK")


def main() -> None:
    if "--selfcheck" in sys.argv:
        selfcheck(); return
    paper = "--paper" in sys.argv
    once = "--once" in sys.argv
    st = load_state()
    ex = Paper(st) if paper else Live()
    reconcile(st, ex, paper)
    mode = "PAPER" if paper else "LIVE"
    log(f"=== AUTOPILOT {mode} | universe {len(universe())} tokens | "
        f"{'holding ' + st['pos']['sym'] if st['pos'] else f'flat, USDT ${ex.usdt():.2f}'} ===")
    while True:
        try:
            if st["pos"]:
                manage_tick(st, ex)
            else:
                scan_tick(st, ex)
            save_state(st)
        except Exception as e:  # noqa: BLE001 — the loop must survive anything transient
            log(f"!! tick error: {str(e)[:140]}")
        if once:
            break
        time.sleep(HOLD_POLL if st["pos"] else SCAN_POLL)


if __name__ == "__main__":
    main()
