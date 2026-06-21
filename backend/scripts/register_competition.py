"""Register the agent wallet on the BNB Hack competition contract (BSC).

Run BEFORE the trading window opens (Jun 22). Uses TWAK so the wallet is resolved
and the tx is signed self-custodially.
    python scripts/register_competition.py
"""
from __future__ import annotations

import asyncio

from gridora.adapters.exchanges.bsc_twak.twak_client import TwakClient
from gridora.config import settings


async def main() -> None:
    twak = TwakClient(chain_key=settings.chain_key)
    status = await twak.compete_status()
    print("status:", status)
    res = await twak.compete_register()  # idempotent; registry 0x212c…Aed5
    print(f"registered: hash={res.get('hash')} participant={res.get('participant')} "
          f"explorer={res.get('explorer')}")


if __name__ == "__main__":
    asyncio.run(main())
