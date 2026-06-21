# CLAUDE.md — Gridora frontend (Next.js public Verifier)

Read-only proof page. Reads BSC directly with viem — NO wallet connect, NO engine
import. Server-rendered, auto-refreshes every 30s.

## ⭐ Port source
- `/Users/kiel/Documents/Hacathon/BridgeAgent/web/` — `app/page.tsx`, `lib/data.ts`, `components/{AgentCard,TradesTable}`
- Optional product UI (one-tap launch / Telegram): `/Users/kiel/Documents/Hacathon/perps-agent/frontend/`

## Run
```bash
cd frontend/web && pnpm install && cp .env.example .env  # fill deployed addresses
pnpm dev   # http://localhost:3000
```

## Rule
UI talks to the chain (viem) and, if it needs agent actions, only the backend
`GridService` facade. Never import adapters or a wallet SDK here.
