#!/usr/bin/env python3
"""
trading_agent.py

A simple momentum-based trading bot that interacts with two arena contracts
on Monad Testnet:
  - FairTradeArena     : uses a commit-reveal scheme (commitOrder -> revealOrder -> executeOrder)
  - UnprotectedArena   : places orders directly (placeOrder -> executeOrder)

After each full cycle it prints each contract's reported PnL for the agent's
own address.

Setup
-----
1. pip install web3 python-dotenv
2. Create a `.env` file next to this script with:

    RPC_URL=https://testnet-rpc.monad.xyz
    AGENT_PRIVATE_KEY=0xyourprivatekeyhere
    FAIR_ARENA_ADDRESS=0x...
    UNPROTECTED_ARENA_ADDRESS=0x...

3. Place the contract ABIs as JSON files in an `abis/` folder next to this
   script:
    abis/FairTradeArena.json
    abis/UnprotectedArena.json

   (Each file should just be the ABI JSON array, e.g. what you'd get from
   a Hardhat/Foundry build artifact's "abi" field.)

4. Run: python trading_agent.py
"""

import os
import json
import time
import random
import secrets
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

load_dotenv()

CHAIN_ID = 10143  # Monad Testnet

RPC_URL = os.getenv("RPC_URL")
AGENT_PRIVATE_KEY = os.getenv("AGENT_PRIVATE_KEY")
FAIR_ARENA_ADDRESS = os.getenv("FAIR_ARENA_ADDRESS")
UNPROTECTED_ARENA_ADDRESS = os.getenv("UNPROTECTED_ARENA_ADDRESS")

ABI_DIR = Path(__file__).parent / "abis"
FAIR_ARENA_ABI_PATH = ABI_DIR / "FairTradeArena.json"
UNPROTECTED_ARENA_ABI_PATH = ABI_DIR / "UnprotectedArena.json"

TRADE_AMOUNT = 100          # fixed trade size
LOOP_MIN_SECONDS = 10
LOOP_MAX_SECONDS = 15
REVEAL_DELAY_SECONDS = 5
PRICE_HISTORY_LEN = 2        # how many past prices we look at for momentum
TX_TIMEOUT_SECONDS = 120


# --------------------------------------------------------------------------
# Helpers: environment / setup
# --------------------------------------------------------------------------

def require_env(value, name):
    if not value:
        raise EnvironmentError(
            f"Missing required environment variable: {name}. "
            f"Please set it in your .env file."
        )
    return value


def load_abi(path: Path):
    if not path.exists():
        raise FileNotFoundError(
            f"ABI file not found at {path}. "
            f"Place the contract ABI JSON there before running."
        )
    with open(path, "r") as f:
        return json.load(f)


def connect_web3():
    require_env(RPC_URL, "RPC_URL")
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to RPC at {RPC_URL}")

    connected_chain_id = w3.eth.chain_id
    if connected_chain_id != CHAIN_ID:
        print(
            f"[WARN] Connected chain ID ({connected_chain_id}) does not match "
            f"expected Monad Testnet chain ID ({CHAIN_ID}). Continuing anyway."
        )
    return w3


# --------------------------------------------------------------------------
# Simple local/simulated price feed + momentum strategy
# --------------------------------------------------------------------------

class PriceFeed:
    """A simple simulated local price series (random walk)."""

    def __init__(self, start_price=1000.0):
        self.price = start_price
        self.history = [start_price]

    def tick(self):
        # simulate a small random price move (+/- up to 2%)
        pct_move = random.uniform(-0.02, 0.02)
        self.price = max(0.01, self.price * (1 + pct_move))
        self.history.append(self.price)
        # keep history bounded
        if len(self.history) > 50:
            self.history.pop(0)
        return self.price


def compute_direction(history):
    """
    Momentum strategy: compare the most recent price to the price
    PRICE_HISTORY_LEN checks ago.
      - price up   -> direction = +1 (buy)
      - price down -> direction = -1 (sell)
      - equal/insufficient data -> direction = +1 by default
    """
    if len(history) <= PRICE_HISTORY_LEN:
        return 1  # not enough data yet, default to buy

    recent = history[-1]
    past = history[-(PRICE_HISTORY_LEN + 1)]

    if recent > past:
        return 1
    elif recent < past:
        return -1
    else:
        return 1


