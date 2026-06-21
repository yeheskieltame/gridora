# CLAUDE.md — Gridora backend (Python, hexagonal)

Adaptive-grid trading agent. The engine/agent are pure; all I/O is behind ports
(`domain/ports.py`). The ONLY execution adapter is `bsc_twak` (signs via TWAK).

## ⭐ Port source (open side by side)
- Engine/agent/safety/x402: `/Users/kiel/Documents/Hacathon/perps-agent/backend/src/perpsagent/`
- Non-custodial wallet + on-chain mirror: `/Users/kiel/Documents/Hacathon/BridgeAgent/bridgeagent/src/bridgeagent/`

## File-by-file port map
| Gridora file | Copy from |
|---|---|
| `domain/grid.py` | `/Users/kiel/Documents/Hacathon/perps-agent/backend/src/perpsagent/domain/grid.py` |
| `domain/ports.py` | `.../domain/ports.py` (done — adapted) |
| `domain/models.py` | `.../domain/models.py` |
| `domain/regime.py` | `.../domain/regime.py` |
| `app/safety.py` | `.../app/safety.py` (breaker + profit guard) |
| `app/engine.py` | `.../app/engine.py` |
| `app/service.py` | `.../app/service.py` (GridService facade) |
| `agent/loop.py` (+sense/recall/decide/gates/learn) | `.../agent/*` |
| `adapters/payments/x402.py` | `.../adapters/payments/x402.py` |
| `adapters/exchanges/bsc_twak/adapter.py` | NEW — model on `.../adapters/exchanges/mantle_dex/adapter.py` |
| `adapters/exchanges/fake.py` | `.../adapters/exchanges/fake.py` |
| `adapters/store/sqlite_store.py` | `.../adapters/store/sqlite_store.py` |

## Build/run
```bash
cd backend && python3.11 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest                              # offline, no keys
python -m gridora.runner --mode dry --market CAKE/USDT
```

## Rules
- Testnet by default (chainId 97). Refuse env↔chain mismatch (config.guard()).
- Allowlist-guard every market against the 149 BEP-20 tokens BEFORE commit.
- TWAK is the sole signer — never put a private key in this process.
- Tune CircuitBreaker so hard halt fires ~ -12% (DQ is -30%).
