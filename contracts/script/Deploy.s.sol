// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Script, console} from "forge-std/Script.sol";
import {IdentityRegistry} from "../src/IdentityRegistry.sol";
import {TradeJournal} from "../src/TradeJournal.sol";
import {StrategyLedger} from "../src/StrategyLedger.sol";

/// @dev forge script script/Deploy.s.sol --rpc-url <bsc|bsc_test> --broadcast
///      Copy the printed addresses into backend/.env (GRIDORA_*_ADDR) and frontend/web/lib/contracts.ts
contract Deploy is Script {
    function run() external {
        require(block.chainid == 56 || block.chainid == 97, "Deploy: not a BSC chain (expect 56 or 97)");
        vm.startBroadcast();
        IdentityRegistry identity = new IdentityRegistry();
        TradeJournal journal = new TradeJournal(identity);
        StrategyLedger ledger = new StrategyLedger();
        vm.stopBroadcast();
        console.log("IdentityRegistry", address(identity));
        console.log("TradeJournal", address(journal));
        console.log("StrategyLedger", address(ledger));
    }
}
