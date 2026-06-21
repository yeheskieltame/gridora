// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC721} from "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import {ERC721URIStorage} from "@openzeppelin/contracts/token/ERC721/extensions/ERC721URIStorage.sol";

/// @title IdentityRegistry — ERC-8004 agent identity as a SOULBOUND ERC-721 (OZ).
/// @notice One identity NFT per owner; `tokenURI` is the registration file, `agentWallet`
///         is the TWAK trading signer. Non-transferable (an identity isn't a tradable asset).
contract IdentityRegistry is ERC721URIStorage {
    error AlreadyRegistered();
    error EmptyAgentURI();
    error NotAgentOwner();
    error NonTransferable();

    mapping(address => uint256) public agentOf;     // owner => agentId (0 = none)
    mapping(uint256 => address) public agentWallet; // TWAK trading signer
    uint256 public nextId = 1;

    event AgentWalletUpdated(uint256 indexed agentId, address newWallet);

    constructor() ERC721("Gridora Agent", "GRID-AGENT") {}

    /// @param uri registration-file URI (https/ipfs/data:); @param wallet 0 => msg.sender.
    function register(string calldata uri, address wallet) external returns (uint256 agentId) {
        if (agentOf[msg.sender] != 0) revert AlreadyRegistered();
        if (bytes(uri).length == 0) revert EmptyAgentURI();
        unchecked { agentId = nextId++; }
        agentOf[msg.sender] = agentId;
        agentWallet[agentId] = wallet == address(0) ? msg.sender : wallet;
        _safeMint(msg.sender, agentId);
        _setTokenURI(agentId, uri);
        emit AgentWalletUpdated(agentId, agentWallet[agentId]);
    }

    function setAgentURI(uint256 agentId, string calldata uri) external {
        if (ownerOf(agentId) != msg.sender) revert NotAgentOwner();
        if (bytes(uri).length == 0) revert EmptyAgentURI();
        _setTokenURI(agentId, uri);
    }

    function setAgentWallet(uint256 agentId, address wallet) external {
        if (ownerOf(agentId) != msg.sender) revert NotAgentOwner();
        address w = wallet == address(0) ? msg.sender : wallet;  // mirror register(): 0 => owner, never blank
        agentWallet[agentId] = w;
        emit AgentWalletUpdated(agentId, w);
    }

    /// @notice One-call view for the verifier's AgentCard. Reverts if `agentId` unminted.
    function agentInfo(uint256 agentId) external view returns (address owner, address wallet, string memory uri) {
        return (ownerOf(agentId), agentWallet[agentId], tokenURI(agentId));
    }

    function totalAgents() external view returns (uint256) {
        unchecked { return nextId - 1; }
    }

    /// @dev Soulbound: permit mint (from == 0), block every transfer.
    function _update(address to, uint256 tokenId, address auth) internal override returns (address) {
        if (_ownerOf(tokenId) != address(0) && to != address(0)) revert NonTransferable();
        return super._update(to, tokenId, auth);
    }
}
