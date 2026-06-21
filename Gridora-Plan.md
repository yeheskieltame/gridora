# Gridora: Project Plan

**BNB Hack: AI Trading Agent Edition (CoinMarketCap × Trust Wallet × BNB Chain)**
Target: **Track 1 (Autonomous Trading Agents, $24k)** + **Special Prize: Best Use of Trust Wallet Agent Kit ($2k)**

> Built by forking two of our own engines: **perps-agent** (the verifiable adaptive-grid engine) for strategy, and **BridgeAgent** (local-first non-custodial agent + on-chain verifier UI) for architecture and UI. We're not starting from zero, we're porting a battle-tested grid engine to the BNB stack.

---

## 1. The one-line pitch

**Gridora is a verifiable, non-custodial adaptive-grid trading agent on BNB Chain.** It runs an adaptive maker-style grid that buys dips and sells rips inside a band, keeps your keys on your machine, signs every swap through the Trust Wallet Agent Kit, and proves what it did on-chain. Pick a coin, set a risk profile, launch, and Gridora trades it autonomously inside hard guardrails and never holds custody.

The product is engineered around the special-prize sentence: *"a new take on an agent a self-custody user would actually let run unattended."* A grid is the most natural strategy to leave running, and TWAK makes it self-custodial end to end.

---

## 2. Why grid trading is the right strategy for *this* competition

The Track 1 mechanics reward exactly what a grid does:

- **≥1 trade/day, 7 over the week** → a grid naturally fires many small trades as price oscillates in its band. Qualification is automatic, not bolted on.
- **Capital must stay deployed (sub-$1 hours score 0%)** → a grid is always working: resting buy/sell levels keep capital in the market the whole window.
- **30% max drawdown = disqualification** → a grid is mean-reverting, not directional. It books many small realized gains instead of betting on a single trend. The tail risk (price trending out of the band into a one-sided bag) is exactly what our circuit breaker + regime veto kill.
- **Ranked on total return** → an adaptive grid compounds spread capture in choppy/ranging markets, which is most of any given week.

Most teams will build directional momentum bots that either moon or get gated out by the drawdown rule. Gridora wins by harvesting volatility and *not blowing up*.

---

## 3. The strategy: adaptive grid (ported from perps-agent)

The core is the maker-only geometric grid from `perps-agent/backend/src/perpsagent/domain/grid.py`, reused almost verbatim. It builds N price levels in a band `[lower, upper]`, places BUY at every level below mid and SELL at every level above, and pairs fills to bank spread.

On top of the static grid, four adaptive layers (already built in perps-agent):

- **Regime-aware bias.** Before sizing, Gridora reads a live regime fingerprint and biases the grid with the trend; it vetoes a lean that would enter at a range extreme. (Signals now come from CoinMarketCap, see §4.)
- **Dynamic re-center.** A supervisor follows price and re-lays the grid around the new mid when price leaves the band (`recenter_interval` in `agent/loop.py`).
- **Circuit breaker** (`app/safety.py: CircuitBreaker`). Hard caps on net inventory and drawdown. On a breach it cancels every order and flattens to stablecoin.
- **Take-profit + trailing stop** (`app/safety.py: ProfitGuard`). Rides a favorable move, then banks the gain after a configurable pullback from the peak.

Optimization target is **risk-adjusted** performance, never raw PnL, which is the correct objective under a hard drawdown DQ.

**What trades:** a spot pair from the **149 eligible BEP-20 tokens**, an allowlisted alt vs. a stablecoin (USDT / USDC / USD1 / FDUSD). One-tap risk presets: **Safe / Balanced / Aggressive** map to band width, level count, and per-trade size (the perps-agent preset pattern).

---

## 4. Adapting to the BNB Hack stack

The perps-agent engine is venue-agnostic (hexagonal ports). Porting to BNB = swap three adapters, change one chain config. The engine and agent loop don't change.

