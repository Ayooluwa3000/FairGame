# Handoff — Contracts + Python side, status as of now

Per the shared contract's Communication Rule (§6): two things changed from the
original spec while building. Flagging both here — nothing else deviates.

---

## ✅ Deployed (Monad testnet, chain 10143)

| Contract | Address |
|---|---|
| FairTradeArena | `0xd78DCd80c693Ba9dB9f92ba81Bd407a9E01aB528` |
| UnprotectedArena | `0x842c522d7F4A13CC3Dc406Fec28A397f97D819F3` |

Deployed via Remix + MetaMask. Verify both on the explorer before relying on
them: https://testnet.monadexplorer.com/ — search each address and confirm
a "Contract Creation" transaction, not an empty account.

**Get the real ABI from Remix**, not the hand-written stopgap in
`python/abi/*.json` — in Remix's Solidity Compiler tab, after compiling,
copy the ABI shown for each contract. That's the ground truth; the JSON
files currently in the repo were written by hand before compilation and
should be treated as provisional.

## ✅ Wallets funded

| Role | Address |
|---|---|
| Trading agent | `0xff7e9324F662A2bBf95aa01B720714DceE8dD93B` |
| Front-runner bot | `0x4cc38F2799BF9Ab52e7643B62fa1C35EA4AABce3` |

Both funded from the faucet, both keys already sitting in `python/.env`
(not committed anywhere — keep it that way, testnet funds only).

---

## ⚠️ Two deviations from the original spec — read before testing

**1. `executeOrder` is now restricted to the order's own trader, in BOTH
contracts.**

Original spec left it permissionless. A review caught that this means
*anyone* could execute someone else's order at a moment of their choosing —
which breaks the fairness story the demo is trying to tell, since it lets a
third party time the price impact instead of the trader who placed the
order. Fix was a one-line `require(msg.sender == o.trader)` in each
contract's `executeOrder`. Function signature and events are unchanged, so
nothing downstream should break — but if anything in your code calls
`executeOrder` on an order it doesn't own, it will now revert with
`"not order owner"` where it previously wouldn't have.

**2. Trade size bumped from 10 to 500 in the Python scripts.**

With `IMPACT=1` / `SCALE=100` as specified, the price-impact formula
(`amount * IMPACT / SCALE`) truncates to **0** for any `amount < 100`
under Solidity's integer division. At `amount=10` the price would never
move — silently breaking the whole demo (momentum strategy has no signal,
front-running has nothing to prove). `trading_agent.py` and
`frontrunner_bot.py` both use `500` now (impact of 5 per trade). If your
piece hardcodes or assumes an amount below 100 anywhere, bump it.

**Not changed, flagging as a known limitation only:** the commit hash
(`keccak256(direction, amount, secret)`) isn't bound to `msg.sender` or
`orderId`. In theory someone watching the mempool could copy a commit hash.
Fixing it means changing the hash formula everyone's building against —
left as-is given the time box. Worth a 30-second team gut-check on whether
it matters for a demo where only our own two wallets are trading.

---

## What's left

**For the GRC teammate (§5 checklist):**
1. Secrecy check — commit, try to read direction/amount pre-reveal
2. Hash mismatch check — reveal with wrong secret/direction, confirm revert
3. Double-reveal check — reveal same order twice, confirm second reverts
4. Front-run confirmation — watch `UnprotectedArena` txs on the explorer
   during a live run, confirm the bot's copy-order appears right after
5. PnL sanity check — `getTraderPnL` for the bot should stay ~0 on
   FairTradeArena, clearly positive on UnprotectedArena

Note: with the `executeOrder` ownership fix, check #4/#5 will look slightly
different than the original spec implied — the bot's "front-run" on
UnprotectedArena is now always its *own* copied order (place → execute),
never someone else's order executed early. Still proves the same point
(bot profits by racing to copy, undefended arena has no protection against
it) — just flagging so the GRC checks are written against what's actually
running.

**To run the live demo:**
```bash
cd python
source venv/bin/activate      # or set up per requirements.txt if not yet done
python trading_agent.py        # terminal 1 — does the actual trading
python frontrunner_bot.py      # terminal 2 — watches + copies
```
Console output from both + the block explorer is the demo, per the §0 scope
cut (no dashboard).

**Still open:**
- Confirm §1 network values against `docs.monad.xyz` one more time before
  the actual pitch — testnets can move.
- Decide on the commit-hash binding limitation above, if there's time.
- Swap the provisional ABI files for the real compiled ones from Remix.
