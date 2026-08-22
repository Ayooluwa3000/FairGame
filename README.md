Provably Fair Trading Arena
Built at Monad Blitz

Two smart contracts on Monad Testnet demonstrate a working defense against front-running / MEV (Maximal Extractable Value) — one of the most well-known unsolved problems in blockchain — using a commit-reveal pattern.

The Problem

On public blockchains, submitted trades sit visibly in the mempool for a brief moment before being confirmed. Bots watch for this and jump in front of profitable-looking trades, stealing value from regular users before their trade executes. This is called front-running, part of the broader "MEV" problem, and it has cost DeFi users hundreds of millions of dollars.

Our Solution

Instead of submitting a trade in the open, our FairTradeArena contract requires traders to lock in their order as a scrambled hash first (commit), and only reveal the real order (direction + amount) afterward (reveal) — by which point it's too late for a bot to react. We prove this works by running the same AI trading agent and the same front-running bot against two contracts side by side:

FairTradeArena.sol — protected, commit-reveal, immune to front-running
UnprotectedArena.sol — vulnerable, orders visible immediately
Live Demo
Deployed contracts (Monad Testnet):
FairTradeArena: https://testnet.monadexplorer.com/address/0xd78DCd80c693Ba9dB9f92ba81Bd407a9E01aB528
UnprotectedArena: https://testnet.monadexplorer.com/address/0x842c522d7F4A13CC3Dc406Fec28A397f97D819F3
Block explorer: https://testnet.monadexplorer.com/
Live dashboard: [Netlify URL here]
How It Works
An AI trading agent (trading_agent.py) runs a simple momentum strategy, trading against both contracts.
A front-running bot (frontrunner_bot.py) watches UnprotectedArena for new orders and immediately copies them to profit off the resulting price movement — successfully, every time.
The same bot watches FairTradeArena, but since orders are hidden until reveal, it has nothing to react to — the agent trades safely.
A live dashboard (dashboard.html) polls both contracts and shows the contrast in real time.
Why Monad

This mechanic requires many fast, cheap transactions — a commit, a reveal, and an execution for every single trade, repeated continuously. That volume of on-chain activity is only practical on a fast, low-cost chain like Monad; on slower/pricier chains this pattern would be too expensive to run live.

Tech Stack
Solidity — smart contracts (FairTradeArena.sol, UnprotectedArena.sol)
Python / web3.py — trading agent and front-running bot
HTML + ethers.js — live PnL dashboard
Monad Testnet — deployment target (Chain ID 10143)
Repo Structure
/contracts       Solidity contracts + deployed addresses/ABI
/agent           Python trading agent + front-running bot
dashboard.html   Live PnL dashboard (deploy via Netlify)
README.md        This file
Running It Locally
bash
pip install web3 python-dotenv

python trading_agent.py       # in one terminal
python frontrunner_bot.py     # in a second terminal

Open dashboard.html (with your addresses filled in) locally, or visit the deployed Netlify link above, to watch PnL update live.

blem (front-running/MEV) — not a simulated or theoretical fix
Side-by-side proof: the same agents and bot behave completely differently depending only on whether commit-reveal is used
Built to showcase Monad's transaction throughput as a core requirement of the solution, not an incidental detail
