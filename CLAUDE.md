# CLAUDE.md — Gridora

Verifiable, non-custodial trading agent on BNB Smart Chain. Keys never leave the machine:
**every order is signed locally through the Trust Wallet Agent Kit (TWAK)**, which is the sole
execution layer. Market data comes from CoinMarketCap (paid per call via x402 through TWAK).
Every settled trade is mirrored on-chain and readable on a public verifier.

Read [`README.md`](./README.md) for the architecture and quickstart. This file is the operating
manual for *you*, the model, when you touch the trading path.

---

## Layout

| Path | What | Deploy unit |
|---|---|---|
| `backend/` | Python hexagonal engine, agent loop, adapters, safety, control API | Docker / launchd |
| `contracts/` | Foundry: IdentityRegistry · TradeJournal · StrategyLedger | BSC mainnet (deployed) |
| `frontend/` | Next.js: public Verifier (`/`) + operator Console (`/console`) | Vercel |

`backend/scripts/` holds the runnable entry points, not library code:

| Script | What |
|---|---|
| `autopilot.py` | **The live bot.** One unified autonomous dip-turn trader. Real money. |
| `live_active.py` | `Trader` — the hardened TWAK swap/balance/equity/journal layer `autopilot` imports. |
| `autopilot_backtest.py` | Grid-search + backtest over real history; imports `autopilot`'s live decision math. |
| `backtest.py` | Offline: drives the real `GridEngine` over deterministic price paths. |
| `preflight.py` | Assert the agent is ready before `--mode live`. |
| `register_competition.py` | One-off on-chain registration. |

---

## The two seams that must not blur

1. **The UI never imports the engine, adapters, or any chain/wallet SDK.** It reads on-chain state
   directly (viem) and talks to the backend only through the typed `GridService` facade
   (`backend/src/gridora/app/service.py`) or its HTTP projection, the control API
   (`backend/src/gridora/control_api.py`, served by `runner --serve`). The browser wallet signs the
   login message only, never a transaction. Console controls are default-deny: they require a
   signature from an address in the `GRIDORA_OWNER` allowlist (comma-separated).

2. **Every venue implements one `ExchangePort`** (`domain/ports.py`). Adding a venue = one folder
   under `adapters/exchanges/` + a `registry.py` entry. The engine and the agent never change. The
   only live execution adapter is `bsc_twak`.

---

## Hard trading rules

These are learned from real losses. They are not suggestions, and no model decision overrides them.

- **Never rotate out of a RED position.** Hold it to recover. A red position that is stuck >24h
  notifies the user; it is never auto-sold. The only forced exit is the account-level kill switch.
- **Only take profit when the net gain clears every fee and gas cost.** At the sizes this runs
  (tens of dollars), a round trip costs ~3% — a "+2%" exit is a loss. `SELL_FRIC` + `GAS_USD` in
  `autopilot.py` are charged against net before any TP fires.
- **Scans deploy fresh capital only.** Never fund a new candidate by selling an existing position.
- **Never buy a parabolic top.** `CHG24_MAX` rejects anything already up hard on the day; `POS_MAX`
  requires the price to sit in the lower part of its 24h range (a real discount, not a knife).
- **`WILD7D_CAP` is a hard block, not a hint.** Above a 120% 7d swing the token is a casino and the
  brain is not even consulted. One LLM call must never be the only thing between the stack and a
  token that falls another 55% after entry.
- **`THIN_TRAPS` is an empirical blacklist.** Tokens land there because they cost real money, not
  because they look bad. Do not remove a symbol from it without new evidence.
- **Optimize risk-adjusted return, never raw PnL.** Survival is the objective; a 30% drawdown is a
  disqualification.

All calibration knobs live in one block at the top of `autopilot.py`. Tune there, never in a forked
copy of the file.

## Hard safety rules

- **Testnet by default** (chainId 97). Refuse on an env↔chain mismatch (`config.guard()`).
- **Allowlist-guard every market** against the 149 eligible BEP-20 tokens *before* commit
  (`allowlist.149.json`). A trade outside the list does not count and must not be placed.
- **TWAK is the sole signer.** Never put a private key in this process. Never commit or print one.
  `.secrets/` and `.env` are gitignored.
- **For any mainnet or money action, stop and ask the user first.**
- Commit the config hash on-chain *before* trading; attest the verified outcome after.

---

## TWAK integration (verified CLI surface: `twak … --json`, chain keys `bsc` / `bsctestnet`)

| Need | Command |
|---|---|
| Execute | `twak swap` (taker) · `twak automate add` (maker limit order, 1 per grid level) |
| Watch resting orders | `twak serve --watch` |
| Market data | `twak x402 request <cmc-url>` — TWAK pays the micropayment and returns the resource |
| Identity / proofs | `twak erc8004 register` · `set-metadata` (commit + attest) |

TWAK **cannot sign arbitrary contract calls.** So the ERC-8004 registration is the primary proof
path, and the custom `contracts/` are an optional read-only mirror written with the agent key via
Foundry `cast` (`adapters/chain/bsc_mirror.py`).

TWAK's symbol resolver silently swaps an unknown symbol for BNB. The adapter therefore passes
**verified contract addresses** and refuses any unmapped token (`bsc_twak/bsc_tokens.py`). Never
loosen that.

---

## Commands

```bash
# backend (Python 3.11)
cd backend && python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" && pytest                          # offline, no keys
python -m gridora.runner --mode dry   --market CAKE/USDT   # offline loop check
python -m gridora.runner --mode paper --auto --serve       # live data, simulated fills, no money
python -u -m scripts.autopilot --selfcheck                 # assert the decision math
python -u -m scripts.autopilot --paper                     # simulated wallet, real market data

# contracts (Foundry)
cd contracts && forge test
forge script script/Deploy.s.sol --rpc-url bsc --broadcast --verify

# frontend
cd frontend/web && pnpm install && cp .env.example .env && pnpm dev
```

Run modes: `dry` (fully offline) · `paper` (real prices, simulated fills, no money) · `live`
(real trades signed by TWAK).
