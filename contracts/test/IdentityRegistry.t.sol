// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IdentityRegistry} from "../src/IdentityRegistry.sol";

contract IdentityRegistryTest is Test {
    IdentityRegistry identity;
    address alice = makeAddr("alice");
    address bob = makeAddr("bob");

    function setUp() public {
        identity = new IdentityRegistry();
    }

    function _register(address who) internal returns (uint256 id) {
        vm.prank(who);
        id = identity.register("ipfs://agent.json", address(0));
    }

    function test_register_mints_soulbound_nft() public {
        uint256 id = _register(alice);
        assertEq(id, 1);
        assertEq(identity.ownerOf(id), alice);
        assertEq(identity.balanceOf(alice), 1);
        assertEq(identity.agentOf(alice), id);
        assertEq(identity.agentWallet(id), alice);   // wallet 0 -> defaults to sender
        assertEq(identity.tokenURI(id), "ipfs://agent.json");
        assertEq(identity.totalAgents(), 1);
    }

    function test_one_identity_per_owner() public {
        _register(alice);
        vm.prank(alice);
        vm.expectRevert(IdentityRegistry.AlreadyRegistered.selector);
        identity.register("ipfs://b", address(0));
    }

    function test_empty_uri_reverts() public {
        vm.prank(alice);
        vm.expectRevert(IdentityRegistry.EmptyAgentURI.selector);
        identity.register("", address(0));
    }

    function test_soulbound_transfer_reverts() public {
        uint256 id = _register(alice);
        vm.prank(alice);
        vm.expectRevert(IdentityRegistry.NonTransferable.selector);
        identity.transferFrom(alice, bob, id);
    }

    function test_only_owner_updates_uri_and_wallet() public {
        uint256 id = _register(alice);
        vm.prank(bob);
        vm.expectRevert(IdentityRegistry.NotAgentOwner.selector);
        identity.setAgentURI(id, "ipfs://hacked");
        vm.prank(alice);
        identity.setAgentWallet(id, bob);
        assertEq(identity.agentWallet(id), bob);
    }

    function test_agent_info() public {
        uint256 id = _register(alice);
        (address owner, address wallet, string memory uri) = identity.agentInfo(id);
        assertEq(owner, alice);
        assertEq(wallet, alice);
        assertEq(uri, "ipfs://agent.json");
    }

    /// @notice Soulbound across EVERY transfer path, including an approved operator.
    function test_soulbound_blocks_all_transfer_paths() public {
        uint256 id = _register(alice);
        vm.startPrank(alice);
        vm.expectRevert(IdentityRegistry.NonTransferable.selector);
        identity.transferFrom(alice, bob, id);
        vm.expectRevert(IdentityRegistry.NonTransferable.selector);
        identity.safeTransferFrom(alice, bob, id);
        vm.expectRevert(IdentityRegistry.NonTransferable.selector);
        identity.safeTransferFrom(alice, bob, id, "");
        identity.approve(bob, id);   // approval itself succeeds but is inert
        vm.stopPrank();
        vm.prank(bob);
        vm.expectRevert(IdentityRegistry.NonTransferable.selector);
        identity.transferFrom(alice, bob, id);  // approved operator still blocked
    }

    function testFuzz_register(address who, address wallet) public {
        vm.assume(who != address(0) && who.code.length == 0);
        vm.prank(who);
        uint256 id = identity.register("ipfs://x", wallet);
        assertEq(identity.ownerOf(id), who);
        assertEq(identity.agentWallet(id), wallet == address(0) ? who : wallet);
        assertEq(identity.agentOf(who), id);
        vm.prank(who);
        vm.expectRevert(IdentityRegistry.AlreadyRegistered.selector);
        identity.register("ipfs://y", wallet);
    }
}
