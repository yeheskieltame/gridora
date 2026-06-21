"""Pure grid-math tests — offline, no keys, no net."""
from decimal import Decimal

import pytest

from gridora.domain.grid import (
    arithmetic_levels,
    build_grid_orders,
    compute_paired_order,
    geometric_levels,
    levels_for,
    parse_external_id,
    plan_levels,
    quantize,
    quantize_qty,
)
from gridora.domain.models import GridConfig, Side, Spacing


def _cfg(**kw) -> GridConfig:
    base = dict(
        instance_id="CAKE/USDT-1",
        market="CAKE/USDT",
        lower=Decimal("90"),
        upper=Decimal("110"),
        levels=11,
        order_size=Decimal("1"),
        spacing=Spacing.ARITHMETIC,
    )
    base.update(kw)
    return GridConfig(**base)


def test_arithmetic_levels_endpoints_and_count():
    levels = arithmetic_levels(Decimal(100), Decimal(110), 11)
    assert len(levels) == 11
    assert levels[0] == Decimal(100)
    assert levels[-1] == Decimal(110)
    assert levels[1] == Decimal(101)


def test_geometric_levels_monotonic_and_bounds():
    levels = geometric_levels(Decimal(100), Decimal(200), 5)
    assert len(levels) == 5
    assert levels[0] == Decimal(100)
    assert abs(levels[-1] - Decimal(200)) < Decimal("0.000001")
    assert all(levels[i] < levels[i + 1] for i in range(len(levels) - 1))


def test_levels_need_at_least_two():
    with pytest.raises(ValueError):
        arithmetic_levels(Decimal(100), Decimal(110), 1)
    with pytest.raises(ValueError):
        geometric_levels(Decimal(100), Decimal(110), 1)


def test_geometric_requires_positive_lower():
    with pytest.raises(ValueError):
        geometric_levels(Decimal(0), Decimal(110), 5)


def test_quantize_to_tick_half_up_default():
    assert quantize(Decimal("100.057"), Decimal("0.1")) == Decimal("100.1")


def test_quantize_buy_rounds_down_sell_rounds_up():
    # A BUY must never round UP past its level (would cross); a SELL never down.
    assert quantize(Decimal("100.19"), Decimal("0.1"), Side.BUY) == Decimal("100.1")
    assert quantize(Decimal("100.11"), Decimal("0.1"), Side.SELL) == Decimal("100.2")


def test_quantize_zero_tick_is_identity():
    assert quantize(Decimal("100.057"), Decimal("0")) == Decimal("100.057")


def test_quantize_qty_floors_to_step_and_respects_min():
    assert quantize_qty(Decimal("1.237"), Decimal("0.01"), Decimal("0.01")) == Decimal("1.23")
    # Below min -> 0 (refuse the order); never silently inflate up to min, which would
    # spend money on an unfunded grid. The engine treats 0 as 'don't trade'.
    assert quantize_qty(Decimal("0.004"), Decimal("0.001"), Decimal("0.01")) == Decimal("0")


def test_plan_levels_guards_max():
    with pytest.raises(ValueError):
        plan_levels(_cfg(levels=10, max_levels=5))


def test_build_grid_buy_below_sell_above_skip_mid():
    cfg = _cfg()
    levels = levels_for(cfg.lower, cfg.upper, cfg.levels, cfg.spacing)  # 90..110 step 2
    mid = Decimal("100")
    orders = build_grid_orders(cfg, levels, mid)
    buys = [o for o in orders if o.side is Side.BUY]
    sells = [o for o in orders if o.side is Side.SELL]
    assert all(o.price < mid for o in buys)
    assert all(o.price > mid for o in sells)
    # The level exactly at mid (100) is skipped to avoid a self-cross.
    assert all(o.price != mid for o in orders)
    assert len(orders) == cfg.levels - 1
    assert all(o.qty == cfg.order_size for o in orders)


def test_build_grid_long_bias_places_only_buys():
    cfg = _cfg(bias=1)
    levels = levels_for(cfg.lower, cfg.upper, cfg.levels, cfg.spacing)
    orders = build_grid_orders(cfg, levels, Decimal("100"), bias=1)
    assert orders and all(o.side is Side.BUY for o in orders)


def test_build_grid_short_bias_places_only_sells():
    cfg = _cfg(bias=-1)
    levels = levels_for(cfg.lower, cfg.upper, cfg.levels, cfg.spacing)
    orders = build_grid_orders(cfg, levels, Decimal("100"), bias=-1)
    assert orders and all(o.side is Side.SELL for o in orders)


def test_paired_order_buy_to_sell_up_sell_to_buy_down():
    cfg = _cfg()
    levels = levels_for(cfg.lower, cfg.upper, cfg.levels, cfg.spacing)
    up = compute_paired_order(cfg, levels, fill_level=3, fill_side=Side.BUY, nonce=1)
    assert up is not None and up.side is Side.SELL and up.level == 4
    down = compute_paired_order(cfg, levels, fill_level=3, fill_side=Side.SELL, nonce=1)
    assert down is not None and down.side is Side.BUY and down.level == 2


def test_paired_order_none_at_boundaries():
    cfg = _cfg()
    levels = levels_for(cfg.lower, cfg.upper, cfg.levels, cfg.spacing)
    assert compute_paired_order(cfg, levels, len(levels) - 1, Side.BUY, 1) is None
    assert compute_paired_order(cfg, levels, 0, Side.SELL, 1) is None


def test_external_id_round_trips_with_hyphenated_instance():
    cfg = _cfg(instance_id="CAKE/USDT-1700000000000-7")
    levels = levels_for(cfg.lower, cfg.upper, cfg.levels, cfg.spacing)
    orders = build_grid_orders(cfg, levels, Decimal("100"), nonce=42)
    iid, level, nonce = parse_external_id(orders[0].external_id)
    assert iid == cfg.instance_id
    assert nonce == 42
    assert level == orders[0].level
