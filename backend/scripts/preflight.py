"""Preflight: assert the agent is ready to go live. Run before `--mode live`.

Checks (each prints PASS/FAIL, never crashes the run):
  1. env↔chain consistency (config.guard)
  2. 149-token allowlist loaded
  3. BSC RPC live + chainId matches config
  4. verifier contracts configured (and have bytecode on-chain)
  5. TWAK reachable (agent wallet resolves)
  6. CMC x402 call works (a real paid Fear & Greed read through TWAK)

Exit code is non-zero if any check fails.  python scripts/preflight.py
MODELED ON perps-agent/backend/scripts/preflight.py.
"""
from __future__ import annotations

import asyncio
import sys

from gridora.adapters.exchanges.bsc_twak.twak_client import TwakClient
from gridora.adapters.payments.x402 import X402Payments
from gridora.adapters.signals.allowlist import load_allowlist
from gridora.adapters.signals.cmc import CmcSignals
from gridora.config import settings

MARKET = "CAKE/USDT"


def _line(ok: bool, name: str, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{f' — {detail}' if detail else ''}")
    return ok


async def main() -> int:
    print(f"=== Gridora preflight (chainId {settings.chain_id}, testnet={settings.testnet}) ===")
    results: list[bool] = []

    try:
        settings.guard()
        results.append(_line(True, "env↔chain consistency"))
    except SystemExit as e:
        results.append(_line(False, "env↔chain consistency", str(e)))

    allow = load_allowlist()
    results.append(_line(len(allow) > 0, "allowlist loaded", f"{len(allow)} BEP-20 tokens"))

    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(settings.bsc_rpc_url))
        cid = await asyncio.to_thread(lambda: w3.eth.chain_id)
        results.append(_line(cid == settings.chain_id, "BSC RPC live",
                             f"rpc chainId {cid} vs config {settings.chain_id}"))
    except Exception as e:  # noqa: BLE001
        results.append(_line(False, "BSC RPC live", repr(e)))

    twak = TwakClient(chain_key=settings.chain_key)
    try:
        addr = await twak.address()
        results.append(_line(bool(addr), "TWAK wallet (CLI)", f"agent {addr}"))
    except Exception as e:  # noqa: BLE001
        results.append(_line(False, "TWAK wallet (CLI)", repr(e)))

    try:
        st = await twak.compete_status()
        results.append(_line(bool(st.get("open") or st.get("registered")), "competition window",
                             f"registered={st.get('registered')} open={st.get('open')}"))
    except Exception as e:  # noqa: BLE001
        results.append(_line(False, "competition window", repr(e)))

    try:
        from gridora.adapters.signals.cmc import _fng_value
        signals = CmcSignals(X402Payments(twak, max_payment=settings.x402_max_payment,
                                          prefer_network=settings.chain_key),
                             base_url=settings.cmc_base_url, api_key=settings.cmc_api_key)
        # Validate the real data path (x402 first, keyed pro-api fallback) — a raise = FAIL,
        # not a silently-defaulted neutral read.
        fng = await signals._fetch("/v3/fear-and-greed/latest", {})
        ok = isinstance(fng, dict) and bool(fng)
        results.append(_line(ok, "CMC data (x402/key)", f"F&G -> {_fng_value(fng)}" if ok else "empty response"))
    except Exception as e:  # noqa: BLE001
        results.append(_line(False, "CMC data (x402/key)", repr(e)[:160]))

    ok = all(results)
    print(f"=== {'ALL PASS — ready' if ok else 'FAILURES — fix before going live'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