# --------------------------------------------------------------------------
# Transaction helper
# --------------------------------------------------------------------------

def send_tx(w3, account, contract_function, label):
    """
    Build, sign, send, and wait for a contract transaction.
    Returns the transaction receipt, or None on failure.
    """
    try:
        nonce = w3.eth.get_transaction_count(account.address, "pending")
        tx = contract_function.build_transaction(
            {
                "from": account.address,
                "nonce": nonce,
                "chainId": CHAIN_ID,
                "gas": 500_000,
                "gasPrice": w3.eth.gas_price,
            }
        )
        signed_tx = account.sign_transaction(tx)
        tx_hash = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
        print(f"  [{label}] tx sent: {tx_hash.hex()}")

        receipt = w3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=TX_TIMEOUT_SECONDS
        )
        if receipt.status == 1:
            print(f"  [{label}] confirmed in block {receipt.blockNumber}")
        else:
            print(f"  [{label}] FAILED (status=0) in block {receipt.blockNumber}")
        return receipt

    except ContractLogicError as e:
        print(f"  [{label}] contract reverted: {e}")
    except TimeExhausted:
        print(f"  [{label}] timed out waiting for confirmation")
    except Exception as e:
        print(f"  [{label}] unexpected error: {e}")
    return None


def get_order_id_from_receipt(receipt, contract, event_name="OrderPlaced"):
    """
    Try to pull an orderId out of an emitted event. Falls back to None
    if the event isn't found / named differently — caller should handle
    that case (e.g. by tracking order IDs itself, or adjusting event_name
    to match the actual contract ABI).
    """
    if receipt is None:
        return None
    try:
        event = getattr(contract.events, event_name)()
        logs = event.process_receipt(receipt)
        if logs:
            return logs[0]["args"].get("orderId")
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# Arena interaction: FairTradeArena (commit-reveal)
# --------------------------------------------------------------------------

def run_fair_arena_cycle(w3, account, contract, direction, amount):
    print("[FairTradeArena] Starting commit-reveal cycle...")

    secret = secrets.token_bytes(32)
    commit_hash = Web3.solidity_keccak(
        ["int8", "uint256", "bytes32"], [direction, amount, secret]
    )

    print(f"  direction={direction} amount={amount} secret={secret.hex()}")
    print(f"  commit_hash={commit_hash.hex()}")

    commit_fn = contract.functions.commitOrder(commit_hash)
    commit_receipt = send_tx(w3, account, commit_fn, "FairTradeArena.commitOrder")
    if commit_receipt is None or commit_receipt.status != 1:
        print("[FairTradeArena] Commit failed, aborting this cycle.")
        return

    order_id = get_order_id_from_receipt(commit_receipt, contract)
    if order_id is None:
        print(
            "[FairTradeArena] WARNING: could not auto-detect orderId from event logs. "
            "Defaulting to 0 — update get_order_id_from_receipt()/event_name to match "
            "your contract's actual event if this is wrong."
        )
        order_id = 0
    else:
        print(f"  order_id={order_id}")

    print(f"  Waiting {REVEAL_DELAY_SECONDS}s before revealing...")
    time.sleep(REVEAL_DELAY_SECONDS)

    reveal_fn = contract.functions.revealOrder(order_id, direction, amount, secret)
    reveal_receipt = send_tx(w3, account, reveal_fn, "FairTradeArena.revealOrder")
    if reveal_receipt is None or reveal_receipt.status != 1:
        print("[FairTradeArena] Reveal failed, aborting this cycle.")
        return

    execute_fn = contract.functions.executeOrder(order_id)
    send_tx(w3, account, execute_fn, "FairTradeArena.executeOrder")


# --------------------------------------------------------------------------
# Arena interaction: UnprotectedArena (direct placement)
# --------------------------------------------------------------------------

