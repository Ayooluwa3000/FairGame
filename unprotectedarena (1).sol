// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/// @title UnprotectedArena
/// @notice Vulnerable trading arena — orders are fully visible the moment
///         they are placed, which is exactly what makes front-running
///         possible. This is intentional, for demo purposes.
contract UnprotectedArena {
    struct Order {
        address trader;
        int8 direction;      // +1 buy, -1 sell
        uint256 amount;
        uint256 entryPrice;
        bool executed;
        int256 pnlDelta;
    }

    uint256 public constant IMPACT = 1;
    uint256 public constant SCALE = 100;

    uint256 public currentPrice = 1000;
    uint256 public nextOrderId;

    mapping(uint256 => Order) public orders;
    mapping(address => int256) public traderPnL;

    event OrderPlaced(
        uint256 indexed orderId,
        address indexed trader,
        int8 direction,
        uint256 amount,
        uint256 entryPrice
    );

    event OrderExecuted(
        uint256 indexed orderId,
        int256 pnlDelta,
        uint256 currentPrice
    );

    /// @notice Places an order. entryPrice is locked in and emitted
    ///         immediately — fully visible on-chain right away.
    function placeOrder(int8 direction, uint256 amount) external returns (uint256 orderId) {
        require(direction == 1 || direction == -1, "direction must be +1 or -1");
        require(amount > 0, "amount must be > 0");

        orderId = nextOrderId++;

        orders[orderId] = Order({
            trader: msg.sender,
            direction: direction,
            amount: amount,
            entryPrice: currentPrice,
            executed: false,
            pnlDelta: 0
        });

        emit OrderPlaced(orderId, msg.sender, direction, amount, currentPrice);
    }

    function executeOrder(uint256 orderId) external {
        Order storage o = orders[orderId];
        require(o.trader != address(0), "order does not exist");
        require(msg.sender == o.trader, "not order owner");
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
