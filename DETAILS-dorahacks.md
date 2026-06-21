## Gridora

**The autonomous grid trader you can actually leave alone.** A verifiable, non custodial adaptive grid trading agent on BNB Chain. It reads the market through CoinMarketCap, buys dips and sells rips inside a volatility sized band, signs every order locally through the Trust Wallet Agent Kit, and proves what it did on chain.

> Most trading bots ask for your keys, then show you a screenshot and call it a track record. Gridora keeps your keys, obeys your rules, and writes its proof to the chain.

Demo: https://youtu.be/6AKQu4t0ur4
Live verifier: https://gridora.vercel.app
Code: https://github.com/yeheskieltame/gridora

---

### Why a grid wins this competition

The scoring rewards exactly what a grid is good at. It trades often, so the one trade per day minimum is automatic. It keeps capital deployed the whole window. It is mean reverting, not a single directional bet, so the 30 percent drawdown gate is a feature instead of a threat. Most teams ship a momentum bot that either moons or gets disqualified. Gridora wins by staying alive and compounding small edges.

---

### How it works

Each cycle the agent senses the market, decides a grid shape, pins its plan on chain, executes through TWAK, and attests the result.

- **Sense.** Read the CoinMarketCap regime (Fear and Greed, momentum) and the token's recent volatility.
- **Decide.** Claude routes the strategy and tunes band, levels, and bias. Guardrails clamp the choice. It falls back to a deterministic classifier if the model is offline, so it never bricks.
- **Check.** Circuit breaker, allowlist, fee floor, and per trade caps are verified before anything is placed.
- **Commit.** The config hash is pinned on chain before a single order. A timestamped pre commitment that cannot be backdated.
- **Execute.** TWAK signs a maker limit order per grid level on PancakeSwap. A swap fallback covers pairs without limit support.
- **Learn.** When an episode closes, the booked outcome is attested on chain and every settled trade is mirrored to the TradeJournal.

---

### The strategy is adaptive

A grid does not predict direction. It harvests the oscillation that exists in almost every market most of the time, banking many small realized gains.

- **Volatility sized band.** Band width tracks the token's recent 24h range, clamped to the risk preset. A flat token gets a tight band so price actually crosses levels.
- **Volatility first selection.** The universe picker scores the 149 eligible tokens by volatility, then relative strength and liquidity. A grid needs a token that moves.
- **Regime bias.** A CoinMarketCap read leans the grid long, neutral, or short for the regime.
- **Fee aware spacing.** Levels are never tighter than a round trip cost (two swap fees plus slippage plus gas), so every banked spread clears fees.
- **Re center with hysteresis.** When price leaves the band, the grid re lays around the new mid. A buffer stops it churning on every wobble.

It optimizes risk adjusted return, never raw PnL. The competition disqualifies a 30 percent drawdown, so survival is the objective.

---

### Guardrails, hard enforced and independent of the model

- **Circuit breaker.** Caps drawdown and net inventory. On a breach it cancels every order and flattens to stablecoin.
- **Profit guard.** Rides a favorable move, then banks the gain after a set pullback from the peak.
- **Account guard.** Portfolio level kill switch that flattens everything well under the 30 percent disqualification line.
- **Allowlist.** Refuses any market outside the 149 eligible BEP-20 tokens before commit.

---

### Trust Wallet Agent Kit is the heart, not the plumbing

TWAK is the only signer and the only execution layer, used across five surfaces.

- **Local signing.** Every limit order, swap, and flatten is signed locally by TWAK. The Python process never sees a private key.
- **Autonomous mode.** The full sense to execute loop runs unattended, inside the rules you set.
- **Native limit orders.** A maker grid maps one to one onto TWAK limit orders on PancakeSwap. A real maker strategy, not a single swap call.
- **Native x402.** Each decision cycle pays per request for CoinMarketCap data through TWAK x402. Real, in the loop, every cycle.
- **TWAK ERC-8004.** Identity, commit, and attest are signed by TWAK as native ERC-8004 metadata. The proof path is self custodial end to end.

---

### Verifiable by design

The agent commits its grid config hash on chain before trading and attests the outcome after. Every settled trade is mirrored to an append only TradeJournal. A public Next.js page reads BNB Chain directly with viem, with no wallet connect and no backend, so anyone can recompute the result.

---

### Architecture

