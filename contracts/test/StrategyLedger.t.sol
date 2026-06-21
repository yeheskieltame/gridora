// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {StrategyLedger} from "../src/StrategyLedger.sol";

contract StrategyLedgerTest is Test {
    StrategyLedger ledger;
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");
    bytes32 constant IID = keccak256("CAKE/USDT-1");

    function setUp() public {
        ledger = new StrategyLedger();
    }

    function test_commit_then_attest_flow() public {
        vm.prank(alice);
        ledger.commit(IID, keccak256("config"));
        assertEq(ledger.configHashOf(alice, IID), keccak256("config"));
        assertFalse(ledger.isAttested(alice, IID));
        vm.prank(alice);
        ledger.attest(IID, keccak256("outcome"));
        assertTrue(ledger.isAttested(alice, IID));
    }

    function test_cannot_commit_twice() public {
        vm.startPrank(alice);
        ledger.commit(IID, keccak256("config"));
        vm.expectRevert(StrategyLedger.AlreadyCommitted.selector);
        ledger.commit(IID, keccak256("config2"));
        vm.stopPrank();
    }

    function test_attest_requires_prior_commit() public {
        vm.prank(alice);
        vm.expectRevert(StrategyLedger.NotCommitted.selector);
        ledger.attest(IID, keccak256("outcome"));
    }

    function test_cannot_attest_twice() public {
        vm.startPrank(alice);
        ledger.commit(IID, keccak256("config"));
        ledger.attest(IID, keccak256("outcome"));
        vm.expectRevert(StrategyLedger.AlreadyAttested.selector); // outcome is write-once
        ledger.attest(IID, keccak256("outcome2"));
        vm.stopPrank();
    }

    /// @notice Squatting resistance: a different committer cannot block alice's instanceId.
    function test_namespaces_are_isolated_per_committer() public {
        vm.prank(bob);
        ledger.commit(IID, keccak256("bob-config"));   // bob squats the same id
        vm.prank(alice);
        ledger.commit(IID, keccak256("alice-config")); // alice commits independently — no revert
        assertEq(ledger.configHashOf(alice, IID), keccak256("alice-config"));
        assertEq(ledger.configHashOf(bob, IID), keccak256("bob-config"));
        vm.prank(bob);
        vm.expectRevert(StrategyLedger.NotCommitted.selector);
        ledger.attest(keccak256("other"), keccak256("x")); // bob can't attest an uncommitted id
    }

    function test_commit_is_timestamped_before_attest() public {
        vm.startPrank(alice);
        ledger.commit(IID, keccak256("config"));
        (,, uint64 committedAt, uint64 attestedAt) = ledger.records(alice, IID);
        assertGt(committedAt, 0);
        assertEq(attestedAt, 0);
        vm.warp(block.timestamp + 100);
        ledger.attest(IID, keccak256("outcome"));
        (,,, uint64 attestedAt2) = ledger.records(alice, IID);
        assertGt(attestedAt2, committedAt);
        vm.stopPrank();
    }

    function testFuzz_commit_attest(address agent, bytes32 iid, bytes32 cfg, bytes32 outcome) public {
        vm.assume(agent != address(0));
        vm.startPrank(agent);
        ledger.commit(iid, cfg);
        assertEq(ledger.configHashOf(agent, iid), cfg);
        ledger.attest(iid, outcome);
        assertTrue(ledger.isAttested(agent, iid));
        vm.expectRevert(StrategyLedger.AlreadyCommitted.selector);
        ledger.commit(iid, cfg);   // commit is write-once per (agent, iid)
        vm.stopPrank();
    }
}
