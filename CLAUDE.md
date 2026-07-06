# CLAUDE.md: Gridora (repo root)

**Verifiable, non-custodial adaptive-grid trading agent on BNB Chain.**
Built for **BNB Hack: AI Trading Agent Edition** (CoinMarketCap × Trust Wallet × BNB Chain).
Target: **Track 1 (Autonomous Trading Agents)** + **Special Prize: Best Use of Trust Wallet Agent Kit**.

Full concept and strategy: see [`Gridora-Plan.md`](./Gridora-Plan.md).

## What this is

Gridora runs an **adaptive maker-style grid** on BSC. It buys dips / sells rips inside a
band, biases with the regime, re-centers when price leaves the band, and halts on a
circuit breaker. Keys never leave the machine; **every order is signed locally through
the Trust Wallet Agent Kit (TWAK)**. Market data comes from the **CoinMarketCap AI Agent
Hub**, paid per call via **x402** through TWAK. Every settled trade is mirrored on-chain
and shown on a public read-only verifier.

## Reference projects (we are PORTING these, copy from them, don't reinvent)

Two of our own working repos are the source of truth. Open them side by side:

| What we need | Copy from | Path |
|---|---|---|
| Grid engine, hexagonal ports, agent loop, safety, x402 | **perps-agent** | `/Users/kiel/Documents/Hacathon/perps-agent` |
| Non-custodial agent wallet, on-chain journal, Next.js verifier UI | **BridgeAgent** | `/Users/kiel/Documents/Hacathon/BridgeAgent` |

Key files to lift (with their original paths):

- Grid math → `perps-agent/backend/src/perpsagent/domain/grid.py`
- Ports (ExchangePort/SignalPort/ChainPort/StorePort) → `perps-agent/backend/src/perpsagent/domain/ports.py`
- Models → `perps-agent/backend/src/perpsagent/domain/models.py`
- Regime → `perps-agent/backend/src/perpsagent/domain/regime.py`
- Circuit breaker + profit guard → `perps-agent/backend/src/perpsagent/app/safety.py`
- GridService facade → `perps-agent/backend/src/perpsagent/app/service.py`
- Agent loop (sense→recall→decide→commit→execute→learn) → `perps-agent/backend/src/perpsagent/agent/loop.py`
- x402 pay-per-call → `perps-agent/backend/src/perpsagent/adapters/payments/x402.py`
- On-chain spot-grid structural reference (limit-order→level mapping) → `perps-agent/backend/src/perpsagent/adapters/exchanges/mantle_dex/adapter.py` (but Gridora's venue is **PancakeSwap via TWAK-native swap + limit orders** (`twak swap` / `twak automate add`), NOT iZiSwap).
- ERC-8004 identity + append-only journal contracts → `BridgeAgent/contracts/src/{IdentityRegistry,TradeJournal}.sol`
- Read-only verifier web (viem, no wallet connect) → `BridgeAgent/web/{app/page.tsx,lib/data.ts,components/*}`

## Monorepo boundaries (each top dir is its own deploy unit)

| Path | What |
|---|---|
| `backend/` | Python engine, hexagonal adapters, agent loop, GridService facade |
| `contracts/` | Foundry (Solidity): IdentityRegistry · TradeJournal · StrategyLedger |
| `frontend/` | Next.js: public Verifier (`/`, viem reads) + operator Console (`/console`, wallet-gated controls via the backend control API) |

## The one cross-team rule

The UI **never imports the engine, adapters, or any chain/wallet SDK**. It reads on-chain
state directly (viem) and talks to the backend only through the typed **`GridService`**
facade (`backend/src/gridora/app/service.py`) / its HTTP projection, the **control API**
(`backend/src/gridora/control_api.py`, exposed by `runner --serve`). Keep that seam small.
Console controls are default-deny: they require a wallet signature from the
`GRIDORA_OWNER` allowlist (comma-separated addresses); the browser wallet only signs
the login message, never transactions.

## Modular venues (hexagonal)

Every venue implements one **`ExchangePort`** (`domain/ports.py`). Add a venue = one folder
under `adapters/exchanges/` + a `registry.py` entry. The engine and agent never change.
For Gridora the only execution adapter is **`bsc_twak`**, which signs through TWAK. This is
the centerpiece of the "Best Use of TWAK" prize: TWAK is the *sole* execution layer.

**Verified TWAK-native integration (CLI `twak … --json`; chain keys `bsc`/`bsctestnet`):**
- Execution = **PancakeSwap** via `twak automate add` (maker **limit order** per grid level, 1:1) with `twak swap` as the taker fallback (`swap_fallback`). A `twak serve --watch` watcher executes resting orders. No hand-rolled router calldata; TWAK resolves tokens by symbol + signs locally.
- Data = `twak x402 request <cmc-url>` (TWAK pays the x402 micropayment + returns the resource).
- **Proofs/identity = TWAK-native ERC-8004** (`twak erc8004 register` + `set-metadata` for commit/attest); TWAK can't sign arbitrary contract calls, so the custom `contracts/` are an **optional** read-only mirror, not the primary proof path.
- Registration = `twak compete register` (registry `0x212c…Aed5`).
- **Strategy routing = Claude (local Claude Code CLI)** picks/switches/halts grid modes from the CMC regime; deterministic guardrails still hard-enforce. Grids are **fee-aware** (level spacing ≥ 2×fee+slippage+gas).

## BNB Hack specifics

- **Chain:** BNB Smart Chain (BSC), chainId **56** (testnet 97 for dev).
- **Eligible assets:** the 149 BEP-20 tokens on CoinMarketCap (hardcoded allowlist). Trades outside the list don't count.
- **Competition contract (BSC):** `0x212c61b9b72c95d95bf29cf032f5e5635629aed5`
- **Register before Jun 22** via `twak compete register` / MCP `competition_register`.
- **Rules:** ≥1 trade/day (7 over the week), keep capital deployed (sub-$1 hour = 0%), 30% max drawdown = DQ.

## Defaults

- **Testnet always** unless explicitly told mainnet. Refuse on env↔URL mismatch.
- Commit the grid config hash on-chain BEFORE trading; attest the verified outcome after.
- Optimize **risk-adjusted** performance, never raw PnL.
- Never commit or print private keys. `.secrets/` and `.env` are gitignored.
- For any mainnet/money action, stop and ask.

## Build order (see Gridora-Plan.md §9 for the dated timeline)

1. `backend/`: fork perps-agent, write the `bsc_twak` ExchangePort + `cmc` SignalPort.
2. `contracts/`: port BridgeAgent contracts, deploy to BSC.
3. `frontend/`: port BridgeAgent verifier to read BSC.
4. Register on-chain, dry-run autonomous mode, demo, submit.