The engine is hexagonal. The domain is pure grid math, regime, and universe. The app layer orchestrates and enforces safety. All input and output sits behind ports, so a venue or data source is one adapter folder and the engine never changes. The UI never imports the engine or any wallet SDK.

`backend/` Python hexagonal engine, agent loop, TWAK and CMC adapters, safety.
`contracts/` Foundry contracts for IdentityRegistry, TradeJournal, StrategyLedger.
`frontend/` Next.js read only public Verifier with viem, no wallet connect.

Run modes: `dry` is fully offline, `paper` uses live prices with simulated fills and no money, `live` signs real trades through TWAK.

---

### On chain proof

**Everything is live on BNB Smart Chain mainnet (chainId 56).** The agent is registered for the competition and the full verifiable proof stack is deployed and readable right now.

- **Agent wallet:** [0x7053676258ef5bFB9b27FCF42092F13fB37B9989](https://bscscan.com/address/0x7053676258ef5bFB9b27FCF42092F13fB37B9989)
- **Competition registration:** registered (confirmed via compete status)
- **Registration tx:** [0x11137b00830122e2949620920e6538ccf7c3cb915706cf55e8231f7ea253f692](https://bscscan.com/tx/0x11137b00830122e2949620920e6538ccf7c3cb915706cf55e8231f7ea253f692)
- **BNB Hack competition contract:** [0x212c61b9b72c95d95bf29cf032f5e5635629aed5](https://bscscan.com/address/0x212c61b9b72c95d95bf29cf032f5e5635629aed5)
- **IdentityRegistry (ERC-8004):** [0x400B0D1a98735871175D3B3C231A6250322ECA5A](https://bscscan.com/address/0x400B0D1a98735871175D3B3C231A6250322ECA5A)
- **TradeJournal:** [0xE946C28ea10bf29AcA9a094f66079De84a50d409](https://bscscan.com/address/0xE946C28ea10bf29AcA9a094f66079De84a50d409)
- **StrategyLedger:** [0x56D4831a39A991Ac0fa8CAe533Cb74E47A5DD79d](https://bscscan.com/address/0x56D4831a39A991Ac0fa8CAe533Cb74E47A5DD79d)

---

### Tech stack

- **Engine:** Python 3.11, hexagonal architecture, pure domain core.
- **Execution and custody:** Trust Wallet Agent Kit, native x402, TWAK ERC-8004.
- **Venue:** PancakeSwap on BNB Chain.
- **Market data:** CoinMarketCap AI Agent Hub over MCP, paid per call with x402.
- **Strategy router:** Claude via local Claude Code CLI, deterministic fallback.
- **Contracts:** Solidity, Foundry.
- **Verifier:** Next.js, viem, read only.

---

### Links

- **GitHub:** https://github.com/yeheskieltame/gridora
- **Live verifier:** https://gridora.vercel.app
- **Demo video:** https://youtu.be/6AKQu4t0ur4
- **X:** https://x.com/YeheskielTame

---

### Built on

BNB Smart Chain · Trust Wallet Agent Kit · CoinMarketCap AI Agent Hub · PancakeSwap

### Team

Yeheskiel Yunus Tame ([@YeheskielTame](https://x.com/YeheskielTame))

---

<!-- ============================================================= -->
<!-- COPY READY FIELDS FOR THE OTHER TABS (not part of Details)     -->
<!-- ============================================================= -->

## Team information (paste in the Team tab)

We build verifiable, non custodial trading agents on BNB Chain. Gridora reuses two of our own production grade systems, a hexagonal grid engine and an on chain proof stack, rebuilt around the Trust Wallet Agent Kit as the sole execution layer. Strengths span Python trading engines, Solidity, and self custody infrastructure, shipping backend, smart contracts, and a public verifier UI as one coherent product. The verifier contracts are already deployed and live on BNB Chain.

## Contact (paste in the Contact tab)

Email: yeheskielyunustame13@gmail.com
Discord: @yeheskieltame
X: x.com/YeheskielTame

## Profile field values

BUIDL name: Gridora
Is this BUIDL an AI Agent: Yes
GitHub: https://github.com/yeheskieltame/gridora
Website: https://gridora.vercel.app
Demo video: https://youtu.be/6AKQu4t0ur4
Social: https://x.com/YeheskielTame
Vision: The autonomous grid trader you can actually leave alone. Gridora reads CoinMarketCap, runs an adaptive grid on BNB Chain, and signs every trade locally through Trust Wallet Agent Kit. Keys stay yours, proofs on chain.
