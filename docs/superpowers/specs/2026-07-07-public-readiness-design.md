# Gridora public-readiness architecture (2026-07-07)

Mandate: take the BNB Hack 3rd-place project to public-runnable, high-performance,
many-users. This doc records the load-bearing decisions.

## The multi-user model (the honest one)

Gridora is **non-custodial by construction**: TWAK signs locally, keys never leave the
machine. A hosted platform that trades *for* users would require holding their keys —
the opposite of the project's whole thesis. So "many users" is:

1. **Shared public read layer** — the verifier (gridora.vercel.app) is the product
   surface anyone can use with zero setup. TradeJournal + ERC-8004 are already
   agentId-keyed, so the same frontend can verify *any* agent, not just ours.
2. **Self-hosted agents** — each user runs their own agent (their TWAK wallet, their
   keys, their VPS/laptop) via `docker compose up`. Safe default = paper mode.
   The operator console (`/console`, wallet-sig gated, GRIDORA_OWNER allowlist)
   is each user's private cockpit.

No accounts, no database of users, no custody. Scale = N independent agents + one
stateless verifier reading the chain.

## Performance decisions

- **TWAK REST transport** (the big one): `twak serve --rest` exposes
  `POST /actions/:name` (Bearer HMAC). TwakClient gains a REST path behind
  `TWAK_REST_URL`, CLI subprocess (~1-3 s per call) stays as fallback. Verified
  actions: wallet_balance, get_address, swap, get_swap_quote, create/list
  automations, x402_request, erc8004_*, competition_*.
- **Verifier**: kill the fetchStats waterfall (one Promise.all), ISR stays 30 s.
- **Console**: poll pauses on hidden tab, exponential backoff on errors.
- **Control API stays stdlib** (ThreadingHTTPServer). It's one operator + a public
  read model — FastAPI would be dead weight. Added instead: per-IP rate limiting
  (auth 10/min, control 30/min), configurable CORS origin.

## New public API surface (control API)

- `GET /api/autopilot` — autopilot state (~/.gridora/autopilot.json) + freshness.
- `GET /api/trades?limit=N` — trade ledger tail (trades.jsonl), newest first.
Both unauthenticated reads, same trust level as /api/state. Console renders them
as the Autopilot panel.

## BNB ecosystem alignment

Already native: TWAK (wallet), x402 (paid data), ERC-8004 (identity #140004),
BSC mainnet contracts. Added: 8004scan.io link on the verifier (public identity
proof). Deliberately skipped for now: Agent0 subgraph (needed only when the
verifier aggregates many agents), ERC-8183 escrow (no agent-to-agent commerce
yet), bnbagent-studio scaffolding (Gridora predates it and already matches its
stack). Revisit when a real need appears.

## Not built (YAGNI, revisit on demand)

Hosted multi-tenant custody, user accounts/DB, websocket push (poll+backoff is
fine at this scale), contract redeploys (deployed set is live and journaling;
any change = new addresses + migration for zero functional gain).
