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
RANGE_MAX = 18.0         # % — wider = post-crash chaos, not a dip venue (LAB post-mortem
                         # 2026-07-07: rng 36% AND the old score REWARDED it via room ×2)
WILD7D_MAX = 60.0        # % 7d high/low swing — above = collapsed parabola / dead-cat regime
                         # (LAB's week was 230%: $18.45 high, $5.58 low). Structural guard.
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
CAP_PCT = 0.06           # bank a spike at +6% — backtest-derived (2026-07-07, 30d+14d
                         # windows: 0.06 = only consistently-positive CAP w/ lowest DD;
                         # 0.12 missed the SLX spike then rode it -70%. See autopilot_backtest)
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
# AXS/ZRO: untradeable via TWAK (BSC liq $32-49k, price lags CEX). LAB: thin low-float
# chaos, burned us 4x (−$1.29, −$0.76, −$4+ 2026-07-07 post-mortem). TOSHI: BSC pool
# diverges from Gate on spikes (scratch-or-loss lottery). RAVE: thin parabolic, BSC price
# once detached 42% from Gate. SLX: repeat catastrophic collapser in every backtest window.
THIN_TRAPS = {"AXS", "ZRO", "LAB", "TOSHI", "RAVE", "SLX"}
GATE_PAIR = {"BTCB": "BTC"}   # Gate ticker symbol overrides
UA = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}

# ---- Hyperliquid whale lens (2026-07-07). Perp book imbalance + OI delta + funding as
# a candidate TIEBREAK and extreme-sell-wall veto — NOT a hard gate (no historical L2
# data exists to backtest one). Every buy ledgers its whale read so the signal's real
# predictive value gets measured from our own trades over time.
HL_URL = "https://api.hyperliquid.xyz/info"
HL_MAP = {  # our symbol -> HL perp name (probed 2026-07-07; 29/42 listed)
    "CAKE": "CAKE", "ETH": "ETH", "BTCB": "BTC", "XRP": "XRP", "ADA": "ADA",
    "DOGE": "DOGE", "LINK": "LINK", "DOT": "DOT", "LTC": "LTC", "UNI": "UNI",
    "PENGU": "PENGU", "FIL": "FIL", "ASTER": "ASTER", "WLFI": "WLFI", "BONK": "kBONK",
    "AVAX": "AVAX", "AAVE": "AAVE", "TRX": "TRX", "ZEC": "ZEC", "BCH": "BCH",
    "FET": "FET", "XPL": "XPL", "INJ": "INJ", "SHIB": "kSHIB", "ETC": "ETC",
    "PENDLE": "PENDLE", "ATOM": "ATOM", "STG": "STG", "BRETT": "BRETT",
}
HL_BAND = 0.02        # count book notional within ±2% of mid
HL_VETO_IMB = 0.38    # bids under 38% of near-mid notional = active sell wall, skip this round
HL_BONUS_W = 40.0     # score bonus per unit of (imbalance - 0.5): 60% bids -> +4 points


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


MODE = "paper"  # set once in main(); stamped on every ledger row


