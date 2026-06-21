"""Pure regime logic: turn the CoinMarketCap fingerprint into a grid lean, and
into a stable bucketed key for on-chain recall.

ADAPTED FROM perps-agent/backend/src/perpsagent/{domain/regime.py, agent/decide.py,
agent/gates.py}: the trend→bias mapping (with hysteresis), the funding gate (a
crowded/extreme funding rate vetoes a with-funding lean), and the structure veto
(don't lean long into extreme greed / short into extreme fear) are reproduced here
on CMC inputs instead of CEX microstructure. No I/O — fully unit-testable.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

from .models import RegimeFingerprint

# Trend-mode thresholds with hysteresis: enter trend mode at |momentum| >= ENTER,
# leave at < EXIT. The gap stops the grid flapping between shapes when momentum
# hovers around one number (perps-agent BIAS_ENTER/BIAS_EXIT).
TREND_ENTER = Decimal("0.3")
TREND_EXIT = Decimal("0.2")

# Fear & Greed extremes (0..100). Leaning WITH the trend into an extreme is leaning
# into exhaustion — the structure veto demotes it to symmetric.
GREED_EXTREME = 78   # don't add a long lean here (buying the euphoria top)
FEAR_EXTREME = 22    # don't add a short lean here (selling the capitulation bottom)

# Funding (bps / 8h). |funding| this large marks a crowded trade (squeeze risk):
# demote any lean that would PAY that carry, label the regime SQUEEZE.
FUNDING_EXTREME_BPS = Decimal("10")   # 0.10%/8h
FUNDING_PAY_BPS = Decimal("3")        # 0.03%/8h — max carry a biased grid may pay


def classify(fear_greed: int, funding_bps: Decimal, momentum: Decimal,
             prev_bias: int = 0) -> tuple[str, int]:
    """Map CMC inputs to (regime_label, grid_bias). Pure.

    bias: +1 long-lean (buy-ladder, ride up) / -1 short-lean / 0 symmetric (range).
    Hysteresis around `prev_bias`: once in a trend lean, hold it until momentum
    clearly dies or flips, so a re-center mid-episode doesn't flap the ladder."""
    if prev_bias > 0 and momentum >= TREND_EXIT:
        bias = 1
    elif prev_bias < 0 and momentum <= -TREND_EXIT:
        bias = -1
    elif momentum >= TREND_ENTER:
        bias = 1
    elif momentum <= -TREND_ENTER:
        bias = -1
    else:
        bias = 0

    # Funding gate: extreme funding = crowded trade. Demote a lean that pays the
    # carry; the squeeze risk is exactly what a one-sided grid would walk into.
    if funding_bps >= FUNDING_EXTREME_BPS and bias > 0:
        bias = 0
    if funding_bps <= -FUNDING_EXTREME_BPS and bias < 0:
        bias = 0

    # Structure veto: never lean WITH the trend into a sentiment extreme.
    if bias > 0 and fear_greed >= GREED_EXTREME:
        bias = 0
    if bias < 0 and fear_greed <= FEAR_EXTREME:
        bias = 0

    if bias > 0:
        label = "TRENDING_UP"
    elif bias < 0:
        label = "TRENDING_DOWN"
    elif abs(funding_bps) >= FUNDING_EXTREME_BPS:
        label = "SQUEEZE"
    else:
        label = "RANGING"
    return label, bias


def bucket(fp: RegimeFingerprint) -> tuple:
    """Coarse, deterministic bucketing of the fingerprint so 'similar' regimes map
    to the same on-chain recall slot."""
    return (
        fp.fear_greed // 10,                       # 0..10 buckets
        int(fp.funding_bps),                       # nearest bp
        round(float(fp.momentum), 1),              # 0.1 buckets
        fp.label,
    )


def regime_key(fp: RegimeFingerprint) -> str:
    """A bytes32-shaped hex key for the bucketed regime. Deterministic so the same
    regime maps to the same on-chain slot. (On-chain we mirror this as a keccak of
    the same tuple; here stdlib sha256 keeps the domain dependency-free.)"""
    raw = repr(bucket(fp)).encode()
    return "0x" + hashlib.sha256(raw).hexdigest()
