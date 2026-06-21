# CLAUDE.md — Gridora contracts (Foundry, BNB Smart Chain)

Three contracts make the agent verifiable on BSC (OpenZeppelin v5; best-practice).
NOTE: these are an OPTIONAL read-only mirror — the PRIMARY proof path is TWAK-native
ERC-8004 (`twak erc8004 register` / `set-metadata`). Deploy them only if you want the
self-hosted verifier UI to read your own contracts.
- `IdentityRegistry.sol` — **soulbound ERC-721** (OZ `ERC721URIStorage`): one identity
  NFT per owner, `tokenURI` = registration file, `agentWallet` = TWAK signer, transfers
  blocked via `_update`. Gates the journal.
- `TradeJournal.sol` — append-only log, **events-only (no on-chain arrays/loops)**:
  history is the `Recorded` event (read off-chain via getLogs); `record` is O(1), gated
  by `identity.ownerOf`.
- `StrategyLedger.sol` — commit config hash before trading, attest outcome after
  (committer-bound, timestamped; hand-written — no OZ primitive fits).

## ⭐ Port source
- `/Users/kiel/Documents/Hacathon/BridgeAgent/contracts/src/{IdentityRegistry,TradeJournal}.sol` (deployed on Mantle there)
- `/Users/kiel/Documents/Hacathon/perps-agent/contracts/` (StrategyLedger / StrategyMemory / Vault)

## Build / test / deploy
```bash
cd contracts
forge install foundry-rs/forge-std OpenZeppelin/openzeppelin-contracts  # populates lib/ (gitignored); remappings.txt is committed
forge test                                # 14 tests, offline
forge script script/Deploy.s.sol --rpc-url bsc --broadcast --verify     # mainnet; testnet not used (TWAK has no bsctestnet)
```
Copy printed addresses into `backend/.env` (GRIDORA_*_ADDR) and `frontend/web/.env`.

## Note
This is the on-chain VERIFIER, separate from the BNB Hack competition contract
(`0x212c61b9b72c95d95bf29cf032f5e5635629aed5`) you register the agent on via TWAK.