def ledger(event: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    event["mode"] = MODE
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
    if rng < RANGE_MIN or rng > RANGE_MAX or pos > POS_MAX or room < ROOM_MIN or vol < VOL_MIN:
        return None
    return (POS_MAX - pos) * 40 + room * 2


def wild_range(highs: list[float], lows: list[float]) -> float:
    """7d high/low swing in % — the collapsed-parabola / dead-cat detector (pure)."""
    if not highs or not lows or min(lows) <= 0:
        return 0.0
    return (max(highs) / min(lows) - 1) * 100


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
    k1d = get(f"https://api.gateio.ws/api/v4/spot/candlesticks?currency_pair={pair}&interval=1d&limit=10")
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
    # the daily-scale view the LAB post-mortem demanded: 7d wildness + closes for the brain
    d_hi = [float(c[3]) for c in k1d[-7:]] if isinstance(k1d, list) else []
    d_lo = [float(c[4]) for c in k1d[-7:]] if isinstance(k1d, list) else []
    d_cl = [float(c[2]) for c in k1d] if isinstance(k1d, list) else []
    return {"base": base_formed(lows), "up": up, "m5": m5, "taker": taker,
            "wild7d": wild_range(d_hi, d_lo), "d_closes": d_cl}


def spot_px(sym: str) -> float | None:
    t = get(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={GATE_PAIR.get(sym, sym)}_USDT")
    return float(t[0]["last"]) if isinstance(t, list) and t else None


# ---------- Hyperliquid whale lens ----------
def book_imbalance(bids: list[tuple[float, float]], asks: list[tuple[float, float]],
                   band: float = HL_BAND) -> float | None:
    """Bid share of near-mid notional: >0.5 = buyers stacking, <0.5 = sell wall.
    bids/asks = [(px, sz)] best-first. Pure (covered by --selfcheck)."""
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2
    b = sum(px * sz for px, sz in bids if px >= mid * (1 - band))
    a = sum(px * sz for px, sz in asks if px <= mid * (1 + band))
    return b / (b + a) if (b + a) > 0 else None


_hl_ctx_cache: dict = {"ts": 0.0, "oi": {}, "funding": {}}


def hl_ctxs() -> dict:
    """One metaAndAssetCtxs call covers every coin — cache it per scan (60s)."""
    if time.time() - _hl_ctx_cache["ts"] < 60:
        return _hl_ctx_cache
    r = get(HL_URL, post=json.dumps({"type": "metaAndAssetCtxs"}).encode())
    try:
        meta, ctxs = r
        oi, fund = {}, {}
        for asset, ctx in zip(meta["universe"], ctxs):
            oi[asset["name"]] = float(ctx.get("openInterest") or 0)
            fund[asset["name"]] = float(ctx.get("funding") or 0)
        _hl_ctx_cache.update(ts=time.time(), oi=oi, funding=fund)
    except Exception:  # noqa: BLE001 — HL down = lens off, bot unaffected
        pass
    return _hl_ctx_cache


def hl_whale(sym: str, st: dict) -> dict | None:
    """{imb, oi_chg_pct, funding} for a candidate, or None when unlisted/unreachable."""
    coin = HL_MAP.get(sym)
    if not coin:
        return None
    book = get(HL_URL, post=json.dumps({"type": "l2Book", "coin": coin}).encode())
    try:
        raw_b, raw_a = book["levels"][0], book["levels"][1]
        imb = book_imbalance([(float(x["px"]), float(x["sz"])) for x in raw_b],
                             [(float(x["px"]), float(x["sz"])) for x in raw_a])
    except Exception:  # noqa: BLE001
        return None
    if imb is None:
        return None
    ctx = hl_ctxs()
    oi_now = ctx["oi"].get(coin)
    prev = st.setdefault("hl_oi", {}).get(sym)
    oi_chg = ((oi_now / prev - 1) * 100) if (oi_now and prev) else None
    if oi_now:
        st["hl_oi"][sym] = oi_now
    return {"imb": imb, "oi_chg": oi_chg, "funding": ctx["funding"].get(coin)}


# ---------- the brain: Claude (Opus) as mandatory pre-entry risk officer ----------
# The LAB post-mortem's third layer: mechanical filters can't smell token character.
# Before ANY buy, a headless `claude --print --model opus` call reviews the full brief
# and may VETO. Veto-only power: it cannot force an entry, cannot touch exits, and the
# deterministic guardrails (never-red, quote-dev, knife, wild7d) still hard-enforce.
# WAJIB per user 2026-07-07: no verdict (CLI down/timeout) = no entry.
BRAIN_MODEL = os.environ.get("GRIDORA_BRAIN_MODEL", "opus")


def parse_verdict(text: str) -> dict | None:
    """Extract {"enter": bool, "confidence": int, "reason": str} from model output (pure)."""
    try:
        i, j = text.index("{"), text.rindex("}") + 1
        v = json.loads(text[i:j])
        if isinstance(v.get("enter"), bool):
            return {"enter": v["enter"], "confidence": int(v.get("confidence", 0)),
                    "reason": str(v.get("reason", ""))[:200]}
    except Exception:  # noqa: BLE001
        pass
    return None


def brain_verdict(sym: str, brief: dict) -> dict | None:
    prompt = (
        "You are the final risk officer for a small live spot-trading bot on BNB Chain "
        "(~$30 all-in per trade, dip-buy only, NEVER sells at a loss — a bad entry traps "
        "the whole stack for days). Mechanical filters already passed this candidate. "
        "Your ONLY job is to veto entries with character problems the filters miss: "
        "collapsed parabolas, dead-cat bounces, thin/low-float or manipulated chaos, "
        "post-pump distribution, macro risk-off, anything a seasoned trader walks away "
        "from. Study daily_closes_10d for the multi-day story. Be strict — a missed win "
        "costs little, a trapped position costs days. DATA:\n"
        + json.dumps(brief)
        + '\nReply with STRICT JSON only: {"enter": true|false, "confidence": 0-100, '
        '"reason": "<max 140 chars>"}'
    )
    try:
        p = subprocess.run(["claude", "--print", "--model", BRAIN_MODEL, prompt],
                           capture_output=True, text=True, timeout=180)
        return parse_verdict(p.stdout or "")
    except Exception as e:  # noqa: BLE001
        log(f"!! brain error: {str(e)[:100]}")
        return None


def hl_str(w: dict | None) -> str:
    if not w:
        return "HL n/a"
    oi = f" OI {w['oi_chg']:+.1f}%" if w.get("oi_chg") is not None else ""
    fu = f" fund {w['funding']*100:+.4f}%" if w.get("funding") is not None else ""
    return f"HL bid {w['imb']*100:.0f}%{oi}{fu}"


# ---------- execution (live = Trader/TWAK; paper = simulated fills at Gate px) ----------
class Live:
    def __init__(self):
        from gridora.adapters.exchanges.bsc_twak.twak_client import TwakClient
        from gridora.config import settings
        from scripts.live_active import Trader
        settings.guard(); settings.assert_twak_creds()
        # ONE persistent loop for every call — the REST transport caches an httpx client
        # bound to its first loop; asyncio.run-per-call would close it ("Event loop is closed").
        self.loop = asyncio.new_event_loop()
        wallet = settings.agent_address or self.loop.run_until_complete(
            TwakClient(chain_key=settings.chain_key).address())
        self.tr = Trader(TwakClient(chain_key=settings.chain_key), settings.chain_key,
                         wallet, settings.bsc_rpc_url, universe())
        self.wallet = wallet

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def usdt(self) -> float:
        return float(self._run(self.tr.bal("USDT")))

    def bal(self, sym: str) -> float:
        return float(self._run(self.tr.bal(sym)))

    def bnb_ok(self) -> bool:
        from gridora.config import settings
        data = "0x"  # native balance via eth_getBalance
        import httpx
        r = httpx.post(settings.bsc_rpc_url, json={"jsonrpc": "2.0", "method": "eth_getBalance",
                       "params": [self.wallet, "latest"], "id": 1}, timeout=15)
        return int(r.json().get("result", "0x0"), 16) / 1e18 >= BNB_MIN

    def quote_ok(self, sym: str, gate_px: float) -> bool:
        """The landmine + divergence guard: TWAK's implied price must track Gate."""
        implied = float(self._run(self.tr.price(sym)))
        if implied <= 0:
            return False
        dev = abs(implied / gate_px - 1)
        if dev > QUOTE_DEV_MAX:
            log(f"!! {sym} TWAK ${implied:.6g} vs Gate ${gate_px:.6g} dev {dev*100:.1f}% > {QUOTE_DEV_MAX*100:.0f}% — refuse")
            return False
        return True

    def buy(self, sym: str) -> tuple[float, float, float] | None:
        before = self.usdt()
        amt = Decimal(str(before)) - RESERVE
        if amt < 5:
            log(f"!! only ${before:.2f} USDT — cannot enter"); return None
        got = 0.0
        try:
            got = float(self._run(self.tr.swap("USDT", sym, amt)))
        except Exception as e:  # noqa: BLE001
            log(f"!! swap raised: {str(e)[:110]}")
        if got <= 0:
            # a swap can LAND on-chain while the client errors (seen live 2026-07-07:
            # LAB filled, response lost) — chain is truth, check before declaring failure
            tok = self.bal(sym)
            spent = before - self.usdt()
            if tok > 0 and spent > 2:
                log(f"!! swap reported failure but {tok:.6g} {sym} landed on-chain (${spent:.2f} spent) — adopting fill")
                return tok, spent, spent / tok
            log("!! BUY FAILED"); return None
        return got, float(amt), float(amt) / got

    def sell(self, sym: str, cost: float, reason: str) -> float | None:
        bal = self._run(self.tr.bal(sym))
        if bal <= 0:
            return None
        u0 = self._run(self.tr.bal("USDT"))
        got = 0.0
        try:
            got = float(self._run(self.tr.swap(sym, "USDT", bal)))
        except Exception as e:  # noqa: BLE001
            log(f"!! sell swap raised: {str(e)[:110]}")
        if got <= 0 and self.bal(sym) <= float(bal) * 0.01:
            got = 1.0  # tokens are gone on-chain -> the sell actually landed; fall through
        if got <= 0:
            log(f"!! SELL FAILED ({reason}) — still holding"); return None
        proceeds = float(self._run(self.tr.bal("USDT"))) - float(u0)
        bps = int((proceeds - cost) / cost * 10000)
        self._run(self.tr.journal(bps, f"{sym}-auto"))
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
    passers = []
    for s, sym, r in ranked[:5]:
        d = deep(sym)
        if not (d and d["base"] and d["up"] and d["taker"] >= TAKER_MIN):
            continue
        if d["wild7d"] > WILD7D_MAX:
            log(f"WILD {sym} — 7d swing {d['wild7d']:.0f}% > {WILD7D_MAX:.0f}% = "
                "collapsed-parabola regime, not a dip — skip")
            continue
        w = hl_whale(sym, st)
        if w and w["imb"] < HL_VETO_IMB:
            log(f"VETO {sym} — {hl_str(w)} = active sell wall on the perp book, skip this round")
            continue
        bonus = (w["imb"] - 0.5) * HL_BONUS_W if w else 0.0
        passers.append((s + bonus, sym, r, d, w))
    if not passers:
        top = f" | best prefilter: {ranked[0][1]} (turn unconfirmed)" if ranked else ""
        st["pending"] = None
        log(f"scan: no dip-turn ({len(tk)} tickers, {len(ranked)} dips) | {btc}{top}"); return

    passers.sort(key=lambda x: -x[0])
    _, sym, r, d, w = passers[0]
    p = st.get("pending")
    count = p["count"] + 1 if p and p["sym"] == sym else 1
    st["pending"] = {"sym": sym, "count": count}
    log(f"CANDIDATE {sym} ${r['px']:.6g} | pos {r['pos']*100:.0f}% rng {r['rng']:.1f}% room {r['room']:.1f}% "
        f"| taker {d['taker']:.0f}% 5m {d['m5']:+.1f}% base ok | {hl_str(w)} | confirm {count}/{CONFIRM_SCANS} | {btc}")
    if count < CONFIRM_SCANS:
        return

    st["pending"] = None
    # THE BRAIN GATE (wajib): Opus reviews the full brief; no verdict = no entry.
    brief = {"candidate": sym, "price_usd": r["px"], "pos_in_24h_range": round(r["pos"], 3),
             "range_24h_pct": round(r["rng"], 1), "room_to_24h_high_pct": round(r["room"], 1),
             "chg_24h_pct": round(r["chg24"], 1), "taker_buy_pct": round(d["taker"], 0),
             "chg_5m_pct": round(d["m5"], 2), "wild7d_swing_pct": round(d["wild7d"], 0),
             "daily_closes_10d": d["d_closes"],
             "hyperliquid_whale": w, "btc_regime": btc}
    v = brain_verdict(sym, brief)
    if v is None:
        log(f"!! brain unavailable for {sym} — entry skipped (no verdict = no entry)")
        st["cooldown"][sym] = now; return
    if not v["enter"]:
        log(f"🧠 BRAIN VETO {sym} ({v['confidence']}%): {v['reason']}")
        notify("Gridora Brain", f"VETO {sym}: {v['reason'][:90]}")
        st["cooldown"][sym] = now; return
    log(f"🧠 BRAIN GO {sym} ({v['confidence']}%): {v['reason']}")
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
            "taker": d["taker"], "pos": r["pos"], "wild7d": d["wild7d"],
            "hl_imb": w["imb"] if w else None,
            "hl_oi_chg": w.get("oi_chg") if w else None,
            "hl_funding": w.get("funding") if w else None,
            "llm_confidence": v["confidence"], "llm_reason": v["reason"]})
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
            f"| 5m {m5:+.1f}% taker {taker:.0f}% | {hl_str(hl_whale(sym, st))} | {btc}")


