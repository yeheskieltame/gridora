"""Pure-helper tests for the BNB adapters — offline (no node, no TWAK)."""
from decimal import Decimal

import pytest

from gridora.adapters.exchanges.bsc_twak.bsc_tokens import (
    UnknownToken,
    from_units,
    resolve_token,
    split_market,
    to_units,
)
from gridora.adapters.signals.cmc import _fng_value, _pct_change_24h, momentum_from_pct


def test_to_from_units_round_trip():
    assert to_units(Decimal("1.5"), 18) == 1_500_000_000_000_000_000
    assert to_units(Decimal("2"), 8) == 200_000_000
    assert from_units(1_500_000_000_000_000_000, 18) == Decimal("1.5")


def test_split_market():
    assert split_market("CAKE/USDT") == ("CAKE", "USDT")
    with pytest.raises(ValueError):
        split_market("CAKEUSDT")


def test_resolve_token_known_and_unknown():
    addr, dec = resolve_token("USDT")
    assert addr.startswith("0x") and dec == 18
    assert resolve_token("DOGE")[1] == 8
    with pytest.raises(UnknownToken):
        resolve_token("NOTAREALTOKEN")


def test_resolve_token_custom_table():
    addr, dec = resolve_token("TEST", {"TEST": ("0xabc", 6)})
    assert (addr, dec) == ("0xabc", 6)


def test_momentum_from_pct_clips_to_unit():
    assert momentum_from_pct(10.0) == Decimal("1")
    assert momentum_from_pct(-20.0) == Decimal("-1")
    assert momentum_from_pct(5.0) == Decimal("0.5")
    assert momentum_from_pct(0.0) == Decimal("0")


def test_cmc_response_parsing_is_shape_tolerant():
    assert _fng_value({"data": {"value": "42"}}) == 42
    assert _fng_value({"data": [{"value": 30}]}) == 30
    assert _fng_value({"nope": 1}) == 50  # neutral default
    body = {"data": {"CAKE": {"quote": {"USD": {"percent_change_24h": 3.2}}}}}
    assert _pct_change_24h(body, "CAKE") == 3.2
    assert _pct_change_24h({"data": {}}, "CAKE") == 0.0