| Layer | perps-agent (Mantle) | → Gridora (BNB Hack) |
|---|---|---|
| **Execution venue** (`ExchangePort`) | Bybit CEX / Mantle DEX | **PancakeSwap on BSC via TWAK-native swap + limit orders**, every tx signed by TWAK (sole execution layer) |
| **Signals** (`SignalPort`) | Elfa / Nansen / Surf | **CoinMarketCap AI Agent Hub** via MCP, F&G, funding rates, momentum, social |
| **Pay-per-call** (`adapters/payments/x402.py`) | x402 over native MNT | **x402 settled through TWAK's native x402** to pay CMC per request |
| **Chain / verifier** (`ChainPort`) | Mantle Sepolia (Ledger/Memory/Vault) | **BSC** (same contracts redeployed) + competition registration |
| **Identity + journal** | none | **BridgeAgent's IdentityRegistry (ERC-8004) + TradeJournal** on BSC |

The on-chain spot-grid mechanics port from `adapters/exchanges/mantle_dex/adapter.py` (the structural model: limit orders map 1:1 to grid levels). On BSC the execution venue is **PancakeSwap**, reached through **TWAK-native actions**: `twak automate add` places a maker **limit order** per grid level (1:1 mapping), and `twak swap` is the taker fallback for pairs without limit support. TWAK signs every order locally. Keys never leave the device. That reroute through TWAK is the heart of the special prize. (TWAK can't sign arbitrary contract calls, so the verifiable proofs use TWAK-native **ERC-8004** instead, see §6.)

---

## 5. Why this wins the "Best Use of TWAK" special prize

| Criterion | Weight | How Gridora scores |
|---|---|---|
| TWAK integration depth | 30 | TWAK is the **sole** execution layer across 3 surfaces: local signing of every grid order, autonomous mode for the unattended loop, and native x402 for data payments |
| Self-custody integrity | 25 | Keys stay on-device the whole trade loop (BridgeAgent's non-custodial agent-wallet pattern); EIP-3009 / Permit2 off-chain signatures authorize on-chain moves |
| Autonomous execution + guardrails | 20 | Hands-off grid loop inside hard policy: inventory cap, drawdown breaker, token allowlist, per-trade & daily limits, slippage guard, regime veto |
| Native x402 usage | 10 | Every decision cycle pays via x402 (through TWAK) for CMC data, real, in the loop, not a README mention |
| Originality + relevance | 10 | A grid is the canonical "leave it running" strategy; non-custodial + verifiable makes it one a real self-custody user would actually trust |
| Demo | 5 | End-to-end self-custody + autonomous-signing loop, proven with BSC tx hashes on the public verifier page |

---

## 6. Architecture (BridgeAgent shape + perps-agent engine)

```
┌──────────────────────── LOCAL MACHINE (non-custodial) ────────────────────────┐
│                                                                                │
│   GRIDORA AGENT (Python, hexagonal, forked from perps-agent backend)          │
│   ┌────────────────────────────────────────────────────────────────────┐      │
│   │  agent/loop.py:  SENSE → RECALL → DECIDE → COMMIT → EXECUTE → LEARN  │      │
│   │  domain/grid.py:  pure grid math (levels, fills, re-center)          │      │
│   │  app/safety.py:   CircuitBreaker + ProfitGuard                       │      │
│   │  GridService facade  ── the ONLY seam the UI talks to ──             │      │
│   └───────────┬───────────────────┬──────────────────┬──────────────────┘      │
│               │                   │                  │                          │
│        SignalPort           PaymentPort         ExchangePort                    │
│               │                   │                  │                          │
│        CMC Agent Hub        x402 (via TWAK)     TWAK signer ── Agent wallet      │
│        (MCP, paid)          pay-per-call        (keys never leave device)        │
└───────────────┼───────────────────┼──────────────────┼──────────────────────────┘
                │                   │                  │
                ▼                   ▼                  ▼
        CoinMarketCap        x402 settlement      PancakeSwap on BSC (TWAK-native)
        live market data     on BNB Chain         live trades → tx hashes
                                                          │
                            ┌─────────────────────────────┴───────────────┐
                            ▼                                              ▼
                  IdentityRegistry (ERC-8004)              TradeJournal (append-only)
                  + StrategyLedger/Memory                  every settled trade mirrored
                            │                                              │
                            └──────────────► Next.js read-only Verifier ◄──┘
                                            (BridgeAgent web: AgentCard,
                                             TradesTable, VerifyPanel, viem,
                                             no wallet connect, public proof)
```

**Decision loop (autonomous, per cycle):**
1. **Sense**: `twak x402 request` pays CMC for F&G, momentum, funding → regime fingerprint.
2. **Recall**: read best verified params for this regime from memory (off-chain mirror).
3. **Decide**: **Claude (local Claude Code) routes the strategy**: pick/switch/halt a grid mode + tune band/levels (deterministic guardrails clamp it). Build grid orders (`domain/grid.py`), fee-aware spacing.
4. **Check**: CircuitBreaker + allowlist + fee-floor + per-trade/daily caps (also enforced by TWAK guardrails).
5. **Commit**: hash the config on-chain *before* trading via **TWAK-native ERC-8004** (`erc8004 set-metadata`, key `gridora.commit.<id>`).
6. **Execute**: TWAK places a **limit order per level** (maker, 1:1) / swaps locally → BSC; a `twak serve --watch` watcher executes resting orders.
7. **Learn**: on close, **attest** the outcome on-chain (TWAK ERC-8004 metadata, `gridora.attest.<id>`); update the off-chain recall memory.

> Proofs use TWAK-native **ERC-8004** (identity + commit/attest metadata) because TWAK can't sign arbitrary contract calls, which keeps the whole loop self-custodial. The custom `contracts/` (IdentityRegistry/TradeJournal/StrategyLedger) are an **optional** read-only mirror, not the primary proof path.

---

## 7. Reuse map (what we fork vs. build new)

**Reuse almost as-is from perps-agent:**
- `domain/grid.py`, `domain/ports.py`, `domain/models.py`, `domain/regime.py`, `domain/pnl.py`
- `app/safety.py` (breaker + profit guard), `app/engine.py`, `app/service.py` (GridService facade), agent loop (`agent/*`)
- `adapters/payments/x402.py` (repoint to CMC + TWAK settlement)
- Telegram one-tap UX patterns (optional secondary UI)

**Reuse from BridgeAgent:**
- `contracts/` IdentityRegistry (ERC-8004) + TradeJournal → redeploy on BSC
- `web/` Next.js read-only verifier (AgentCard, TradesTable, VerifyPanel, viem reads) → the public proof UI
- Non-custodial local agent-wallet + 60s on-chain flush worker pattern

**Build new (the ~20% that's BNB-specific):**
- `adapters/exchanges/bsc_twak/adapter.py`: `ExchangePort` that routes grid orders through **TWAK** signing on BSC (the special-prize centerpiece)
- `adapters/signals/cmc.py`: `SignalPort` over CoinMarketCap AI Agent Hub (MCP)
- Competition registration via `twak compete register` / MCP `competition_register`
- 149-token allowlist guard + chain config for BSC (chainId 56)

---

## 8. Registration (Track 1 is on-chain)

- Competition contract on BSC: `0x212c61b9b72c95d95bf29cf032f5e5635629aed5`
- Register the agent wallet via CLI `twak compete register` **or** MCP `competition_register`.
- **Deadline: before the trading window opens June 22.**
- Also submit agent address + strategy writeup on DoraHacks. Hold non-zero in-scope assets at start.

---

## 9. Build timeline (today June 17, build closes June 21)

We're porting, not building from scratch, so the schedule is realistic.

| Day | Focus |
|---|---|
| **Jun 17–18** | Fork perps-agent backend. Stand up TWAK + CMC Agent Hub locally. Write `bsc_twak` ExchangePort: one grid order signed by TWAK landing on BSC testnet. Confirm x402 CMC call via TWAK. |
| **Jun 19** | Write `cmc.py` SignalPort (F&G/funding/momentum → regime). Wire GridService end-to-end on BSC. Tune CircuitBreaker for the 30% DQ (hard halt ~12%). Port BridgeAgent contracts to BSC + deploy. |
| **Jun 20** | Port the Next.js verifier UI to BSC reads. Paper-run the adaptive grid on a live allowlist pair. Tune Safe/Balanced/Aggressive presets. Unit tests green. |
| **Jun 21** | Register agent on-chain before deadline, fund wallet, dry-run autonomous mode, record demo, submit on DoraHacks. |
| **Jun 22–28** | Live trading. Monitor breaker, ensure capital stays deployed, ≥1 trade/day (the grid handles this). |

---

## 10. Demo plan

1. Show keys staying local, TWAK signs, nothing custodial.
2. One-tap launch a grid on an allowlist pair → x402 CMC data call → regime → grid orders signed by TWAK → **tx hashes on bsctrace**.
3. Force price out of band → show **dynamic re-center**, then the **circuit breaker** flattening to stablecoin. The money shot for "leave it running unattended."
4. Open the public Verifier page → committed config hash + live settled trades from the competition week, recomputable by anyone.

---

## 11. Submission checklist

- Agent registered on-chain (contract `0x212c…aed5`) before Jun 22
- Agent address + strategy writeup on DoraHacks
- Public repo + demo video (reproducible)
- TWAK = sole execution layer across signing + autonomous + x402
- x402 used for real per-request CMC data in the loop
- Grid guardrails: inventory cap, drawdown breaker, allowlist, per-trade/daily limits, slippage
- ≥1 trade/day; capital kept deployed (no dust hours)
- On-chain verifier page live (committed config + settled trades)
- No token launches / fundraising during the event

---

## 12. Risks & mitigations

- **Grid grinds into a one-sided bag in a hard trend** → inventory cap + drawdown breaker + regime veto halt and flatten to stablecoin well before the 30% DQ line.
- **Thin liquidity / high slippage on some allowlist alts** → restrict grid pairs to liquid names; slippage guard aborts bad fills.
- **AMMs have no native limit orders** → use **TWAK's native limit orders** (`twak automate add`) for a maker grid that maps 1:1 to levels; for pairs without limit support, fall back to a synthetic grid via `twak swap` at each level. Either way TWAK signs.
- **Tight timeline** → we fork two working repos; only ~20% (BSC/TWAK/CMC adapters) is net-new.
- **x402 data cost** → ~0.01 USDC/call; cache within a cycle, don't over-poll.

---

## Team

Yeheskiel Yunus Tame ([@YeheskielTame](https://x.com/YeheskielTame))

## Sources

- [BNB Hack hackathon detail (DoraHacks)](https://dorahacks.io/hackathon/bnbhack-twt-cmc/detail)
- [Trust Wallet AgentKit + Binance x402 (CryptoBriefing)](https://cryptobriefing.com/trust-wallet-binance-x402-ai-payments/)
- [Trust Wallet Builders portal](https://portal.trustwallet.com/)
- [CoinMarketCap AI Agent Hub is live](https://coinmarketcap.com/academy/article/coinmarketcap-ai-agent-hub-now-live)
- [CMC AI Agent Hub, x402 docs](https://coinmarketcap.com/api/documentation/ai-agent-hub/x402)
- [bnbagent-sdk (GitHub)](https://github.com/bnb-chain/bnbagent-sdk)
- Internal: `perps-agent/` (grid engine, x402, hexagonal ports) · `BridgeAgent/` (non-custodial agent + on-chain verifier UI)