def reconcile(st: dict, ex, paper: bool) -> None:
    """Chain is truth (live): a position sold/changed outside the bot must not ghost on,
    and a landed swap whose response was lost must not leave holdings unmanaged."""
    if paper:
        return
    if st["pos"]:
        sym = st["pos"]["sym"]
        bal = ex.bal(sym)
        if bal * st["pos"]["eff"] < 2:
            log(f"reconcile: state had {sym} but chain shows none — clearing to FLAT")
            st["pos"] = None; st["peak"] = None; st["px_peak"] = None
        elif abs(bal - st["pos"]["qty"]) / st["pos"]["qty"] > 0.05:
            log(f"reconcile: {sym} qty {st['pos']['qty']:.6g} -> {bal:.6g} (chain)")
            st["pos"]["qty"] = bal
        return
    # flat per state — sweep the universe for untracked holdings (boot-only, ~20 RPC reads)
    for sym in universe():
        try:
            bal = ex.bal(sym)
        except Exception:  # noqa: BLE001
            continue
        if bal <= 0:
            continue
        px = spot_px(sym)
        if px and bal * px > 2:
            st["pos"] = {"sym": sym, "qty": bal, "cost": bal * px, "eff": px, "ts": time.time()}
            st["peak"] = None; st["px_peak"] = None
            log(f"reconcile: adopted untracked {bal:.6g} {sym} (~${bal*px:.2f}) @ eff=now — managing it")
            notify("Gridora Autopilot", f"Adopsi posisi tak terlacak: {sym} ~${bal*px:.2f}")
            ledger({"event": "buy", "sym": sym, "qty": bal, "cost": bal * px, "eff": px,
                    "reason": "reconcile-adopt (untracked on-chain holding)"})
            return


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
    # post-mortem guards: chaos range cap + collapsed-parabola detector
    assert dip_score(2.0, 36.0, 0.30, 5.0, 5e6) is None             # LAB's rng 36% = rejected now
    assert dip_score(2.0, 12.0, 0.30, 5.0, 5e6) is not None         # healthy volatility still passes
    assert wild_range([18.45, 16.9, 15.2], [5.58, 8.3, 13.6]) > 200  # LAB's week = 230% = wild
    assert wild_range([10.5, 10.2], [9.8, 9.6]) < 10                 # calm week passes
    # brain verdict parsing (fenced, plain, garbage)
    assert parse_verdict('```json\n{"enter": false, "confidence": 88, "reason": "dead cat"}\n```') == \
        {"enter": False, "confidence": 88, "reason": "dead cat"}
    assert parse_verdict('{"enter": true, "confidence": 70, "reason": "ok"}')["enter"] is True
    assert parse_verdict("I think yes") is None
    # whale lens: near-mid book imbalance
    bal = book_imbalance([(100.0, 10.0)], [(100.2, 10.0)])
    assert bal is not None and 0.48 < bal < 0.52                     # balanced book ≈ 0.5
    heavy = book_imbalance([(100.0, 30.0)], [(100.2, 10.0)])
    assert heavy is not None and heavy > 0.7                         # bid-stacked = whales in
    far = book_imbalance([(100.0, 10.0), (90.0, 999.0)], [(100.2, 10.0)])
    assert far is not None and abs(far - bal) < 0.01                 # >2% from mid = ignored
    assert book_imbalance([], [(100.2, 10.0)]) is None               # no bids = no read
    print("selfcheck OK")


def main() -> None:
    if "--selfcheck" in sys.argv:
        selfcheck(); return
    paper = "--paper" in sys.argv
    once = "--once" in sys.argv
    global MODE
    MODE = "paper" if paper else "live"
    st = load_state()
    if not paper:
        st.pop("paper", None)   # stale paper wallet would mislabel the console badge
    ex = Paper(st) if paper else Live()
    reconcile(st, ex, paper)
    mode_lbl = "PAPER" if paper else "LIVE"
    what = "holding " + st["pos"]["sym"] if st["pos"] else f"flat, USDT ${ex.usdt():.2f}"
    log(f"=== AUTOPILOT {mode_lbl} | universe {len(universe())} tokens | {what} ===")
    notify("Gridora Autopilot", f"{mode_lbl} aktif — {what}")  # boot = Mac tahu kabar bot
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
