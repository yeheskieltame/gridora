// Read ABIs for the verifier — match contracts/src/*.sol.
// IdentityRegistry is an OpenZeppelin ERC-721 (soulbound); TradeJournal history lives
// in the `Recorded` event (read via getContractEvents/getLogs — no on-chain arrays).

export const tradeJournalAbi = [
  { type: "function", name: "totalTrades", stateMutability: "view", inputs: [], outputs: [{ type: "uint256" }] },
  { type: "function", name: "tradeCountByAgent", stateMutability: "view",
    inputs: [{ type: "uint256" }], outputs: [{ type: "uint256" }] },
  { type: "event", name: "Recorded", inputs: [
    { name: "agentId", type: "uint256", indexed: true },
    { name: "index", type: "uint256", indexed: true },
    { name: "tradeHash", type: "bytes32", indexed: false },
    { name: "pnlBps", type: "int256", indexed: false },
    { name: "closedAt", type: "uint64", indexed: false },
  ] },
] as const;

export const identityAbi = [
  { type: "function", name: "totalAgents", stateMutability: "view", inputs: [], outputs: [{ type: "uint256" }] },
  { type: "function", name: "ownerOf", stateMutability: "view",
    inputs: [{ type: "uint256" }], outputs: [{ type: "address" }] },
  { type: "function", name: "tokenURI", stateMutability: "view",
    inputs: [{ type: "uint256" }], outputs: [{ type: "string" }] },
  { type: "function", name: "agentWallet", stateMutability: "view",
    inputs: [{ type: "uint256" }], outputs: [{ type: "address" }] },
  { type: "function", name: "agentOf", stateMutability: "view",
    inputs: [{ type: "address" }], outputs: [{ type: "uint256" }] },
  { type: "function", name: "agentInfo", stateMutability: "view",
    inputs: [{ type: "uint256" }],
    outputs: [{ type: "address" }, { type: "address" }, { type: "string" }] },
] as const;

// StrategyLedger is namespaced per committer: records[agent][instanceId].
export const ledgerAbi = [
  { type: "function", name: "records", stateMutability: "view",
    inputs: [{ type: "address" }, { type: "bytes32" }],
    outputs: [
      { name: "configHash", type: "bytes32" },
      { name: "outcomeHash", type: "bytes32" },
      { name: "committedAt", type: "uint64" },
      { name: "attestedAt", type: "uint64" },
    ] },
  { type: "function", name: "configHashOf", stateMutability: "view",
    inputs: [{ type: "address" }, { type: "bytes32" }], outputs: [{ type: "bytes32" }] },
  { type: "function", name: "isAttested", stateMutability: "view",
    inputs: [{ type: "address" }, { type: "bytes32" }], outputs: [{ type: "bool" }] },
  { type: "event", name: "Committed", inputs: [
    { name: "agent", type: "address", indexed: true },
    { name: "instanceId", type: "bytes32", indexed: true },
    { name: "configHash", type: "bytes32", indexed: false },
    { name: "at", type: "uint64", indexed: false },
  ] },
  { type: "event", name: "Attested", inputs: [
    { name: "agent", type: "address", indexed: true },
    { name: "instanceId", type: "bytes32", indexed: true },
    { name: "outcomeHash", type: "bytes32", indexed: false },
    { name: "at", type: "uint64", indexed: false },
  ] },
] as const;
