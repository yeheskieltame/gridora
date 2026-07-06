"""TwakClient REST transport — verified against the probed `twak serve --rest` action
surface, all offline via httpx.MockTransport (no real server, no subprocess, no net)."""
import json

import httpx
import pytest

from gridora.adapters.exchanges.bsc_twak.twak_client import (
    TwakClient, TwakError, _interval_ms)


def _client(handler) -> TwakClient:
    return TwakClient(chain_key="bsc", rest_url="http://twak.test", rest_secret="s3cret",
                      rest_transport=httpx.MockTransport(handler))


def _forbid_cli(monkeypatch):
    async def boom(self, *args):
        raise AssertionError(f"CLI subprocess must not run, got: twak {' '.join(args)}")
    monkeypatch.setattr(TwakClient, "_run", boom)


def _capture_cli(monkeypatch, result=None):
    calls: list[tuple] = []

    async def fake_run(self, *args):
        calls.append(args)
        return result if result is not None else {"id": "cli-1", "output": "1 USDT"}
    monkeypatch.setattr(TwakClient, "_run", fake_run)
    return calls


# ---- routing + auth ----

async def test_rest_get_address_posts_action_with_bearer(monkeypatch):
    _forbid_cli(monkeypatch)
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["Authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"chain": "bsc", "address": "0xA11CE"})

    tw = _client(handler)
    assert await tw.address() == "0xA11CE"
    assert seen["url"] == "http://twak.test/actions/get_address"
    assert seen["auth"] == "Bearer s3cret"
    assert seen["body"] == {"chain": "bsc"}


async def test_no_rest_url_means_cli_only(monkeypatch):
    calls = _capture_cli(monkeypatch, result={"address": "0xCLI"})
    tw = TwakClient(chain_key="bsc", rest_url="")   # explicit: env may be anything
    assert await tw.address() == "0xCLI"
    assert calls == [("wallet", "address", "--chain", "bsc")]


async def test_balance_stays_on_cli_even_with_rest(monkeypatch):
    # REST wallet_balance is native-only atomic units (no tokens[] holdings scan) —
    # the CLI composite view is irreplaceable, so balance() must not route to REST.
    calls = _capture_cli(monkeypatch, result={"tokens": [{"symbol": "USDT", "balance": "5"}]})

    def handler(req):  # any REST hit is a routing bug
        raise AssertionError("balance() must not use the REST transport")

    tw = _client(handler)
    b = await tw.balance()
    assert b["tokens"][0]["symbol"] == "USDT"
    assert calls == [("wallet", "balance", "--chain", "bsc")]


# ---- swap: quote + execute translation ----

async def test_rest_quote_only_routes_to_get_swap_quote(monkeypatch):
    _forbid_cli(monkeypatch)
    seen = {}

    def handler(req):
        seen["action"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"success": True, "input": "1 FIL",
                                         "output": "0.78 USDT", "provider": "LiquidMesh"})

    tw = _client(handler)
    res = await tw.swap("1", "0xFIL", "0xUSDT", slippage_pct=0.5, quote_only=True, decimals=18)
    assert seen["action"] == "/actions/get_swap_quote"
    assert seen["body"] == {"fromChain": "bsc", "fromToken": "0xFIL", "toChain": "bsc",
                            "toToken": "0xUSDT", "amount": "1"}
    assert res["output"] == "0.78 USDT"   # adapter parses this key


async def test_rest_swap_execute_derives_output_from_summary(monkeypatch):
    # REST `swap` returns summary "<in> SYM -> <out> SYM" instead of the CLI's
    # input/output keys; the transport must restore the CLI shape.
    _forbid_cli(monkeypatch)
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"success": True, "hash": "0xdeed",
                                         "summary": "10 USDT -> 4.1 CAKE",
                                         "provider": "LiquidMesh"})

    tw = _client(handler)
    res = await tw.swap("10", "0xUSDT", "0xCAKE", slippage_pct=0.5)
    assert seen["body"]["slippage"] == "0.5"
    assert res["output"] == "4.1 CAKE" and res["input"] == "10 USDT"
    assert res["hash"] == "0xdeed"


