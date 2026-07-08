# CLAUDE.md, Gridora

Verifiable, non-custodial trading agent on BNB Smart Chain. Keys never leave the machine: every
order is signed locally through the Trust Wallet Agent Kit (TWAK), which is the sole execution
layer. Market data comes from CoinMarketCap, paid per call via x402 through TWAK. Every settled
trade is mirrored on-chain and readable on a public verifier.

[`README.md`](./README.md) covers the architecture and the quickstart. This file is the operating
manual for you, the model, when you touch the trading path.

---

## Layout

| Path | What | Deploy unit |
|---|---|---|
| `backend/` | Python hexagonal engine, agent loop, adapters, safety, control API | Docker, launchd |
| `contracts/` | Foundry: IdentityRegistry, TradeJournal, StrategyLedger | BSC mainnet (deployed) |
| `frontend/` | Next.js: public Verifier (`/`) and operator Console (`/console`) | Vercel |

`backend/scripts/` holds the live trader and its research harness. It is **gitignored on purpose**:
that code is ours, it moves fast, and it touches real money. Never commit it, never reference it
from anything the repo ships.

---

## The two seams that must not blur

**1. The UI never imports the engine, the adapters, or any chain or wallet SDK.** It reads on-chain
state directly with viem, and talks to the backend only through the typed `GridService` facade
(`backend/src/gridora/app/service.py`) or its HTTP projection, the control API
(`backend/src/gridora/control_api.py`, served by `runner --serve`). The browser wallet signs the
login message and nothing else. Console controls are default-deny: they require a signature from an
address in the `GRIDORA_OWNER` allowlist (comma-separated).

**2. Every venue implements one `ExchangePort`** (`domain/ports.py`). Adding a venue means one
folder under `adapters/exchanges/` plus a `registry.py` entry. The engine and the agent never
change. The only live execution adapter is `bsc_twak`.

---

## Hard trading rules

Each of these was learned from a real loss. They are not suggestions, and no model decision
overrides them.

| Rule | Why it exists |
|---|---|
| **Never rotate out of a red position.** Hold it to recover. | Rotating a red position realizes the loss and then pays entry fees on the replacement. A position stuck red past 24h notifies the user; it is never auto-sold. The only forced exit is the account-level kill switch. |
| **Only take profit when net gain clears every fee and gas cost.** | At the sizes this runs (tens of dollars), a round trip costs roughly 3%. A "+2%" exit is a loss. Sell friction and gas are charged against net before any take-profit fires. |
| **Scans deploy fresh capital only.** | Funding a new candidate by selling an existing one is a rotation in disguise. See rule one. |
| **Never buy a parabolic top.** | A token already up hard on the day is rejected, and price must sit in the lower part of its 24h range. A discount, not a knife. |
| **A 7d swing above 120% is a hard block.** | The brain is not even consulted. LAB's week was 230% and it fell another 55% after entry. One LLM call must never be the only thing between the stack and a token like that. |
| **The thin-token blacklist is empirical.** | Symbols land there because they cost real money, not because they look bad. Do not remove one without new evidence. |
| **Optimize risk-adjusted return, never raw PnL.** | Survival is the objective. A 30% drawdown is a disqualification. |

Every calibration knob lives in a single block at the top of the autopilot. Tune there, never in a
forked copy of the file.

## Hard safety rules

| Rule | Enforcement |
|---|---|
| Testnet by default (chainId 97) | `config.guard()` refuses on an env and chain mismatch |
| Allowlist-guard every market before commit | `allowlist.149.json`, the 149 eligible BEP-20 tokens. A trade outside the list does not count and must not be placed. |
| TWAK is the sole signer | Never put a private key in this process. Never commit or print one. `.secrets/` and `.env` are gitignored. |
| Any mainnet or money action stops and asks the user first | No exceptions |
| Commit the config hash on-chain before trading | Attest the verified outcome after |

---

## TWAK integration

Verified CLI surface (`twak … --json`), chain keys `bsc` and `bsctestnet`:

| Need | Command |
|---|---|
| Execute (taker) | `twak swap` |
| Execute (maker) | `twak automate add`, one limit order per grid level |
| Watch resting orders | `twak serve --watch` |
| Market data | `twak x402 request <cmc-url>`, TWAK pays the micropayment and returns the resource |
| Identity and proofs | `twak erc8004 register`, `twak erc8004 set-metadata` for commit and attest |

Two things about TWAK that bite:

**It cannot sign arbitrary contract calls.** So the ERC-8004 registration is the primary proof path,
and the custom `contracts/` are an optional read-only mirror, written with the agent key via Foundry
`cast` (`adapters/chain/bsc_mirror.py`).

**Its symbol resolver silently swaps an unknown symbol for BNB.** The adapter therefore passes
verified contract addresses and refuses any unmapped token (`bsc_twak/bsc_tokens.py`). Never loosen
that check. It is the difference between a trade and a donation.

---

## Commands

```bash
# backend (Python 3.11)
cd backend && python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" && pytest                          # offline, no keys
python -m gridora.runner --mode dry   --market CAKE/USDT   # offline loop check
python -m gridora.runner --mode paper --auto --serve       # live data, simulated fills, no money

# contracts (Foundry)
cd contracts && forge test
forge script script/Deploy.s.sol --rpc-url bsc --broadcast --verify

# frontend
cd frontend/web && pnpm install && cp .env.example .env && pnpm dev
```

Run modes: `dry` is fully offline, `paper` uses real prices with simulated fills and no money, and
`live` signs real trades through TWAK.

## Git

Commits go up as **@yeheskieltame**. Do not add a Claude co-author trailer.
