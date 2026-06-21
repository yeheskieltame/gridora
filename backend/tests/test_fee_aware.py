"""Fee-aware grid tests — a grid must not place levels closer than a round-trip's cost."""
from decimal import Decimal

import pytest

from gridora.agent.strategies import STRATEGIES, build_config
from gridora.app.service import GridService
from gridora.domain.grid import (
    fee_floor_bps,
    fit_levels_to_fees,
    min_adjacent_spacing_bps,
)
from gridora.domain.models import RiskPreset, Spacing


def test_fee_floor_bps_default():
    # 2*fee(25) + slippage(10) + gas(5)
    assert fee_floor_bps() == 65
    assert fee_floor_bps(fee_bps=5, slippage_bps=2, gas_bps=1) == 13  # V3-stable style


def test_min_spacing_shrinks_with_more_levels():
    wide = min_adjacent_spacing_bps(Decimal("90"), Decimal("110"), 5, Spacing.GEOMETRIC)
    tight = min_adjacent_spacing_bps(Decimal("90"), Decimal("110"), 40, Spacing.GEOMETRIC)
    assert wide > tight > 0


def test_fit_levels_reduces_when_too_tight():
    # ~2% band, 20 levels -> ~10bps spacing < 65bps floor -> must drop levels.
    fitted = fit_levels_to_fees(Decimal("99"), Decimal("101"), 20, Spacing.GEOMETRIC, 65)
    assert 2 <= fitted < 20
    assert min_adjacent_spacing_bps(Decimal("99"), Decimal("101"), fitted, Spacing.GEOMETRIC) >= 65


def test_fit_levels_keeps_wide_grid():
    # ±10% band, 12 levels -> plenty of room, no reduction.
    fitted = fit_levels_to_fees(Decimal("90"), Decimal("110"), 12, Spacing.GEOMETRIC, 65)
    assert fitted == 12


def test_band_narrower_than_floor_raises():
    with pytest.raises(ValueError):
        fit_levels_to_fees(Decimal("100"), Decimal("100.3"), 8, Spacing.GEOMETRIC, 65)  # 30bps band < 65 floor


def test_preset_config_is_fee_safe():
    cfg = GridService.config_for_preset("i", "CAKE/USDT", RiskPreset.BALANCED, Decimal("100"), Decimal("2.5"))
    assert min_adjacent_spacing_bps(cfg.lower, cfg.upper, cfg.levels, cfg.spacing) >= fee_floor_bps()


def test_tight_scalp_strategy_gets_widened():
    # tight_scalp is ±3% with 18 levels -> ~35bps spacing < 65bps floor -> fewer levels.
    cfg = build_config(STRATEGIES["tight_scalp"], "i", "CAKE/USDT", Decimal("2.5"), Decimal("1000"))
    assert cfg.levels < STRATEGIES["tight_scalp"].levels
    assert min_adjacent_spacing_bps(cfg.lower, cfg.upper, cfg.levels, cfg.spacing) >= fee_floor_bps()