def run_unprotected_arena_cycle(w3, account, contract, direction, amount):
    print("[UnprotectedArena] Starting direct order cycle...")
    print(f"  direction={direction} amount={amount}")

    place_fn = contract.functions.placeOrder(direction, amount)
    place_receipt = send_tx(w3, account, place_fn, "UnprotectedArena.placeOrder")
    if place_receipt is None or place_receipt.status != 1:
        print("[UnprotectedArena] placeOrder failed, aborting this cycle.")
        return

    order_id = get_order_id_from_receipt(place_receipt, contract)
    if order_id is None:
        print(
            "[UnprotectedArena] WARNING: could not auto-detect orderId from event logs. "
            "Defaulting to 0 — update get_order_id_from_receipt()/event_name to match "
            "your contract's actual event if this is wrong."
        )
        order_id = 0
    else:
        print(f"  order_id={order_id}")

    execute_fn = contract.functions.executeOrder(order_id)
    send_tx(w3, account, execute_fn, "UnprotectedArena.executeOrder")


# --------------------------------------------------------------------------
# PnL reporting
# --------------------------------------------------------------------------

def print_pnl(contract, my_address, label):
    try:
        pnl = contract.functions.getTraderPnL(my_address).call()
        print(f"  >>> [{label}] PnL for {my_address}: {pnl}")
    except ContractLogicError as e:
        print(f"  >>> [{label}] getTraderPnL reverted: {e}")
    except Exception as e:
        print(f"  >>> [{label}] error fetching PnL: {e}")


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def main():
    print("=== Momentum Trading Agent starting ===")

    require_env(AGENT_PRIVATE_KEY, "AGENT_PRIVATE_KEY")
    require_env(FAIR_ARENA_ADDRESS, "FAIR_ARENA_ADDRESS")
    require_env(UNPROTECTED_ARENA_ADDRESS, "UNPROTECTED_ARENA_ADDRESS")

    w3 = connect_web3()
    account = w3.eth.account.from_key(AGENT_PRIVATE_KEY)
    print(f"Connected. Agent address: {account.address}")
    print(f"Latest block: {w3.eth.block_number}")

    fair_abi = load_abi(FAIR_ARENA_ABI_PATH)
    unprotected_abi = load_abi(UNPROTECTED_ARENA_ABI_PATH)

    fair_contract = w3.eth.contract(
        address=Web3.to_checksum_address(FAIR_ARENA_ADDRESS), abi=fair_abi
    )
    unprotected_contract = w3.eth.contract(
        address=Web3.to_checksum_address(UNPROTECTED_ARENA_ADDRESS), abi=unprotected_abi
    )

    price_feed = PriceFeed()

    cycle = 0
    while True:
        cycle += 1
        print(f"\n===== Cycle {cycle} =====")

        try:
            price = price_feed.tick()
            direction = compute_direction(price_feed.history)
            side = "BUY (+1)" if direction == 1 else "SELL (-1)"
            print(f"Price feed: {price:.4f} | history={['%.2f' % p for p in price_feed.history[-5:]]}")
            print(f"Momentum decision: {side}, amount={TRADE_AMOUNT}")

            # --- FairTradeArena (commit-reveal) ---
            try:
                run_fair_arena_cycle(w3, account, fair_contract, direction, TRADE_AMOUNT)
            except Exception as e:
                print(f"[FairTradeArena] cycle error: {e}")

            # --- UnprotectedArena (direct) ---
            try:
                run_unprotected_arena_cycle(
                    w3, account, unprotected_contract, direction, TRADE_AMOUNT
                )
            except Exception as e:
                print(f"[UnprotectedArena] cycle error: {e}")

            # --- PnL report ---
            print("PnL report:")
            print_pnl(fair_contract, account.address, "FairTradeArena")
            print_pnl(unprotected_contract, account.address, "UnprotectedArena")

        except Exception as e:
            print(f"[MAIN LOOP] Unexpected error this cycle: {e}")

        sleep_for = random.uniform(LOOP_MIN_SECONDS, LOOP_MAX_SECONDS)
        print(f"Sleeping {sleep_for:.1f}s before next cycle...")
        time.sleep(sleep_for)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAgent stopped by user.")
    except EnvironmentError as e:
        print(f"[CONFIG ERROR] {e}")
    except FileNotFoundError as e:
        print(f"[CONFIG ERROR] {e}")
    except ConnectionError as e:
        print(f"[CONNECTION ERROR] {e}")
