"""Safety-guard tests — offline, no keys, no net."""
from decimal import Decimal

from gridora.app.safety import AccountGuard, Allowlist, CircuitBreaker, ProfitGuard


def test_breaker_trips_on_drawdown():
    cb = CircuitBreaker(max_drawdown=Decimal(100))
    assert cb.check(Decimal(0), Decimal(-150)) is not None
    assert cb.tripped


def test_breaker_trips_on_inventory():
    cb = CircuitBreaker(max_inventory=Decimal(10))
    assert cb.check(Decimal(-12), Decimal(0)) is not None
    assert cb.tripped


def test_breaker_idempotent_and_resettable():
    cb = CircuitBreaker(max_drawdown=Decimal(100))
    first = cb.check(Decimal(0), Decimal(-150))
    # Once tripped it returns the SAME reason even if pnl recovers.
    assert cb.check(Decimal(0), Decimal(0)) == first
    cb.reset()
    assert not cb.tripped
    assert cb.check(Decimal(0), Decimal(0)) is None


def test_breaker_disabled_with_zero_caps():
    cb = CircuitBreaker()  # both caps 0 -> never trips
    assert cb.check(Decimal(10_000), Decimal(-10_000)) is None
    assert not cb.tripped


def test_profit_guard_take_profit():
    pg = ProfitGuard(take_profit=Decimal(50))
    assert pg.check(Decimal(20)) is None
    assert pg.check(Decimal(50)) is not None
    assert pg.tripped


def test_profit_guard_trailing_stop_arms_then_banks():
    # Let it run; arm after the peak clears trail_arm, bank on a 30% give-back.
    pg = ProfitGuard(trail_frac=Decimal("0.3"), trail_arm=Decimal(10))
    assert pg.check(Decimal(5)) is None     # below arm -> not armed
    assert pg.check(Decimal(100)) is None    # new peak, armed, no give-back yet
    assert pg.check(Decimal(80)) is None     # gave back 20% (<30%) -> hold
    assert pg.check(Decimal(69)) is not None  # gave back 31% of peak -> bank


def test_profit_guard_disabled_never_banks():
    pg = ProfitGuard()
    assert pg.check(Decimal(10_000)) is None


def test_account_guard_fail_open_until_armed():
    g = AccountGuard(max_drop=Decimal(100))
    # Unarmed: never trips even on a huge apparent drop.
    assert g.check(Decimal(0)) is None
    g.arm(Decimal(1_000))
    g.arm(Decimal(5))  # arm is idempotent — first reading wins
    assert g.start_equity == Decimal(1_000)
    assert g.check(Decimal(950)) is None       # -50 within cap
    assert g.check(Decimal(880)) is not None    # -120 > cap -> trip
    assert g.tripped


def test_account_guard_disabled():
    g = AccountGuard(max_drop=Decimal(0))
    g.arm(Decimal(1_000))
    assert g.check(Decimal(0)) is None


def test_allowlist_permits_only_listed_pairs():
    al = Allowlist(symbols=frozenset({"CAKE", "USDT", "BNB"}))
    assert al.permits("CAKE/USDT")
    assert al.permits("BNB/USDT")
    assert not al.permits("DOGE/USDT")   # base not listed
    assert not al.permits("CAKE/EUR")    # quote not listed


def test_allowlist_assert_raises():
    al = Allowlist(symbols=frozenset({"CAKE", "USDT"}))
    al.assert_permits("CAKE/USDT")
    try:
        al.assert_permits("DOGE/USDT")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "allowlist" in str(e)
