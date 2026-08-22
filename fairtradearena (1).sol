// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title FairTradeArena
/// @notice Protected trading arena using commit-reveal so order direction
///         and size are hidden until reveal, preventing front-running.
contract FairTradeArena {
    struct Order {
        address trader;
        bytes32 commitHash;
        bool revealed;
        bool executed;
        int8 direction;      // +1 buy, -1 sell
        uint256 amount;
        uint256 entryPrice;  // currentPrice at time of REVEAL
        int256 pnlDelta;
    }

    uint256 public constant IMPACT = 1;
    uint256 public constant SCALE = 100;

    uint256 public currentPrice = 1000;
    uint256 public nextOrderId;

    mapping(uint256 => Order) public orders;
    mapping(address => int256) public traderPnL;

    event OrderCommitted(uint256 indexed orderId, address indexed trader, bytes32 commitHash);
    event OrderRevealed(uint256 indexed orderId, int8 direction, uint256 amount, uint256 entryPrice);
    event OrderExecuted(uint256 indexed orderId, int256 pnlDelta, uint256 currentPrice);

    function commitOrder(bytes32 commitHash) external returns (uint256 orderId) {
        orderId = nextOrderId++;

        orders[orderId] = Order({
            trader: msg.sender,
            commitHash: commitHash,
            revealed: false,
            executed: false,
            direction: 0,
            amount: 0,
            entryPrice: 0,
            pnlDelta: 0
        });

        emit OrderCommitted(orderId, msg.sender, commitHash);
    }

    function revealOrder(uint256 orderId, int8 direction, uint256 amount, bytes32 secret) external {
        Order storage o = orders[orderId];
        require(o.trader == msg.sender, "not order owner");
        require(!o.revealed, "already revealed");
        require(direction == 1 || direction == -1, "direction must be +1 or -1");

        bytes32 computedHash = keccak256(abi.encodePacked(direction, amount, secret));
        require(computedHash == o.commitHash, "hash mismatch");

        o.direction = direction;
        o.amount = amount;
        o.entryPrice = currentPrice;
        o.revealed = true;

        emit OrderRevealed(orderId, direction, amount, currentPrice);
    }

    function executeOrder(uint256 orderId) external {
        Order storage o = orders[orderId];
        require(o.trader != address(0), "order does not exist");
        require(o.revealed, "not revealed yet");
        require(!o.executed, "already executed");

        int256 pnlDelta = int256(o.direction) * int256(o.amount) *
            (int256(currentPrice) - int256(o.entryPrice));

        o.pnlDelta = pnlDelta;
        o.executed = true;
        traderPnL[o.trader] += pnlDelta;

        // apply price impact
        int256 newPrice = int256(currentPrice) +
            (int256(o.direction) * int256(o.amount) * int256(IMPACT)) / int256(SCALE);
        require(newPrice >= 0, "price cannot go negative");
        currentPrice = uint256(newPrice);

        emit OrderExecuted(orderId, pnlDelta, currentPrice);
    }

    function getTraderPnL(address trader) external view returns (int256 totalPnL) {
        return traderPnL[trader];
    }
}
