"""Single TIGHT grid, LIVE on BSC via TWAK (real money). The band is volatility-sized down
to the ±1.5% floor (NOT the wide preset cap), so a ranging token (e.g. ASTER, which oscillates
in a tight box) actually crosses levels and fills OFTEN — that's what satisfies the ≥1-trade/day
rule and books fast small spreads. Commits the config on-chain before trading; per-grid breaker
+ account guard active.

Run (mainnet env like the live deploy):
  GRIDORA_TESTNET=false GRIDORA_CHAIN_ID=56 GRIDORA_BSC_RPC_URL=https://bsc-dataseed1.bnbchain.org \
  GRIDORA_SWAP_FALLBACK=true GRIDORA_RECENTER_INTERVAL_S=30 PYTHONUNBUFFERED=1 \
    python -u -m scripts.live_grid ASTER/USDT BALANCED 16 2.0
  # argv: market  preset  margin  range_pct   (range_pct 2.0 -> band hits the ±1.5% tight floor)
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal

from gridora.adapters.exchanges.bsc_twak.adapter import BscTwakExchange
from gridora.adapters.exchanges.bsc_twak.twak_client import TwakClient
from gridora.adapters.payments.x402 import X402Payments
from gridora.adapters.signals.allowlist import load_allowlist
from gridora.adapters.signals.cmc import CmcSignals
from gridora.agent.loop import LearningLoop
from gridora.app.safety import Allowlist
from gridora.app.service import GridService
from gridora.config import settings
from gridora.domain.models import RiskPreset, Venue
from gridora.runner import _build_guards, _live_chain


async def main() -> None:
    market = sys.argv[1] if len(sys.argv) > 1 else "ASTER/USDT"
    preset = RiskPreset((sys.argv[2] if len(sys.argv) > 2 else "BALANCED").upper())
    margin = Decimal(sys.argv[3] if len(sys.argv) > 3 else "16")
    rng = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0   # <=2.0 -> band clamps to the ±1.5% floor

    settings.guard()
    settings.assert_twak_creds()
    twak = TwakClient(chain_key=settings.chain_key)
    ex = BscTwakExchange(twak, chain_id=settings.chain_id, chain_key=settings.chain_key,
                         use_limit_orders=not settings.swap_fallback, slippage_pct=settings.slippage_pct)
    signals = CmcSignals(X402Payments(twak, max_payment=settings.x402_max_payment,
                                      prefer_network=settings.chain_key),
                         base_url=settings.cmc_base_url, api_key=settings.cmc_api_key)
    chain = _live_chain(twak)
    allow = Allowlist(symbols=load_allowlist())
    allow.assert_permits(market)   # both legs eligible BEFORE anything signs
    breaker, profit, account = _build_guards()
    loop = LearningLoop(ex, signals, chain, allow, breaker, profit,
                        recenter_interval=settings.recenter_interval_s or 30.0, account_guard=account)
    svc = GridService(loop, venue=Venue.BSC_TWAK)

    print(f"=== TIGHT GRID — {market} {preset.value} margin {margin} range_pct {rng} "
          f"(band → ±1.5% floor) on BSC via TWAK ===", flush=True)
    res = await svc.launch(market, preset, margin, range_24h_pct=rng)
    print(f"launched {res.instance_id}: {res.orders_placed} orders, commit {res.commit_tx}", flush=True)
    print(f"rationale: {res.rationale}", flush=True)
    await loop.supervise(res.instance_id)   # runs until interrupted; guards flatten on a breach


if __name__ == "__main__":
    asyncio.run(main())
