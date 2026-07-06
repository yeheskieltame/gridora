"""TWAK-native execution + chain tests — verified CLI argv + the limit-order grid,
all offline via a FakeTwak (no real `twak`, no net)."""
import asyncio
import json
from decimal import Decimal

import pytest

from gridora.adapters.chain.client import BscChain
from gridora.adapters.exchanges.bsc_twak.adapter import BscTwakExchange
from gridora.adapters.exchanges.bsc_twak.bsc_tokens import BSC_TOKENS
from gridora.adapters.exchanges.bsc_twak.twak_client import TwakClient
from gridora.domain.models import EpisodeOutcome, GridConfig, Order, Side, Spacing


# ---- TwakClient builds the exact verified CLI argv ----

class _FakeProc:
    def __init__(self, out: bytes):
        self._out, self.returncode = out, 0

    async def communicate(self):
        return (self._out, b"")

    def kill(self):
        pass


def _patch_exec(monkeypatch, captured):
    payload = json.dumps({"hash": "0x1", "id": "a1", "address": "0xAGENT",
                          "agentId": "7", "output": "2.5", "registered": True}).encode()

    async def fake_exec(*argv, stdout=None, stderr=None):
        captured.append(argv)
        return _FakeProc(payload)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)


async def test_twak_swap_argv(monkeypatch):
    cap = []
    _patch_exec(monkeypatch, cap)
    tw = TwakClient(chain_key="bsc")
    out = await tw.swap("1.5", "CAKE", "USDT", slippage_pct=0.5)
    argv = cap[-1]
    assert argv[0] == "twak"
    assert argv[1:5] == ("swap", "1.5", "CAKE", "USDT")
    assert "--chain" in argv and "bsc" in argv and "--slippage" in argv and "--json" in argv
    assert out["output"] == "2.5"


async def test_twak_limit_order_argv(monkeypatch):
    cap = []
    _patch_exec(monkeypatch, cap)
    tw = TwakClient(chain_key="bsctestnet")
    await tw.add_limit_order("USDT", "CAKE", "10", "2.4", "below")
    argv = cap[-1]
    assert argv[1:3] == ("automate", "add")
    for tok in ("--from", "USDT", "--to", "CAKE", "--amount", "10", "--price", "2.4",
                "--condition", "below", "--chain", "bsctestnet", "--json"):
        assert tok in argv


async def test_twak_erc8004_and_x402_argv(monkeypatch):
    cap = []
    _patch_exec(monkeypatch, cap)
    tw = TwakClient(chain_key="bsc")
    await tw.erc8004_register("ipfs://agent.json")
    assert cap[-1][1:3] == ("erc8004", "register") and "--uri" in cap[-1]
    await tw.x402_request("https://cmc.test/fng", 100_000, prefer_network="bsc")
    argv = cap[-1]
    assert argv[1:4] == ("x402", "request", "https://cmc.test/fng")
    assert "--max-payment" in argv and "100000" in argv and "--yes" in argv


async def test_twak_raises_on_cli_error(monkeypatch):
    async def fake_exec(*argv, stdout=None, stderr=None):
        return _FakeProc(json.dumps({"error": "wallet not configured", "errorCode": "NO_WALLET"}).encode())
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    from gridora.adapters.exchanges.bsc_twak.twak_client import TwakError
    with pytest.raises(TwakError):
        await TwakClient().address()


# ---- BscTwakExchange maps a grid to TWAK limit orders ----

class FakeTwak:
    def __init__(self):
        self.calls = []
        self.automations: list[dict] = []
        self._n = 0
        self.balance_tokens: list[dict] = []
        self.metadata: dict[str, str] = {}

    async def add_limit_order(self, frm, to, amount, price_usd, condition, chain=None, expires=None):
        self.calls.append(("limit", frm, to, amount, price_usd, condition))
        self._n += 1
        oid = f"a{self._n}"
        self.automations.append({"id": oid})
        return {"id": oid}

    async def delete_automation(self, oid):
        self.calls.append(("delete", oid))
        self.automations = [a for a in self.automations if a["id"] != oid]

    async def list_automations(self):
        return list(self.automations)

    async def swap(self, amount, frm, to, chain=None, slippage_pct=1.0, quote_only=False, decimals=None):
        self.calls.append(("swap", amount, frm, to, quote_only))
        # real TWAK returns output as "<number> <SYMBOL>", not a bare number
        return {"output": "2.5 USDT"} if quote_only else {"hash": "0xswap", "output": "2.5 USDT"}

    async def balance(self, chain=None):
        return {"totalUsd": "1000", "tokens": self.balance_tokens}

    async def erc8004_register(self, uri, chain=None):
        self.calls.append(("register", uri))
        return {"agentId": "7", "hash": "0xreg"}

    async def erc8004_set_metadata(self, agent_id, key, value, chain=None):
        self.calls.append(("meta", agent_id, key, value))
        self.metadata[key] = value
        return {"hash": "0xmeta"}

    async def erc8004_get_metadata(self, agent_id, key, chain=None):
        return {"value": self.metadata.get(key, "")}


