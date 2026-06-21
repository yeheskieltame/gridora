# Gridora: DoraHacks Submission Copy

Tinggal copy per bagian ke form BUIDL. Bahasa Inggris (juri internasional). Tanpa garis "--", tanpa em dash.

---

## BUIDL name

Gridora

## Tagline (one liner)

The autonomous grid trader you can actually leave alone.

## Is this BUIDL an AI Agent?

Yes

## Category

AI Agent, DeFi, Trading

## Vision (short, for the Vision field)

Gridora is a non custodial, verifiable grid trading agent on BNB Chain. It reads the market through CoinMarketCap, runs an adaptive grid that buys dips and sells rips inside a band, and signs every trade locally through the Trust Wallet Agent Kit. Your keys never leave your wallet. Every settled trade is proven on chain. It is built to harvest volatility without blowing up.

---

## Full description (main BUIDL description)

Most trading bots ask you to hand over your keys, then show you a screenshot and call it a track record. Gridora flips that. It is the autonomous agent a self custody user would actually trust to run unattended.

Gridora runs an adaptive maker style grid on PancakeSwap. It places buy orders below the mid price and sell orders above, banking the spread as price oscillates. A live regime read from the CoinMarketCap AI Agent Hub biases the grid with the trend, a supervisor re centers the grid when price leaves the band, and a hard circuit breaker flattens to stablecoins long before risk gets dangerous.

Trust Wallet Agent Kit is the single execution layer. Gridora never holds your key. Every limit order, every swap, every flatten is signed locally by TWAK inside the rules you set: token allowlist, per trade and daily caps, slippage bounds, and a drawdown circuit breaker. The agent pays for its own market data per request over x402, settled through TWAK, so the data spend is part of the trade loop and not a side channel.

Everything Gridora does is verifiable. Before the first order it commits the strategy hash on chain through a TWAK native ERC 8004 identity. After each episode closes it attests the outcome on chain. The result is a public record anyone can recompute, with no dashboard and no account required.

Why a grid. The competition rewards exactly what a grid is good at. It trades often, so the daily minimum is automatic. It keeps capital deployed the whole window. It is mean reverting, not a single directional bet, so the drawdown gate is a feature instead of a threat. Most teams ship a momentum bot that either moons or gets disqualified. Gridora wins by staying alive and compounding small edges.

---

## Strategy explanation (Track 1 requires this)

Strategy: adaptive maker grid with regime bias and a hard risk engine.

How it decides. Each cycle Gridora pulls a regime fingerprint from CoinMarketCap: Fear and Greed, funding rates, momentum, and social heat. That fingerprint sets the grid bias. Risk off leans defensive and can rotate to stablecoins. Neutral runs a balanced band. Risk on leans into momentum with strict per token caps.

How it trades. It builds a geometric grid of price levels in a band around the current mid. Buy orders rest below mid, sell orders rest above. Each matched buy and sell banks the spread. The grid is fee aware: level spacing must clear a full round trip cost of two times the swap fee plus slippage plus gas, otherwise it widens and drops levels. This stops the bot from churning fees.

How it stays safe. A circuit breaker caps net inventory and drawdown and flattens on a breach. A profit guard banks gains with a trailing stop. A supervisor re centers the grid when price exits the band. An allowlist rejects any token outside the 149 eligible BEP 20 set before a single order is placed. The optimization target is risk adjusted return, never raw PnL.

Presets. One tap Safe, Balanced, or Aggressive map to band width, level count, deployed fraction, and the drawdown cap. Safe books many tight spreads and halts early. Aggressive widens the band and deploys more while still staying well under the disqualification line.

---

## Why this wins Best Use of Trust Wallet Agent Kit

TWAK is the sole execution layer, used across three surfaces, not one swap call with the real logic living elsewhere.

Integration depth. Local signing for every order, autonomous mode for the unattended loop, native limit orders for a real maker grid, and native x402 for data payments.

Self custody integrity. Keys stay inside TWAK on your machine for the entire trade loop. Gridora never sees a private key.

Autonomous execution and guardrails. The agent signs and processes its own transactions inside hard rules: token allowlist, per trade and daily limits, slippage protection, and a drawdown circuit breaker.

Native x402. Each decision cycle pays per request for CoinMarketCap data through TWAK x402. Real, in the loop, every cycle.

Originality and relevance. A grid is the canonical leave it running strategy. Making it non custodial and verifiable turns it into something a real self custody user would actually let trade unattended.

---

## Tech stack

Backend: Python, hexagonal architecture. Pure grid engine, agent loop of sense, recall, decide, commit, execute, learn. A single GridService facade.

Execution and custody: Trust Wallet Agent Kit. Limit orders and swaps on PancakeSwap, BNB Chain. Native x402 payments. TWAK native ERC 8004 identity for commit and attest.

Data: CoinMarketCap AI Agent Hub over MCP, paid per call with x402.

Verifier: Next.js read only page that reads BNB Chain directly with viem. No wallet connect, no account.

Chain: BNB Smart Chain, chain id 56. Testnet 97 for development.

---

## Links

GitHub: https://github.com/yeheskieltame/gridora
Demo video: https://youtu.be/6AKQu4t0ur4
Website: https://gridora.vercel.app
Social: https://x.com/YeheskielTame

## On chain proof (BNB Smart Chain mainnet, chain 56)

Agent wallet: 0x7053676258ef5bFB9b27FCF42092F13fB37B9989
Competition contract: 0x212c61b9b72c95d95bf29cf032f5e5635629aed5
Competition registration tx: 0x11137b00830122e2949620920e6538ccf7c3cb915706cf55e8231f7ea253f692
ERC 8004 identity: agentId 140004, URI https://gridora.vercel.app
IdentityRegistry: 0x400B0D1a98735871175D3B3C231A6250322ECA5A
TradeJournal: 0xE946C28ea10bf29AcA9a094f66079De84a50d409
StrategyLedger: 0x56D4831a39A991Ac0fa8CAe533Cb74E47A5DD79d
All three verifier contracts are verified on BscScan.

---

## Suggested demo video script (about 2 minutes)

1. Open on the verifier page. State the one liner. Keys stay in your wallet, every trade is on chain.
2. Show the agent wallet is controlled by TWAK. Nothing custodial.
3. Launch a grid on an allowlist pair. Show the x402 data call, the regime read, the limit orders signed by TWAK, and a tx hash on bscscan.
4. Force a drawdown. Show the circuit breaker flatten to stablecoin automatically. This is the leave it running moment.
5. Close on the verifier showing the committed strategy hash and the attested outcome on chain.
