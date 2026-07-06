"""TwakClient — the single seam to the Trust Wallet Agent Kit (local `twak` CLI).

Shells out to `twak <cmd> --json`; TWAK holds the key + signs locally (self-custody).
TWAK has no arbitrary contract call — only swap / automate (limit/DCA) / x402 / erc8004
/ compete / wallet. Chain keys `bsc` (56) / `bsctestnet` (97). Full surface: twak-cli-api.

REST transport: set `TWAK_REST_URL` (e.g. http://127.0.0.1:3900) to route calls to a
persistent `twak serve --rest --port <n>` server — POST {url}/actions/{name} with
`Authorization: Bearer $TWAK_HMAC_SECRET` — skipping the ~1-3s CLI process spawn per
call. Falls back to the CLI subprocess when the server is unreachable or lacks the
action (logged once). Verified REST action map (probed 2026-07-07, twak CLI bundle):

  wallet address        -> get_address           {chain}
  wallet balance        -> CLI ONLY: REST `wallet_balance` is native-only in atomic
                           units and has no COMMON_TOKENS holdings scan, so it cannot
                           reproduce the CLI's {symbol, available, tokens[]} view.
  swap --quote-only     -> get_swap_quote        {fromChain, fromToken, toChain,
                                                  toToken, amount}
  swap                  -> swap                  {same + slippage (str)}. REST returns
                           {hash, summary: "<in> SYM -> <out> SYM", provider, explorer}
                           — input/output are derived from summary to keep the CLI
                           shape. `--decimals` has no REST field (REST auto-resolves;
                           on a decimals validation error the call retries via CLI).
  automate add --price  -> create_automation     {type:'limit', fromToken, toToken,
                                                  chain, amount, targetPrice,
                                                  condition: 'above'|'below'}
  automate add --interval -> create_automation   {type:'dca', ..., intervalMs}
                           (--max-runs / --expires have no REST field -> CLI)
  automate list         -> list_automations      {activeOnly: false} -> {automations:[]}
  automate delete       -> delete_automation     {id}
  x402 request          -> x402_request          {url, maxPaymentAtomic (str),
                           preferNetwork, autoApprove: false}. REST wraps the resource
                           in {content, payment, ...} — content is unwrapped (and
                           JSON-parsed when a string) to match the CLI, and
                           autoApprove=false mirrors the CLI's no-silent-Permit2 stance.
  erc8004 register      -> erc8004_register      {agentURI, chain}
  erc8004 set-metadata  -> erc8004_set_metadata  {agentId, key, value, chain}
  erc8004 get-metadata  -> erc8004_get_metadata  {agentId, key, chain}
  erc8004 show          -> erc8004_show          {agentId, chain}
  compete register      -> competition_register  {}
  compete status        -> competition_status    {}

REST errors: HTTP 4xx/5xx or a {success:false, code, message} body raise TwakError
(never retried via CLI — reissuing a possibly-signed action is unsafe); connect
failures and 404 "Unknown action" fall back to the CLI.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Callable, Sequence

import httpx

DEFAULT_CHAIN = "bsc"

_INTERVAL_MS = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}


def _interval_ms(interval: str) -> int | None:
    """'30s'/'5m'/'1h'/'7d' -> milliseconds (the CLI grammar); None if unparseable."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([smhd])", interval.strip().lower())
    return int(float(m.group(1)) * _INTERVAL_MS[m.group(2)]) if m else None


def _swap_output(res: Any) -> Any:
    """REST `swap` reports summary '<in> SYM -> <out> SYM' instead of the CLI's
    input/output keys — derive them so adapter parsing is transport-agnostic."""
    if isinstance(res, dict) and "output" not in res:
        inp, sep, outp = str(res.get("summary", "")).partition(" -> ")
        if sep:
            res = {**res, "input": inp.strip(), "output": outp.strip()}
    return res


def _x402_content(res: Any) -> Any:
    """REST `x402_request` wraps the resource in {content, payment, ...}; the CLI
    prints the resource itself. Unwrap + JSON-parse a string body to match."""
    if isinstance(res, dict) and "content" in res:
        content = res["content"]
        if isinstance(content, str):
            try:
                return json.loads(content)
            except ValueError:
                return content
        return content
    return res


class TwakError(RuntimeError):
    """A `twak` CLI call failed (not installed, wallet not configured, rejected, or
    bad output). A signing/execution path must never silently no-op."""


class TwakRestError(TwakError):
    """The REST server accepted the request but the action failed (validation or
    execution error). Raised only from the REST transport — callers may use it to
    retry validation-stage rejections via the CLI (nothing was signed)."""


class _RestUnavailable(Exception):
    """REST transport can't serve this call (connect failure or unknown action) —
    internal signal to fall back to the CLI subprocess."""