CAKE_A = BSC_TOKENS["CAKE"][0]
USDT_A = BSC_TOKENS["USDT"][0]


async def test_place_buy_is_limit_below_sell_is_above():
    # Orders must go to TWAK as verified CONTRACT ADDRESSES, never bare symbols — TWAK's
    # symbol resolver silently misroutes unknowns to BNB (verified live 2026-06-22).
    tw = FakeTwak()
    ex = BscTwakExchange(tw, use_limit_orders=True)
    buy = Order(instance_id="i", market="CAKE/USDT", side=Side.BUY, price=Decimal("2.4"), qty=Decimal("2"), level=1)
    await ex.place_order(buy)
    assert ("limit", USDT_A, CAKE_A, "4.8", "2.4", "below") in tw.calls  # spend quote, condition below
    assert buy.order_id == "a1"
    sell = Order(instance_id="i", market="CAKE/USDT", side=Side.SELL, price=Decimal("2.6"), qty=Decimal("2"), level=3)
    await ex.place_order(sell)
    assert ("limit", CAKE_A, USDT_A, "2", "2.6", "above") in tw.calls  # sell base, condition above


async def test_refuses_token_not_in_verified_address_map():
    # The anti-landmine guard: a token with no BscScan-verified address must be REFUSED,
    # not passed as a symbol (which TWAK would silently swap into BNB).
    from gridora.adapters.exchanges.bsc_twak.twak_client import TwakError
    tw = FakeTwak()
    ex = BscTwakExchange(tw, use_limit_orders=True)
    o = Order(instance_id="i", market="NOTATOKEN/USDT", side=Side.BUY, price=Decimal("0.01"), qty=Decimal("1"), level=1)
    with pytest.raises(TwakError):
        await ex.place_order(o)
    with pytest.raises(TwakError):
        await ex.best_bid_ask("NOTATOKEN/USDT")
    assert not any(c[0] in ("limit", "swap") for c in tw.calls)   # nothing reached TWAK


async def test_best_bid_ask_parses_suffixed_twak_output():
    # Real TWAK quote 'output' is "<number> <SYMBOL>" — best_bid_ask must parse the number,
    # not Decimal() the whole string (which crashed the first live launch).
    tw = FakeTwak()
    ex = BscTwakExchange(tw, use_limit_orders=True)
    bid, ask = await ex.best_bid_ask("CAKE/USDT")
    assert bid > 0 and ask > bid


async def test_cancel_deletes_automation():
    tw = FakeTwak()
    ex = BscTwakExchange(tw, use_limit_orders=True)
    o = Order(instance_id="i", market="CAKE/USDT", side=Side.BUY, price=Decimal("2.4"), qty=Decimal("1"))
    await ex.place_order(o)
    await ex.cancel_order("CAKE/USDT", o.order_id)
    assert ("delete", o.order_id) in tw.calls
    assert not await ex.open_orders("CAKE/USDT")


async def test_stream_fills_detects_executed_limit_order():
    tw = FakeTwak()
    ex = BscTwakExchange(tw, use_limit_orders=True, poll_interval=0.0)
    o = Order(instance_id="i", market="CAKE/USDT", side=Side.BUY, price=Decimal("2.4"), qty=Decimal("1"), level=2)
    await ex.place_order(o)
    tw.automations = []   # the watcher executed it -> it disappears from the list
    gen = ex.stream_fills()
    fill = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    await gen.aclose()
    assert fill.market == "CAKE/USDT" and fill.side is Side.BUY and fill.level == 2 and fill.qty == Decimal("1")


async def test_limit_poll_drops_expired_not_filled():
    # An order TWAK marks 'expired' (terminal, NOT executed) must not become a phantom fill.
    tw = FakeTwak()
    ex = BscTwakExchange(tw, use_limit_orders=True, poll_interval=0.0)
    o = Order(instance_id="i", market="CAKE/USDT", side=Side.BUY, price=Decimal("2.4"), qty=Decimal("1"), level=2)
    await ex.place_order(o)
    tw.automations = [{"id": o.order_id, "status": "expired"}]
    fills = [f async for f in ex._poll_limit_fills()]
    assert fills == []
    assert o.order_id not in ex._resting