async def test_rest_swap_decimals_validation_error_retries_cli(monkeypatch):
    # REST auto-resolves decimals by token search; if that fails for a token whose
    # decimals we KNOW, retry the CLI (--decimals). Rejected at validation = unsigned,
    # so the retry cannot double-execute.
    calls = _capture_cli(monkeypatch, result={"output": "9.9 USDT", "hash": "0xcli"})

    def handler(req):
        return httpx.Response(200, json={
            "success": False, "code": "VALIDATION_ERROR",
            "message": "Could not determine decimals for 0xODD on bsc; provide decimals explicitly."})

    tw = _client(handler)
    res = await tw.swap("10", "0xODD", "0xUSDT", slippage_pct=0.5, decimals=6)
    assert res["hash"] == "0xcli"
    assert calls and "--decimals" in calls[0] and "6" in calls[0]


async def test_rest_swap_other_error_raises_without_cli_retry(monkeypatch):
    _forbid_cli(monkeypatch)

    def handler(req):
        return httpx.Response(200, json={"success": False, "code": "NO_ROUTES",
                                         "message": "No swap routes found"})

    tw = _client(handler)
    with pytest.raises(TwakError, match="NO_ROUTES"):
        await tw.swap("10", "0xUSDT", "0xCAKE", slippage_pct=0.5, decimals=18)


# ---- automations ----

async def test_rest_limit_order_maps_to_create_automation(monkeypatch):
    _forbid_cli(monkeypatch)
    seen = {}

    def handler(req):
        seen["action"] = req.url.path
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"success": True, "id": "auto-7",
                                         "summary": "Limit: ...", "record": {"id": "auto-7"}})

    tw = _client(handler)
    res = await tw.add_limit_order("0xUSDT", "0xCAKE", "4.8", "2.4", "below")
    assert seen["action"] == "/actions/create_automation"
    assert seen["body"] == {"type": "limit", "fromToken": "0xUSDT", "toToken": "0xCAKE",
                            "chain": "bsc", "amount": "4.8", "targetPrice": "2.4",
                            "condition": "below"}
    assert res["id"] == "auto-7"   # adapter reads this key


async def test_rest_limit_order_with_expires_uses_cli(monkeypatch):
    # create_automation has no expiry field — an expiring order must go via the CLI.
    calls = _capture_cli(monkeypatch, result={"id": "cli-9"})

    def handler(req):
        raise AssertionError("expires has no REST field — must not POST")

    tw = _client(handler)
    res = await tw.add_limit_order("0xUSDT", "0xCAKE", "4.8", "2.4", "below",
                                   expires="2026-08-01")
    assert res["id"] == "cli-9"
    assert calls and "--expires" in calls[0] and "2026-08-01" in calls[0]


async def test_rest_dca_converts_interval_to_ms(monkeypatch):
    _forbid_cli(monkeypatch)
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"success": True, "id": "dca-1"})

    tw = _client(handler)
    await tw.add_dca("0xUSDT", "0xCAKE", "5", "1h")
    assert seen["body"] == {"type": "dca", "fromToken": "0xUSDT", "toToken": "0xCAKE",
                            "chain": "bsc", "amount": "5", "intervalMs": 3_600_000}


async def test_rest_dca_with_max_runs_uses_cli(monkeypatch):
    calls = _capture_cli(monkeypatch, result={"id": "cli-2"})

    def handler(req):
        raise AssertionError("max_runs has no REST field — must not POST")

    tw = _client(handler)
    await tw.add_dca("0xUSDT", "0xCAKE", "5", "1h", max_runs=3)
    assert calls and "--max-runs" in calls[0]


def test_interval_ms_grammar():
    assert _interval_ms("30s") == 30_000
    assert _interval_ms("5m") == 300_000
    assert _interval_ms("1h") == 3_600_000
    assert _interval_ms("7d") == 604_800_000
    assert _interval_ms("soon") is None and _interval_ms("5 m") is None


async def test_rest_list_automations_requests_all_and_unwraps(monkeypatch):
    # CLI `automate list` returns ALL records (active + paused); REST defaults to
    # activeOnly=true, so the transport must send activeOnly=false and unwrap.
    _forbid_cli(monkeypatch)
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"success": True, "count": 1,
                                         "automations": [{"id": "a1", "active": False}]})

    tw = _client(handler)
    assert await tw.list_automations() == [{"id": "a1", "active": False}]
    assert seen["body"] == {"activeOnly": False}


