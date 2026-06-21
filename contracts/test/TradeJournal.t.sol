// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IdentityRegistry} from "../src/IdentityRegistry.sol";
import {TradeJournal} from "../src/TradeJournal.sol";

contract TradeJournalTest is Test {
    IdentityRegistry identity;
    TradeJournal journal;
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");
    uint256 id;

    function setUp() public {
        identity = new IdentityRegistry();
        journal = new TradeJournal(identity);
        vm.prank(alice);
        id = identity.register("ipfs://a", address(0));
    }

    function test_owner_records_emits_and_counts() public {
        vm.expectEmit(true, true, true, true);
        emit TradeJournal.Recorded(id, 0, keccak256("ep1"), int256(120), uint64(block.timestamp));
        vm.prank(alice);
        journal.record(id, keccak256("ep1"), int256(120), uint64(block.timestamp));
        assertEq(journal.totalTrades(), 1);
        assertEq(journal.tradeCountByAgent(id), 1);
    }

    function test_non_owner_reverts() public {
        vm.prank(bob);
        vm.expectRevert(TradeJournal.NotAgentOwner.selector);
        journal.record(id, keccak256("ep1"), int256(1), uint64(block.timestamp));
    }

    function test_empty_hash_reverts() public {
        vm.prank(alice);
        vm.expectRevert(TradeJournal.EmptyHash.selector);
        journal.record(id, bytes32(0), int256(1), uint64(block.timestamp));
    }

    function test_record_for_unregistered_agent_reverts() public {
        vm.prank(alice);
        vm.expectRevert();  // identity.ownerOf(unminted) reverts (OZ ERC721NonexistentToken)
        journal.record(99, keccak256("ep1"), int256(1), uint64(block.timestamp));
    }

    function testFuzz_owner_records_increment_counts(bytes32 h, int256 pnl, uint64 ts, uint8 n) public {
        vm.assume(h != bytes32(0));
        ts = uint64(bound(uint256(ts), 0, block.timestamp));  // closedAt can't be in the future
        uint256 rounds = uint256(n) % 8 + 1;
        for (uint256 i = 0; i < rounds; i++) {
            vm.prank(alice);
            journal.record(id, h, pnl, ts);
        }
        assertEq(journal.totalTrades(), rounds);
        assertEq(journal.tradeCountByAgent(id), rounds);
    }
}
