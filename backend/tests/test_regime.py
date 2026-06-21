"""Pure regime-classification tests — offline."""
from decimal import Decimal

from gridora.domain.models import RegimeFingerprint
from gridora.domain.regime import classify, regime_key


def test_ranging_when_flat():
    label, bias = classify(fear_greed=50, funding_bps=Decimal(0), momentum=Decimal(0))
    assert label == "RANGING"
    assert bias == 0


def test_trending_up_sets_long_bias():
    label, bias = classify(fear_greed=55, funding_bps=Decimal(0), momentum=Decimal("0.5"))
    assert label == "TRENDING_UP"
    assert bias == 1


def test_trending_down_sets_short_bias():
    label, bias = classify(fear_greed=45, funding_bps=Decimal(0), momentum=Decimal("-0.5"))
    assert label == "TRENDING_DOWN"
    assert bias == -1


def test_hysteresis_holds_lean_until_momentum_dies():
    # Momentum 0.25 is below ENTER (0.3) but above EXIT (0.2): a held long stays.
    _, held = classify(fear_greed=55, funding_bps=Decimal(0), momentum=Decimal("0.25"), prev_bias=1)
    assert held == 1
    # From flat, the same momentum does NOT enter a lean.
    _, fresh = classify(fear_greed=55, funding_bps=Decimal(0), momentum=Decimal("0.25"), prev_bias=0)
    assert fresh == 0


def test_funding_gate_vetoes_crowded_long():
    # Strong up-momentum but extreme positive funding (crowded longs) -> demote.
    label, bias = classify(fear_greed=55, funding_bps=Decimal(15), momentum=Decimal("0.5"))
    assert bias == 0
    assert label == "SQUEEZE"


def test_structure_veto_no_long_into_extreme_greed():
    label, bias = classify(fear_greed=85, funding_bps=Decimal(0), momentum=Decimal("0.5"))
    assert bias == 0  # don't buy the euphoria top


def test_structure_veto_no_short_into_extreme_fear():
    _, bias = classify(fear_greed=15, funding_bps=Decimal(0), momentum=Decimal("-0.5"))
    assert bias == 0  # don't sell the capitulation bottom


def test_regime_key_is_deterministic_and_buckets():
    a = RegimeFingerprint(market="CAKE/USDT", fear_greed=52, funding_bps=Decimal(1),
                          momentum=Decimal("0.41"), label="TRENDING_UP", bias=1)
    b = RegimeFingerprint(market="CAKE/USDT", fear_greed=58, funding_bps=Decimal(1),
                          momentum=Decimal("0.44"), label="TRENDING_UP", bias=1)
    # Same coarse bucket (fear_greed//10 == 5, momentum ~0.4) -> same key.
    assert regime_key(a) == regime_key(b)
    assert regime_key(a).startswith("0x") and len(regime_key(a)) == 66