async def test_rest_delete_automation_not_found_raises(monkeypatch):
    # CLI delete of a missing id exits non-zero -> TwakError; REST reports it as a
    # success:false body (HTTP 200) and must raise identically, with NO CLI fallback.
    _forbid_cli(monkeypatch)

    def handler(req):
        return httpx.Response(200, json={"success": False, "code": "AUTOMATION_NOT_FOUND",
                                         "message": "Automation zz not found"})

    tw = _client(handler)
    with pytest.raises(TwakError, match="AUTOMATION_NOT_FOUND"):
        await tw.delete_automation("zz")


# ---- x402 + erc8004 + compete ----

async def test_rest_x402_unwraps_content_and_never_auto_approves(monkeypatch):
    # REST wraps the resource in {content,...}; the CLI prints the resource itself.
    # autoApprove must be False: the CLI path never silently broadcasts a Permit2 approval.
    _forbid_cli(monkeypatch)
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json={"success": True,
                                         "content": json.dumps({"data": {"value": 54}}),
                                         "payment": {"amount": "10000"}})

    tw = _client(handler)
    res = await tw.x402_request("https://cmc.test/fng", 100_000, prefer_network="bsc")
    assert res == {"data": {"value": 54}}
    assert seen["body"] == {"url": "https://cmc.test/fng", "maxPaymentAtomic": "100000",
                            "preferNetwork": "bsc", "autoApprove": False}


async def test_rest_erc8004_and_compete_param_mapping(monkeypatch):
    _forbid_cli(monkeypatch)
    posts = []

    def handler(req):
        posts.append((req.url.path, json.loads(req.content)))
        return httpx.Response(200, json={"success": True, "agentId": "7", "hash": "0xh",
                                         "value": "0x01", "registered": True})

    tw = _client(handler)
    assert (await tw.erc8004_register("ipfs://agent.json"))["agentId"] == "7"
    assert (await tw.erc8004_set_metadata("7", "k", "v"))["hash"] == "0xh"
    assert (await tw.erc8004_get_metadata("7", "k"))["value"] == "0x01"
    await tw.erc8004_show("7")
    assert (await tw.compete_status())["registered"] is True
    await tw.compete_register()
    assert posts == [
        ("/actions/erc8004_register", {"agentURI": "ipfs://agent.json", "chain": "bsc"}),
        ("/actions/erc8004_set_metadata", {"agentId": "7", "key": "k", "value": "v", "chain": "bsc"}),
        ("/actions/erc8004_get_metadata", {"agentId": "7", "key": "k", "chain": "bsc"}),
        ("/actions/erc8004_show", {"agentId": "7", "chain": "bsc"}),
        ("/actions/competition_status", {}),
        ("/actions/competition_register", {}),
    ]


# ---- fallback behavior ----

async def test_rest_connect_failure_falls_back_to_cli_and_warns_once(monkeypatch, capsys):
    calls = _capture_cli(monkeypatch, result={"address": "0xCLI"})

    def handler(req):
        raise httpx.ConnectError("connection refused", request=req)

    tw = _client(handler)
    assert await tw.address() == "0xCLI"
    tw._address = None                      # force a second round-trip
    assert await tw.address() == "0xCLI"
    assert len(calls) == 2
    out = capsys.readouterr().out
    assert out.count("using CLI fallback") == 1   # logged once, not per call


async def test_rest_unknown_action_404_falls_back_to_cli(monkeypatch):
    calls = _capture_cli(monkeypatch, result={"registered": True})

    def handler(req):
        return httpx.Response(404, json={"code": "VALIDATION_ERROR",
                                         "message": "Unknown action: competition_status"})

    tw = _client(handler)
    assert (await tw.compete_status())["registered"] is True
    assert calls == [("compete", "status")]


async def test_rest_http_400_raises_without_cli_fallback(monkeypatch):
    # A schema rejection is a REAL error (bad params), not transport failure —
    # falling back to the CLI would just fail again (or worse, mask a bug).
    _forbid_cli(monkeypatch)

    def handler(req):
        return httpx.Response(400, json={"code": "VALIDATION_ERROR", "message": "Required"})

    tw = _client(handler)
    with pytest.raises(TwakError, match="VALIDATION_ERROR"):
        await tw.compete_status()
