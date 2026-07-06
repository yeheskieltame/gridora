"""Control API: wallet-signature auth, state snapshot, and action dispatch.

Pure-logic tests — no HTTP socket; the handler layer is thin plumbing over these.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal

import pytest
from eth_account import Account
from eth_account.messages import encode_defunct

from gridora.agent.brain import AgentDecision
from gridora.agent.state import GridoraState
from gridora.agent.strategies import STRATEGIES
from gridora.control_api import (
    AUTOPILOT_FRESH_S,
    RATE_AUTH_PER_MIN,
    RATE_CONTROL_PER_MIN,
    TRADES_LIMIT_MAX,
    AuthStore,
    RateLimiter,
    apply_action,
    autopilot_json,
    cors_headers,
    state_json,
    trades_tail,
)


def _signed(store: AuthStore, acct) -> tuple[str, str]:
    nonce, message = store.issue_nonce()
    sig = acct.sign_message(encode_defunct(text=message)).signature.hex()
    return nonce, sig


def test_auth_owner_roundtrip():
    owner = Account.create()
    store = AuthStore(owner.address)
    nonce, sig = _signed(store, owner)
    token = store.verify(owner.address, sig, nonce)
    assert store.check_token(token)
    # nonce is single-use — replay with the same signature is refused
    with pytest.raises(PermissionError):
        store.verify(owner.address, sig, nonce)


def test_auth_rejects_non_owner_and_no_owner():
    owner, mallory = Account.create(), Account.create()
    store = AuthStore(owner.address)
    nonce, sig = _signed(store, mallory)
    with pytest.raises(PermissionError):
        store.verify(mallory.address, sig, nonce)          # signed, but not allowlisted
    unset = AuthStore("")                                   # default-deny when unconfigured
    nonce2, sig2 = _signed(unset, owner)
    with pytest.raises(PermissionError):
        unset.verify(owner.address, sig2, nonce2)
    assert not store.check_token("bogus")


def test_auth_allowlist_multiple_wallets():
    """GRIDORA_OWNER is a comma-separated allowlist — every listed wallet can drive,
    anyone else is refused (case-insensitive match)."""
    a, b, mallory = Account.create(), Account.create(), Account.create()
    store = AuthStore(f" {a.address.upper()} , {b.address.lower()},, ")  # messy env value
    assert store.owners == frozenset({a.address.lower(), b.address.lower()})
    for acct in (a, b):
        nonce, sig = _signed(store, acct)
        assert store.check_token(store.verify(acct.address, sig, nonce))
    nonce, sig = _signed(store, mallory)
    with pytest.raises(PermissionError):
        store.verify(mallory.address, sig, nonce)


def test_state_json_serializes():
    s = GridoraState(market="FIL/USDT", mode="paper", chain_id=56)
    s.start_equity = Decimal("100")
    s.band = (Decimal("4.1"), Decimal("4.9"))
    s.net_base = Decimal("1.25")
    s.record_decision(AgentDecision(strategy_key="range", reasoning="chop", confidence="HIGH", source="claude"))
    s.record_episode("range", 42, Decimal("0.42"))
    s.add_log("hello")
    snap = json.loads(json.dumps(state_json(s, owner_set=True)))   # must be JSON-safe
    assert snap["market"] == "FIL/USDT" and snap["owner_set"]
    assert snap["grid"]["band"] == ["4.1", "4.9"]
    assert snap["decision"]["strategy_key"] == "range"
    assert snap["account"]["equity"] == pytest.approx(100.42)
    assert snap["episode_history"][-1][2] == 42


class _StubSup:
    """Records what the dispatcher calls — no engine, no I/O."""

    def __init__(self):
        self.state = GridoraState()
        self.registry = STRATEGIES
        self.autopilot = True
        self.calls: list = []

    def set_autopilot(self, on):
        self.autopilot = on
        self.calls.append(("autopilot", on))

    async def decide_and_apply(self):
        self.calls.append(("decide",))

    async def halt(self, why):
        self.calls.append(("halt", why))

    async def force_strategy(self, key, **kw):
        self.calls.append(("strategy", key, kw))


async def test_apply_action_dispatch():
    sup = _StubSup()
    await apply_action(sup, {"action": "strategy", "key": "range", "half_band": 0.05, "levels": 10, "bias": 2})
    # manual pick turns autopilot off (TUI parity) and clamps bias into [-1, 1]
    assert sup.calls[0] == ("autopilot", False)
    kind, key, kw = sup.calls[1]
    assert (kind, key) == ("strategy", "range")
    assert kw == {"half_band": Decimal("0.05"), "levels": 10, "bias": 1}
    await apply_action(sup, {"action": "autopilot", "on": True})
    await apply_action(sup, {"action": "decide"})
    await apply_action(sup, {"action": "halt"})
    assert [c[0] for c in sup.calls[2:]] == ["autopilot", "decide", "halt"]
    with pytest.raises(ValueError):
        await apply_action(sup, {"action": "strategy", "key": "nope"})
    with pytest.raises(ValueError):
        await apply_action(sup, {"action": "warp"})
    with pytest.raises(ValueError):
        await apply_action(sup, {"action": "strategy", "key": "range", "half_band": "x"})


def test_autopilot_json_fresh_and_stale(tmp_path):
    """GET /api/autopilot payload: the state file + mtime freshness. Never raises."""
    f = tmp_path / "autopilot.json"
    f.write_text(json.dumps({"pos": {"sym": "FIL", "qty": "7.7"}, "paper": {"USDT": 30.88}}))
    snap = autopilot_json(f)
    assert snap["pos"]["sym"] == "FIL" and snap["paper"] == {"USDT": 30.88}
    assert snap["updated"] == pytest.approx(f.stat().st_mtime)
    assert snap["running"] is True                       # just written -> mtime is fresh
    old = time.time() - (AUTOPILOT_FRESH_S + 60)
    os.utime(f, (old, old))
    stale = autopilot_json(f)
    assert stale["running"] is False and stale["pos"]["sym"] == "FIL"
    json.dumps(snap)                                     # must be JSON-safe as served


def test_autopilot_json_missing_or_malformed_never_raises(tmp_path):
    assert autopilot_json(tmp_path / "nope.json") == {"pos": None, "running": False}
    bad = tmp_path / "autopilot.json"
    bad.write_text("{torn write")
    assert autopilot_json(bad) == {"pos": None, "running": False}
    bad.write_text("[1, 2, 3]")                          # parses, but not an object
    assert autopilot_json(bad) == {"pos": None, "running": False}


def test_trades_tail_newest_first_and_cap(tmp_path):
    f = tmp_path / "trades.jsonl"
    with open(f, "w") as fh:
        for i in range(300):
            fh.write(json.dumps({"i": i, "kind": "SELL", "sym": "FIL"}) + "\n")
    tail = trades_tail(f, limit=50)
    assert len(tail) == 50
    assert tail[0]["i"] == 299 and tail[-1]["i"] == 250  # newest first
    assert len(trades_tail(f, limit=10_000)) == TRADES_LIMIT_MAX   # cap, not unbounded
    assert trades_tail(f, limit=-5)[0]["i"] == 299       # nonsense limit clamps to 1
    # bounded tail read seeks mid-file — the partial first line is dropped, not mangled
    assert [t["i"] for t in trades_tail(f, limit=1)] == [299]


def test_trades_tail_missing_and_torn_lines(tmp_path):
    assert trades_tail(tmp_path / "missing.jsonl") == []
    f = tmp_path / "trades.jsonl"
    f.write_text(json.dumps({"i": 0}) + "\n" + '{"torn": \n' + json.dumps({"i": 1}) + "\n")
    assert [t["i"] for t in trades_tail(f)] == [1, 0]    # garbage line skipped, order kept


def test_rate_limiter_allows_then_refuses():
    rl = RateLimiter()
    t0 = 1_000_000.0
    for _ in range(RATE_AUTH_PER_MIN):
        assert rl.allow("1.2.3.4", "auth", RATE_AUTH_PER_MIN, now=t0)
    assert not rl.allow("1.2.3.4", "auth", RATE_AUTH_PER_MIN, now=t0 + 1)   # 11th -> 429
    assert rl.allow("5.6.7.8", "auth", RATE_AUTH_PER_MIN, now=t0 + 1)       # per-IP
    assert rl.allow("1.2.3.4", "control", RATE_CONTROL_PER_MIN, now=t0 + 1) # per-bucket
    assert rl.allow("1.2.3.4", "auth", RATE_AUTH_PER_MIN, now=t0 + 61)      # window slid


def test_cors_headers_echo_configured_origin():
    """GRIDORA_CORS_ORIGIN is echoed verbatim; '*' keeps the historical open default."""
    assert ("Access-Control-Allow-Origin", "*") in cors_headers("*")
    assert ("Access-Control-Allow-Origin", "*") in cors_headers("")        # unset -> open
    locked = cors_headers("https://gridora.vercel.app")
    assert ("Access-Control-Allow-Origin", "https://gridora.vercel.app") in locked
    assert ("Vary", "Origin") in locked                  # caches must key on the echo
    assert ("Vary", "Origin") not in cors_headers("*")
