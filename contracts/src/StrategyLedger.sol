// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title StrategyLedger — commit the config hash BEFORE trading, attest the outcome
///         AFTER. Commitments are namespaced per committer, so no one can squat another
///         agent's instanceId. Both moments are timestamped → a verifiable pre-commitment.
contract StrategyLedger {
    struct Record {
        bytes32 configHash;
        bytes32 outcomeHash;
        uint64 committedAt;
        uint64 attestedAt;
    }

    error AlreadyCommitted();
    error NotCommitted();
    error AlreadyAttested();

    // committer => instanceId => Record
    mapping(address => mapping(bytes32 => Record)) public records;

    event Committed(address indexed agent, bytes32 indexed instanceId, bytes32 configHash, uint64 at);
    event Attested(address indexed agent, bytes32 indexed instanceId, bytes32 outcomeHash, uint64 at);

    function commit(bytes32 instanceId, bytes32 configHash) external {
        Record storage r = records[msg.sender][instanceId];
        if (r.committedAt != 0) revert AlreadyCommitted();
        r.configHash = configHash;
        r.committedAt = uint64(block.timestamp);
        emit Committed(msg.sender, instanceId, configHash, r.committedAt);
    }

    function attest(bytes32 instanceId, bytes32 outcomeHash) external {
        Record storage r = records[msg.sender][instanceId];
        if (r.committedAt == 0) revert NotCommitted();
        if (r.attestedAt != 0) revert AlreadyAttested();   // write-once: an outcome can't be rewritten after the fact
        r.outcomeHash = outcomeHash;
        r.attestedAt = uint64(block.timestamp);
        emit Attested(msg.sender, instanceId, outcomeHash, r.attestedAt);
    }

    function configHashOf(address agent, bytes32 instanceId) external view returns (bytes32) {
        return records[agent][instanceId].configHash;
    }

    function isAttested(address agent, bytes32 instanceId) external view returns (bool) {
        return records[agent][instanceId].attestedAt != 0;
    }
}
