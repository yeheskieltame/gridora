"""Headless live PAPER soak — run a real grid on REAL CoinGecko prices with simulated
fills (no money, no TWAK, no chain) across a fixed token set, until killed. Validates
execution/plumbing before risking real capital.

Reuses the Portfolio + supervise() machinery; restricts the allowlist to our chosen tokens
so the selector picks exactly those (not the volatility-misranked top-12). Prints a status
line every minute; fills/PnL also land in gridora_paper.db.

Run (background):  python -m scripts.paper_soak PENGU LDO FIL IP
Stop:              kill the process (Ctrl-C if foreground → books + prints final)
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal

from gridora.app.portfolio import Portfolio
from gridora.app.safety import Allowlist
from gridora.config import settings
from gridora.domain.models import RiskPreset, Venue
from gridora.runner import _paper_adapters

MARGIN = Decimal("400")          # simulated quote across the basket (no real money)
DEFAULT = ["PENGU", "LDO", "FIL", "IP"]


async def main() -> None:
    tokens = [t.upper() for t in (sys.argv[1:] or DEFAULT)]
    signals, chain, _store, exchange_for = _paper_adapters(MARGIN)
    allow = Allowlist(symbols=frozenset({*tokens, "USDT"}))
    pf = Portfolio(exchange_for, signals, chain, allow, MARGIN, venue=Venue.FAKE, quote="USDT",
                   n_markets=len(tokens), preset=RiskPreset.SAFE,
                   recenter_interval=settings.recenter_interval_s or 15.0,
                   agent_address=settings.agent_address or "0xGRIDORA_AGENT")

    print(f"=== PAPER SOAK — SAFE, ${MARGIN} across {tokens} (real CoinGecko prices) ===", flush=True)
    picks = await pf.launch()
    for p in picks:
        print(f"  {p.market:<12} bias {p.bias:+d}  weight {float(p.weight):.2f}  "
              f"7d {p.pct_7d:+.1f}%  range {p.range_24h_pct:.1f}%", flush=True)

    async def status() -> None:
        tick = 0
        while True:
            await asyncio.sleep(60)
            tick += 1
            fills = sum(e.fill_count for e in pf.engines.values())
            print(f"[{tick:>3}m] realized {float(pf.total_realized):+.4f} quote "
                  f"({float(pf.total_realized / MARGIN * 100):+.3f}%) · fills {fills} · "
                  f"grids {len(pf.engines)}", flush=True)

    st = asyncio.create_task(status())
    try:
        await pf.supervise()                       # until a guard trips or the process is killed
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        st.cancel()
        await pf.close_all()
        print(f"=== SOAK STOPPED — realized {float(pf.total_realized):+.4f} quote "
              f"({float(pf.total_realized / MARGIN * 100):+.3f}%) ===", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