class TwakClient:
    def __init__(self, chain_key: str = DEFAULT_CHAIN, timeout: float = 90.0,
                 bin_path: str = "twak", rest_url: str | None = None,
                 rest_secret: str | None = None,
                 rest_transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.chain_key = chain_key
        self.timeout = timeout
        self.bin = bin_path
        self.rest_url = (rest_url if rest_url is not None
                         else os.environ.get("TWAK_REST_URL", "")).rstrip("/")
        self._rest_secret = (rest_secret if rest_secret is not None
                             else os.environ.get("TWAK_HMAC_SECRET", ""))
        self._rest_transport = rest_transport   # test seam (httpx.MockTransport)
        self._http: httpx.AsyncClient | None = None
        self._rest_fallback_warned = False
        self._address: str | None = None

    # ---- generic CLI exec ----

    async def _run(self, *args: str) -> Any:
        """Exec `twak <args> --json`, parse JSON stdout. The wallet password is NEVER
        passed as a CLI flag (it would leak into shell history); TWAK unlocks from the
        OS keychain, or from the `TWAK_WALLET_PASSWORD` env var that it reads itself.
        Raises TwakError on failure."""
        argv = [self.bin, *args, "--json"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
        except FileNotFoundError as e:
            raise TwakError(f"`{self.bin}` CLI not found — install @trustwallet/cli") from e
        except asyncio.TimeoutError as e:
            raise TwakError(f"twak {args[0] if args else ''} timed out after {self.timeout}s") from e
        if proc.returncode != 0:
            raise TwakError(f"twak {' '.join(args[:2])} exited {proc.returncode}: {err.decode()[:300]}")
        text = out.decode().strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise TwakError(f"twak {args[:2]} non-JSON output: {text[:200]}") from e
        if isinstance(data, dict) and data.get("error"):
            raise TwakError(f"twak {args[:2]} error: {data['error']} ({data.get('errorCode')})")
        return data

    # ---- REST transport ----

    def _rest_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self.rest_url, timeout=self.timeout,
                headers={"Authorization": f"Bearer {self._rest_secret}"},
                transport=self._rest_transport)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _rest(self, action: str, params: dict[str, Any]) -> Any:
        """POST {rest_url}/actions/{action}. Raises _RestUnavailable when the server
        can't serve the call (fall back to CLI) or TwakRestError on an action error."""
        try:
            resp = await self._rest_client().post(
                f"/actions/{action}", json={k: v for k, v in params.items() if v is not None})
        except httpx.HTTPError as e:
            raise _RestUnavailable(f"{type(e).__name__}: {e}") from e
        if resp.status_code == 404:   # "Unknown action" — this server can't do it
            raise _RestUnavailable(f"no REST action {action!r}")
        try:
            body = resp.json()
        except ValueError as e:
            raise TwakRestError(f"twak-rest {action} non-JSON response: {resp.text[:200]}") from e
        failed = isinstance(body, dict) and (body.get("success") is False or body.get("error"))
        if resp.status_code >= 400 or failed:
            code = body.get("code") or body.get("errorCode") if isinstance(body, dict) else None
            msg = (body.get("message") or body.get("error")) if isinstance(body, dict) else resp.text[:200]
            raise TwakRestError(f"twak-rest {action} error: {msg} ({code or resp.status_code})")
        return body

    async def _call(self, action: str, params: dict[str, Any], cli_args: Sequence[str],
                    translate: Callable[[Any], Any] | None = None) -> Any:
        """Route one verb: REST when TWAK_REST_URL is configured, CLI otherwise.
        `translate` reshapes the REST body to the CLI shape (never applied to CLI
        output). Falls back to the CLI only when REST is unreachable/unmapped."""
        if self.rest_url:
            try:
                data = await self._rest(action, params)
                return translate(data) if translate else data
            except _RestUnavailable as e:
                if not self._rest_fallback_warned:
                    self._rest_fallback_warned = True
                    print(f"  ! twak REST {self.rest_url} unavailable ({e}) — using CLI fallback")
        return await self._run(*cli_args)

    # ---- wallet ----

    async def address(self, chain: str | None = None) -> str:
        if self._address is None:
            ck = chain or self.chain_key
            data = await self._call("get_address", {"chain": ck},
                                    ("wallet", "address", "--chain", ck))
            self._address = data.get("address") or data.get("agentWallet") or ""
        return self._address

    async def balance(self, chain: str | None = None) -> dict:
        # CLI only: REST `wallet_balance` is native-only atomic units with no token
        # holdings list, so it can't feed the adapter's stable/base equity split.
        return await self._run("wallet", "balance", "--chain", chain or self.chain_key)

    # ---- execution: swap (taker) + automate (maker limit orders) ----

    async def swap(self, amount: str, frm: str, to: str, chain: str | None = None,
                   *, slippage_pct: float, quote_only: bool = False,
                   decimals: int | None = None) -> dict:
        ck = chain or self.chain_key
        args = ["swap", amount, frm, to, "--chain", ck, "--slippage", str(slippage_pct)]
        if decimals is not None:   # source-token decimals — needed when frm is a non-18-dec address
            args += ["--decimals", str(decimals)]
        if quote_only:
            args.append("--quote-only")
        body = {"fromChain": ck, "fromToken": frm, "toChain": ck, "toToken": to, "amount": amount}
        try:
            if quote_only:
                return await self._call("get_swap_quote", body, args)
            return await self._call("swap", {**body, "slippage": str(slippage_pct)},
                                    args, translate=_swap_output)
        except TwakRestError as e:
            # REST auto-resolves token decimals via search; when that fails for a token
            # whose decimals we know, the CLI --decimals override still works. The REST
            # call was rejected at validation (nothing signed), so this cannot double-swap.
            if decimals is not None and "decimals" in str(e).lower():
                return await self._run(*args)
            raise

    async def add_limit_order(self, frm: str, to: str, amount: str, price_usd: str,
                              condition: str, chain: str | None = None,
                              expires: str | None = None) -> dict:
        """`condition` is 'below' (buy the dip) or 'above' (take profit); `price_usd`
        is the target USD price of the volatile token; `amount` is in the source
        token. Returns {id, ...}. Executes only while a watcher runs."""
        if condition not in ("below", "above"):
            raise ValueError("condition must be 'below' or 'above'")
        ck = chain or self.chain_key
        args = ["automate", "add", "--from", frm, "--to", to, "--chain", ck,
                "--amount", amount, "--price", price_usd, "--condition", condition]
        if expires:   # create_automation has no expiry field — CLI only
            return await self._run(*args, "--expires", expires)
        return await self._call(
            "create_automation",
            {"type": "limit", "fromToken": frm, "toToken": to, "chain": ck,
             "amount": amount, "targetPrice": price_usd, "condition": condition}, args)

    async def add_dca(self, frm: str, to: str, amount: str, interval: str,
                      chain: str | None = None, max_runs: int | None = None) -> dict:
        ck = chain or self.chain_key
        args = ["automate", "add", "--from", frm, "--to", to, "--chain", ck,
                "--amount", amount, "--interval", interval]
        ms = _interval_ms(interval)
        if max_runs is not None or ms is None:   # no REST maxRuns field / odd interval — CLI only
            if max_runs is not None:
                args += ["--max-runs", str(max_runs)]
            return await self._run(*args)
        return await self._call(
            "create_automation",
            {"type": "dca", "fromToken": frm, "toToken": to, "chain": ck,
             "amount": amount, "intervalMs": ms}, args)

    async def list_automations(self) -> list:
        data = await self._call("list_automations", {"activeOnly": False},
                                ("automate", "list"))
        return data if isinstance(data, list) else data.get("automations", [])

    async def delete_automation(self, order_id: str) -> None:
        await self._call("delete_automation", {"id": order_id},
                         ("automate", "delete", order_id))

    # ---- x402 (pay-per-call for CMC data) ----

    async def x402_request(self, url: str, max_payment: int, prefer_network: str | None = None) -> dict:
        """TWAK fetches the x402-gated URL, signs+settles the payment, returns the
        resource JSON. No tx hash is returned to the client (server verifies)."""
        args = ["x402", "request", url, "--max-payment", str(max_payment), "--yes"]
        if prefer_network:
            args += ["--prefer-network", prefer_network]
        return await self._call(
            "x402_request",
            {"url": url, "maxPaymentAtomic": str(max_payment),
             "preferNetwork": prefer_network, "autoApprove": False},
            args, translate=_x402_content)

    # ---- identity + verifiable proofs: native ERC-8004 ----

    async def erc8004_register(self, uri: str, chain: str | None = None) -> dict:
        ck = chain or self.chain_key
        return await self._call("erc8004_register", {"agentURI": uri, "chain": ck},
                                ("erc8004", "register", "--uri", uri, "--chain", ck))

    async def erc8004_set_metadata(self, agent_id: str, key: str, value: str,
                                   chain: str | None = None) -> dict:
        ck = chain or self.chain_key
        return await self._call(
            "erc8004_set_metadata",
            {"agentId": str(agent_id), "key": key, "value": value, "chain": ck},
            ("erc8004", "set-metadata", str(agent_id), "--key", key, "--value", value,
             "--chain", ck))

    async def erc8004_show(self, agent_id: str, chain: str | None = None) -> dict:
        ck = chain or self.chain_key
        return await self._call("erc8004_show", {"agentId": str(agent_id), "chain": ck},
                                ("erc8004", "show", str(agent_id), "--chain", ck))

    async def erc8004_get_metadata(self, agent_id: str, key: str, chain: str | None = None) -> dict:
        ck = chain or self.chain_key
        return await self._call(
            "erc8004_get_metadata", {"agentId": str(agent_id), "key": key, "chain": ck},
            ("erc8004", "get-metadata", str(agent_id), "--key", key, "--chain", ck))

    # ---- competition ----

    async def compete_register(self) -> dict:
        return await self._call("competition_register", {}, ("compete", "register"))

    async def compete_status(self) -> dict:
        return await self._call("competition_status", {}, ("compete", "status"))
