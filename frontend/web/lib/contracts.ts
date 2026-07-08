// Deployed addresses (fill from `forge script` output).
export const ADDR = {
  identity: (process.env.NEXT_PUBLIC_IDENTITY_ADDR ?? "0x") as `0x${string}`,
  journal: (process.env.NEXT_PUBLIC_JOURNAL_ADDR ?? "0x") as `0x${string}`,
  ledger: (process.env.NEXT_PUBLIC_LEDGER_ADDR ?? "0x") as `0x${string}`,
};

// The win, and the on-chain facts that back it. These are fixed BSC mainnet history
// (the competition ran on mainnet), so they link to bscscan.com directly rather than the
// env-dependent explorer — the result is the same no matter what chain a dev points at.
export const WIN = {
  place: "3rd place",
  event: "BNB Hack: AI Trading Agent Edition",
  by: "CoinMarketCap × Trust Wallet × BNB Chain",
} as const;

export const COMPETITION = {
  registry: "0x212c61b9b72c95d95bf29cf032f5e5635629aed5" as `0x${string}`,
  registrationTx:
    "0x11137b00830122e2949620920e6538ccf7c3cb915706cf55e8231f7ea253f692" as `0x${string}`,
  agentId: "140004",
  identityTx:
    "0x8b90829ef0a6854deeff31b212e3fb49f6e3262ebd740d71c0d405400015fdb9" as `0x${string}`,
  scan: "https://bscscan.com",
} as const;
