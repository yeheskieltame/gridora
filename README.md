# Gridora

**Verifiable, non-custodial adaptive-grid trading agent on BNB Chain.**
BNB Hack: AI Trading Agent Edition — Track 1 + Best Use of Trust Wallet Agent Kit.

See [`Gridora-Plan.md`](./Gridora-Plan.md) for the full concept and [`CLAUDE.md`](./CLAUDE.md)
for the build guide + reference-project paths.

## Layout
```
backend/    Python hexagonal engine + agent loop + TWAK/CMC/x402 adapters
contracts/  Foundry: IdentityRegistry · TradeJournal · StrategyLedger (BSC)
frontend/   Next.js read-only public Verifier (viem, no wallet connect)
```

## Reference projects (we port from these)
- Grid engine + x402 + hexagonal ports: `/Users/kiel/Documents/Hacathon/perps-agent`
- Non-custodial agent + on-chain verifier UI: `/Users/kiel/Documents/Hacathon/BridgeAgent`

## Quickstart
```bash
# contracts
cd contracts && forge test && forge script script/Deploy.s.sol --rpc-url bsc_test --broadcast
# backend
cd backend && python3.11 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]" && pytest
python -m gridora.runner --mode dry --market CAKE/USDT          # scripted offline loop check
python -m gridora.runner --mode dry --ui                        # TUI, deterministic brain
python -m gridora.runner --mode dry --ui --brain claude         # TUI, REAL Claude routing strategies
# frontend
cd frontend/web && pnpm install && pnpm dev
```

## Agentic core

Claude is the **strategy router**: each cycle it reads the CoinMarketCap regime and the
live grid state and picks / switches / tunes a strategy (or halts to stablecoin) — what a
blind deterministic bot can't do. It runs via the **local Claude Code CLI** (subscription,
no API key). The deterministic grid engine executes and the guardrails (circuit breaker,
inventory cap, drawdown, token allowlist) hard-enforce risk regardless. If Claude is
unavailable the brain falls back to a deterministic regime classifier — it never bricks.
Watch it live in the Textual TUI (`--ui`); keys: `d` decide now · `k` kill→flat · `q` quit.