async def test_limit_poll_emits_on_executed_status_with_txhash():
    tw = FakeTwak()
    ex = BscTwakExchange(tw, use_limit_orders=True, poll_interval=0.0)
    o = Order(instance_id="i", market="CAKE/USDT", side=Side.SELL, price=Decimal("2.6"), qty=Decimal("1"), level=3)
    await ex.place_order(o)
    tw.automations = [{"id": o.order_id, "status": "executed", "txHash": "0xabc"}]
    fills = [f async for f in ex._poll_limit_fills()]
    assert len(fills) == 1 and fills[0].tx_hash == "0xabc" and fills[0].level == 3


async def test_limit_rejects_non_usd_quote():
    # The limit --price is a USD target; a non-USD quote would set the trigger in the wrong unit.
    from gridora.adapters.exchanges.bsc_twak.twak_client import TwakError
    tw = FakeTwak()
    ex = BscTwakExchange(tw, use_limit_orders=True)
    o = Order(instance_id="i", market="CAKE/BNB", side=Side.BUY, price=Decimal("0.004"), qty=Decimal("1"), level=1)
    with pytest.raises(TwakError):
        await ex.place_order(o)


async def test_balance_equity_is_trading_capital_excludes_native_gas():
    # The live trip: equity counted native BNB (TWAK 'totalUsd', flaky ~$3) which swung more
    # than the guard cap. Equity must be TRADING capital only: stables (@$1) + held base
    # (priced via quote), with native BNB EXCLUDED.
    tw = FakeTwak()
    tw.balance_tokens = [{"symbol": "USDT", "balance": "50"}, {"symbol": "CAKE", "balance": "4"}]
    ex = BscTwakExchange(tw, use_limit_orders=True)
    bv = await ex.balance()
    # USDT 50 (@$1) + CAKE 4 valued via quote (FakeTwak swap -> "2.5 USDT") = 52.5; native ignored
    assert bv.equity_quote == Decimal("52.5")
    assert bv.quote_free == Decimal("50")


async def test_flatten_swaps_base_to_quote():
    tw = FakeTwak()
    tw.balance_tokens = [{"symbol": "CAKE", "balance": "5"}]
    ex = BscTwakExchange(tw, use_limit_orders=True)
    await ex.flatten("CAKE/USDT")
    assert ("swap", "5", CAKE_A, USDT_A, False) in tw.calls


# ---- BscChain writes proofs via TWAK ERC-8004 ----

async def test_bsc_chain_register_commit_attest_via_erc8004():
    tw = FakeTwak()
    chain = BscChain(tw, chain_key="bsctestnet")
    await chain.register_identity("0xowner")
    assert chain.agent_id == "7"
    cfg = GridConfig(instance_id="CAKE/USDT-999", market="CAKE/USDT", lower=Decimal("2.3"),
                     upper=Decimal("2.7"), levels=8, order_size=Decimal("1"), spacing=Spacing.GEOMETRIC)
    await chain.commit_strategy(cfg.instance_id, cfg)
    await chain.attest(cfg.instance_id, EpisodeOutcome(cfg.instance_id, Decimal("1"), 50, 3, 80, 0, "0xroot"))
    keys = [c[2] for c in tw.calls if c[0] == "meta"]
    assert any(k.startswith("gridora.commit.") for k in keys)
    assert any(k.startswith("gridora.attest.") for k in keys)


async def test_bsc_chain_commit_write_once_and_attest_needs_commit():
    tw = FakeTwak()
    chain = BscChain(tw, chain_key="bsctestnet")
    await chain.register_identity("0xowner")
    cfg = GridConfig(instance_id="CAKE/USDT-1", market="CAKE/USDT", lower=Decimal("2.3"),
                     upper=Decimal("2.7"), levels=8, order_size=Decimal("1"), spacing=Spacing.GEOMETRIC)
    out = EpisodeOutcome(cfg.instance_id, Decimal("1"), 50, 3, 80, 0, "0xroot")
    with pytest.raises(ValueError):           # attest before commit -> refused
        await chain.attest(cfg.instance_id, out)
    await chain.commit_strategy(cfg.instance_id, cfg)
    with pytest.raises(ValueError):           # second commit -> refused (write-once)
        await chain.commit_strategy(cfg.instance_id, cfg)
    await chain.attest(cfg.instance_id, out)
    with pytest.raises(ValueError):           # second attest -> refused (write-once)
        await chain.attest(cfg.instance_id, out)
