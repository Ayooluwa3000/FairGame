#!/usr/bin/env python3
"""
frontrunner_bot.py

Demonstration bot that shows the practical difference between an
unprotected order-placement contract and a commit-reveal protected one,
on Monad Testnet.

  - UnprotectedArena: orders are visible on-chain the moment they're placed
    (via the OrderPlaced event), with direction+amount in the clear. This
    bot watches for those events and immediately copies the same direction
    with its own order, then executes — a simple copy-trading / back-running
    exploit that free-rides on the original trader's signal.

  - FairTradeArena: orders go through commit-reveal (OrderCommitted only
    exposes a hash; OrderRevealed only fires *after* execution is already
    possible), so there's no usable direction/amount signal to copy before
    it's too late to matter. The bot just logs that there's nothing to
    exploit here.

This script is intended to run against your own testnet deployment of these
two arena contracts, to illustrate why commit-reveal schemes protect traders
from this kind of copy-trading/front-running.

Setup
-----
1. pip install web3 python-dotenv
2. .env file with:

    RPC_URL=https://testnet-rpc.monad.xyz
    BOT_PRIVATE_KEY=0xyourbotprivatekeyhere
    FAIR_ARENA_ADDRESS=0x...
    UNPROTECTED_ARENA_ADDRESS=0x...

3. ABIs as JSON arrays in:
    abis/FairTradeArena.json
    abis/UnprotectedArena.json

4. Run: python frontrunner_bot.py
"""

import os
import json
import time
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
BOT_PRIVATE_KEY = os.getenv("BOT_PRIVATE_KEY")
FAIR_ARENA_ADDRESS = os.getenv("FAIR_ARENA_ADDRESS")
UNPROTECTED_ARENA_ADDRESS = os.getenv("UNPROTECTED_ARENA_ADDRESS")

ABI_DIR = Path(__file__).parent / "abis"
FAIR_ARENA_ABI_PATH = ABI_DIR / "FairTradeArena.json"
UNPROTECTED_ARENA_ABI_PATH = ABI_DIR / "UnprotectedArena.json"

POLL_INTERVAL_SECONDS = 1.5
TX_TIMEOUT_SECONDS = 120


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def require_env(value, name):
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def load_abi(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"ABI file not found at {path}")
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
            f"[WARN] Connected chain ID ({connected_chain_id}) != expected "
            f"({CHAIN_ID}). Continuing anyway."
        )
    return w3


def send_tx(w3, account, contract_function, label):
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
        print(f"    [{label}] tx sent: {tx_hash.hex()}")

        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=TX_TIMEOUT_SECONDS)
        if receipt.status == 1:
            print(f"    [{label}] confirmed in block {receipt.blockNumber}")
        else:
            print(f"    [{label}] FAILED (status=0) in block {receipt.blockNumber}")
        return receipt

    except ContractLogicError as e:
        print(f"    [{label}] contract reverted: {e}")
    except TimeExhausted:
        print(f"    [{label}] timed out waiting for confirmation")
    except Exception as e:
        print(f"    [{label}] unexpected error: {e}")
    return None


def get_order_id_from_receipt(receipt, contract, event_name="OrderPlaced"):
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


