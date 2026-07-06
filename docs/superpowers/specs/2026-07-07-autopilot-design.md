# Autopilot — unified autonomous dip-turn trader (2026-07-07)

Replaces the 23 one-off `scripts-local/` bots with ONE persistent daemon:
`backend/scripts/autopilot.py`. Distilled from the full trade audit
(11 green / 5 red / 6 scratch, Jun 25–Jul 7): every win used dip-turn entry +
anti-rugi ratchet exit; every loss violated one of those rules.

## Decisions (user, 2026-07-07)
- **Full auto**: bot scans, picks, and executes alone. User can still override in chat.
- **Never-red + time-alert**: no auto red-sell ever; red/stuck > 24 h → macOS
  notification + logged analysis, user decides.
- **All-in, 1 position max** (only sizing that clears ~3 % round-trip cost at $30).

## State machine
FLAT → (scan) → PENDING (2 consecutive confirms) → (buy) → HOLDING → (sell) → FLAT.

**SCAN** (every 120 s, one Gate `/spot/tickers` call, deep-check top candidates):
- BTC regime gate first: 1 h ≥ −1.2 %, 24 h ≥ −5 % — else stay cash (fixes the
  ZRO/AVAX/LAB crash losses).
- Universe = verified `BSC_TOKENS` minus stables and known thin-traps (AXS, ZRO).
- Dip filter: 24 h chg ≤ +9 % (no parabolic tops), range ≥ 4.5 % (no gas-churn
  chop), position ≤ 45 % of range, room to high ≥ 3.5 %, Gate vol ≥ $1 M.
- Turn confirm (deep): higher-low base on 15 m (no knives), 5 m close up,
  taker ≥ 54 %. Must hold on 2 consecutive scans.

**ENTRY**: TWAK quote-sanity (implied price within ±3 % of Gate — the resolver
landmine + pool-divergence guard), then all-in (USDT − $0.50 reserve) by
verified contract address.

**MANAGE** (poll 15 s): net = qty·px·(1−fric) − gas − cost.
- CAP +12 % → bank spike. FAST-reversal (needs price-drop confirm, not a taker
  blip) once net ≥ 3 %. Trailing ratchet: arm at +$0.02, floor = max(+$0.02,
  peak − gap), gap ≈ 1.5 % of cost (0.7 % when BTC weak), whipsaw guard
  `floor ≥ net ≥ −0.03` (gap below BE between polls = HOLD, never realize red).
- RED = HOLD. > 24 h red/stuck → notify + analysis, re-alert every 12 h.
- Exit → journal on-chain (bps), 30 min re-entry cooldown on that token.

## Plumbing
- State `~/.gridora/autopilot.json` (atomic write, survives restarts; live mode
  reconciles with on-chain balances at boot — chain is truth).
- Ledger `~/.gridora/trades.jsonl` — every buy/sell appended (no more memory
  archaeology).
- Keepalive: launchd `com.gridora.autopilot.plist`, `KeepAlive=true` → kills the
  silent ~55 min bot deaths. Logs → `backend/autopilot.log`.
- `--paper` = same code, real Gate data, simulated wallet. Validate paper first,
  flip live by removing the flag from the plist.
- Execution reuses `scripts.live_active.Trader` (RPC balances, receipt-checked
  swaps, journal) — nothing reinvented.

## Not built (YAGNI)
Multi-position, breakout entries (banned by hard rule), web UI, console
integration, per-token param tuning.
