// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC721} from "@openzeppelin/contracts/token/ERC721/IERC721.sol";

/// @title TradeJournal — append-only settled-episode log, gated by the identity NFT.
/// @notice History lives entirely in indexed EVENTS (read off-chain via getLogs) — no
///         on-chain arrays or loops, so `record` is O(1) and storage can't grow unbounded.
contract TradeJournal {
    error NotAgentOwner();
    error EmptyHash();
    error BadTimestamp();

    event Recorded(uint256 indexed agentId, uint256 indexed index, bytes32 tradeHash, int256 pnlBps, uint64 closedAt);

    IERC721 public immutable identity;
    uint256 public totalTrades;
    mapping(uint256 => uint256) public tradeCountByAgent;

    constructor(IERC721 _identity) {
        identity = _identity;
    }

    /// @notice Anchor a settled episode for `agentId` (fills root + signed PnL). Caller
    ///         must hold that agent's identity NFT. `closedAt` is caller-supplied; a future
    ///         stamp is rejected so a close can't be dated ahead of the block. NOT gated by
    ///         StrategyLedger — the commit↔journal link is convention, verified off-chain.
    function record(uint256 agentId, bytes32 tradeHash, int256 pnlBps, uint64 closedAt) external {
        if (identity.ownerOf(agentId) != msg.sender) revert NotAgentOwner();
        if (tradeHash == bytes32(0)) revert EmptyHash();
        if (closedAt > block.timestamp) revert BadTimestamp();
        uint256 index = totalTrades;
        unchecked {
            totalTrades = index + 1;
            ++tradeCountByAgent[agentId];
        }
        emit Recorded(agentId, index, tradeHash, pnlBps, closedAt);
    }
}
