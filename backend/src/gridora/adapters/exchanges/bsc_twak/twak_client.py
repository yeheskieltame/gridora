"""TwakClient — the single seam to the Trust Wallet Agent Kit (local `twak` CLI).

Shells out to `twak <cmd> --json`; TWAK holds the key + signs locally (self-custody).
TWAK has no arbitrary contract call — only swap / automate (limit/DCA) / x402 / erc8004
/ compete / wallet. Chain keys `bsc` (56) / `bsctestnet` (97). Full surface: twak-cli-api.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

DEFAULT_CHAIN = "bsc"


class TwakError(RuntimeError):
    """A `twak` CLI call failed (not installed, wallet not configured, rejected, or
    bad output). A signing/execution path must never silently no-op."""


class TwakClient:
    def __init__(self, chain_key: str = DEFAULT_CHAIN, timeout: float = 90.0,
                 bin_path: str = "twak") -> None:
        self.chain_key = chain_key
        self.timeout = timeout
        self.bin = bin_path
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

    # ---- wallet ----

    async def address(self, chain: str | None = None) -> str:
        if self._address is None:
            data = await self._run("wallet", "address", "--chain", chain or self.chain_key)
            self._address = data.get("address") or data.get("agentWallet") or ""
        return self._address

    async def balance(self, chain: str | None = None) -> dict:
        return await self._run("wallet", "balance", "--chain", chain or self.chain_key)

    # ---- execution: swap (taker) + automate (maker limit orders) ----

    async def swap(self, amount: str, frm: str, to: str, chain: str | None = None,
                   *, slippage_pct: float, quote_only: bool = False) -> dict:
        args = ["swap", amount, frm, to, "--chain", chain or self.chain_key,
                "--slippage", str(slippage_pct)]
        if quote_only:
            args.append("--quote-only")
        return await self._run(*args)

    async def add_limit_order(self, frm: str, to: str, amount: str, price_usd: str,
                              condition: str, chain: str | None = None,
                              expires: str | None = None) -> dict:
        """`condition` is 'below' (buy the dip) or 'above' (take profit); `price_usd`
        is the target USD price of the volatile token; `amount` is in the source
        token. Returns {id, ...}. Executes only while a watcher runs."""
        if condition not in ("below", "above"):
            raise ValueError("condition must be 'below' or 'above'")
        args = ["automate", "add", "--from", frm, "--to", to, "--chain", chain or self.chain_key,
                "--amount", amount, "--price", price_usd, "--condition", condition]
        if expires:
            args += ["--expires", expires]
        return await self._run(*args)

    async def add_dca(self, frm: str, to: str, amount: str, interval: str,
                      chain: str | None = None, max_runs: int | None = None) -> dict:
        args = ["automate", "add", "--from", frm, "--to", to, "--chain", chain or self.chain_key,
                "--amount", amount, "--interval", interval]
        if max_runs is not None:
            args += ["--max-runs", str(max_runs)]
        return await self._run(*args)

    async def list_automations(self) -> list:
        data = await self._run("automate", "list")
        return data if isinstance(data, list) else data.get("automations", [])

    async def delete_automation(self, order_id: str) -> None:
        await self._run("automate", "delete", order_id)

    # ---- x402 (pay-per-call for CMC data) ----

    async def x402_request(self, url: str, max_payment: int, prefer_network: str | None = None) -> dict:
        """TWAK fetches the x402-gated URL, signs+settles the payment, returns the
        resource JSON. No tx hash is returned to the client (server verifies)."""
        args = ["x402", "request", url, "--max-payment", str(max_payment), "--yes"]
        if prefer_network:
            args += ["--prefer-network", prefer_network]
        return await self._run(*args)

    # ---- identity + verifiable proofs: native ERC-8004 ----

    async def erc8004_register(self, uri: str, chain: str | None = None) -> dict:
        return await self._run("erc8004", "register", "--uri", uri, "--chain", chain or self.chain_key)

    async def erc8004_set_metadata(self, agent_id: str, key: str, value: str,
                                   chain: str | None = None) -> dict:
        return await self._run("erc8004", "set-metadata", str(agent_id),
                               "--key", key, "--value", value, "--chain", chain or self.chain_key)

    async def erc8004_show(self, agent_id: str, chain: str | None = None) -> dict:
        return await self._run("erc8004", "show", str(agent_id), "--chain", chain or self.chain_key)

    async def erc8004_get_metadata(self, agent_id: str, key: str, chain: str | None = None) -> dict:
        return await self._run("erc8004", "get-metadata", str(agent_id),
                               "--key", key, "--chain", chain or self.chain_key)

    # ---- competition ----

    async def compete_register(self) -> dict:
        return await self._run("compete", "register")

    async def compete_status(self) -> dict:
        return await self._run("compete", "status")