def print_pnl(contract, address, label):
    try:
        pnl = contract.functions.getTraderPnL(address).call()
        print(f"    >>> [{label}] PnL for bot ({address}): {pnl}")
        return pnl
    except ContractLogicError as e:
        print(f"    >>> [{label}] getTraderPnL reverted: {e}")
    except Exception as e:
        print(f"    >>> [{label}] error fetching PnL: {e}")
    return None


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    print("=== Copy-Trading / Front-Running Demo Bot starting ===")
    print("Illustrates: UnprotectedArena is exploitable, FairTradeArena is not.\n")

    require_env(BOT_PRIVATE_KEY, "BOT_PRIVATE_KEY")
    require_env(FAIR_ARENA_ADDRESS, "FAIR_ARENA_ADDRESS")
    require_env(UNPROTECTED_ARENA_ADDRESS, "UNPROTECTED_ARENA_ADDRESS")

    w3 = connect_web3()
    account = w3.eth.account.from_key(BOT_PRIVATE_KEY)
    print(f"Connected. Bot address: {account.address}")
    print(f"Latest block: {w3.eth.block_number}\n")

    fair_abi = load_abi(FAIR_ARENA_ABI_PATH)
    unprotected_abi = load_abi(UNPROTECTED_ARENA_ABI_PATH)

    fair_contract = w3.eth.contract(
        address=Web3.to_checksum_address(FAIR_ARENA_ADDRESS), abi=fair_abi
    )
    unprotected_contract = w3.eth.contract(
        address=Web3.to_checksum_address(UNPROTECTED_ARENA_ADDRESS), abi=unprotected_abi
    )

    # Track processed order IDs / event signatures so we don't double-copy
    seen_unprotected_orders = set()
    seen_fair_committed = set()
    seen_fair_revealed = set()

    copied_count = 0
    running_pnl = 0

    # Start polling from the current block so we only react to new activity
    last_checked_block = w3.eth.block_number

    while True:
        try:
            current_block = w3.eth.block_number

            if current_block >= last_checked_block:
                from_block = last_checked_block
                to_block = current_block

                # ---------------- UnprotectedArena: OrderPlaced ----------------
                try:
                    order_placed_filter = unprotected_contract.events.OrderPlaced.create_filter(
                        fromBlock=from_block, toBlock=to_block
                    )
                    new_orders = order_placed_filter.get_all_entries()
                except Exception as e:
                    print(f"[UnprotectedArena] error polling OrderPlaced events: {e}")
                    new_orders = []

                for evt in new_orders:
                    order_id = evt["args"].get("orderId")
                    trader = evt["args"].get("trader")
                    direction = evt["args"].get("direction")
                    amount = evt["args"].get("amount")

                    if order_id is None or order_id in seen_unprotected_orders:
                        continue
                    if trader is not None and trader.lower() == account.address.lower():
                        # don't copy our own order
                        seen_unprotected_orders.add(order_id)
                        continue

                    seen_unprotected_orders.add(order_id)
                    print(
                        f"\n[UnprotectedArena] Detected order #{order_id} "
                        f"from {trader}: direction={direction}, amount={amount}"
                    )
                    print(f"    -> Copying: placing matching order (direction={direction}, amount={amount})")

                    place_fn = unprotected_contract.functions.placeOrder(direction, amount)
                    place_receipt = send_tx(w3, account, place_fn, "UnprotectedArena.placeOrder")

                    if place_receipt is not None and place_receipt.status == 1:
                        my_order_id = get_order_id_from_receipt(place_receipt, unprotected_contract)
                        if my_order_id is None:
                            print(
                                "    [UnprotectedArena] WARNING: could not auto-detect our orderId "
                                "from event logs; defaulting to 0 — verify event name matches your ABI."
                            )
                            my_order_id = 0

                        execute_fn = unprotected_contract.functions.executeOrder(my_order_id)
                        send_tx(w3, account, execute_fn, "UnprotectedArena.executeOrder")

                        copied_count += 1
                        pnl = print_pnl(unprotected_contract, account.address, "UnprotectedArena")
                        pnl_str = pnl if pnl is not None else "unknown"
                        print(
                            f"Unprotected Arena: copied order #{order_id}, "
                            f"total copies so far: {copied_count}, profit so far: {pnl_str}"
                        )
                    else:
                        print(f"[UnprotectedArena] Failed to copy order #{order_id}, skipping.")

                # ---------------- FairTradeArena: OrderCommitted ----------------
                try:
                    committed_filter = fair_contract.events.OrderCommitted.create_filter(
                        fromBlock=from_block, toBlock=to_block
                    )
                    committed_events = committed_filter.get_all_entries()
                except Exception as e:
                    print(f"[FairTradeArena] error polling OrderCommitted events: {e}")
                    committed_events = []

                for evt in committed_events:
                    order_id = evt["args"].get("orderId")
                    if order_id is None or order_id in seen_fair_committed:
                        continue
                    seen_fair_committed.add(order_id)
                    print(
                        f"\nFair Arena: nothing to exploit — commit #{order_id} seen, "
                        f"only a hash is visible (no direction/amount available pre-reveal)."
                    )

                # ---------------- FairTradeArena: OrderRevealed ----------------
                try:
                    revealed_filter = fair_contract.events.OrderRevealed.create_filter(
                        fromBlock=from_block, toBlock=to_block
                    )
                    revealed_events = revealed_filter.get_all_entries()
                except Exception as e:
                    print(f"[FairTradeArena] error polling OrderRevealed events: {e}")
                    revealed_events = []

                for evt in revealed_events:
                    order_id = evt["args"].get("orderId")
                    if order_id is None or order_id in seen_fair_revealed:
                        continue
                    seen_fair_revealed.add(order_id)
                    direction = evt["args"].get("direction")
                    amount = evt["args"].get("amount")
                    print(
                        f"Fair Arena: nothing to exploit — reveal #{order_id} seen "
                        f"(direction={direction}, amount={amount}), but by now the order "
                        f"is already executable/executed, so there's no window to front-run it."
                    )

                # Report fair arena PnL periodically (should hover near zero
                # since the bot has no actionable edge there)
                if new_orders or committed_events or revealed_events:
                    print_pnl(fair_contract, account.address, "FairTradeArena")

                last_checked_block = current_block + 1

        except Exception as e:
            print(f"[MAIN LOOP] Unexpected error: {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
    except EnvironmentError as e:
        print(f"[CONFIG ERROR] {e}")
    except FileNotFoundError as e:
        print(f"[CONFIG ERROR] {e}")
    except ConnectionError as e:
        print(f"[CONNECTION ERROR] {e}")
