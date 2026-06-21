// Deployed addresses (fill from `forge script` output). ⭐ PORT FROM BridgeAgent/web/lib/contracts.ts
export const ADDR = {
  identity: (process.env.NEXT_PUBLIC_IDENTITY_ADDR ?? "0x") as `0x${string}`,
  journal: (process.env.NEXT_PUBLIC_JOURNAL_ADDR ?? "0x") as `0x${string}`,
  ledger: (process.env.NEXT_PUBLIC_LEDGER_ADDR ?? "0x") as `0x${string}`,
};
