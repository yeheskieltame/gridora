# CLAUDE.md: Gridora frontend (Next.js — Verifier + Console)

Two surfaces, one app:

- **`/` Verifier** — read-only proof page. Reads BSC directly with viem, no wallet
  needed to view, no engine import. Server-rendered, auto-refreshes every 30s.
- **`/console` Console** — the operator TUI on the web. Polls the backend control
  API (`runner --serve`, default `http://127.0.0.1:8317`) every 1.5s; anyone can
  WATCH, but controls unlock only after a wallet signature from an address on the
  backend's `GRIDORA_OWNER` allowlist (comma-separated; SIWE-lite: nonce →
  personal_sign → bearer token).
  Controls mirror the Textual TUI 1:1 (auto/manual, strategy 1-6, band/levels/bias,
  decide, kill→flat) including the same keyboard bindings.

## Port source
- `/Users/kiel/Documents/Hacathon/BridgeAgent/web/`: `app/page.tsx`, `lib/data.ts`, `components/{AgentCard,TradesTable}`
- Console read/control model: `backend/src/gridora/control_api.py` (`state_json` is the contract)

## Run
```bash
cd frontend/web && pnpm install && cp .env.example .env  # fill deployed addresses
pnpm dev   # http://localhost:3000
# for the console, run the backend with the API:
#   cd ../../backend && python -m gridora.runner --mode paper --auto --serve [--ui]
#   GRIDORA_OWNER=0xWallet1,0xWallet2 (allowlist) must be set or controls stay locked
#   (default-deny); on a VPS also set GRIDORA_CONTROL_HOST=0.0.0.0 + put TLS in front
```

## Rule
The UI talks to the chain (viem) and to the backend ONLY through the control API /
`GridService` facade. Never import engine adapters or a server-side wallet SDK here.
The wallet in the browser is used for *authentication signatures only* — it never
signs transactions; TWAK on the backend remains the sole transaction signer.
