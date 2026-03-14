# Discord Bot

An economy-driven Discord gambling bot built with Python 3 and `discord.py`.  
All data (Gold balances, tokens, daily cooldowns) persists across restarts via database.

---

## Quick Start

### 1. Install dependencies

```powershell
pip install -r requirements.txt
```

### 2. Set your bot token

Open `.env` and paste your token:

```
DISCORD_TOKEN=your_token_here
```

> Get your token at [discord.com/developers/applications](https://discord.com/developers/applications).  
> **Important:** Enable **Message Content Intent** under *Privileged Gateway Intents* so the bot can read messages.

### 3. Run

```powershell
python main.py
```

### 4. Stop / Restart

```
Ctrl + C        ← stop
python main.py  ← restart (all data saved in the database)
```

---

## Economy System — Gold 💰

Gold is the primary currency. Balances are **guild-specific** — your Gold in Server A is completely separate from Server B.

| Command | Example | Description |
|---|---|---|
| `/daily` | `/daily` | Claim **300 Gold** once every 24 hours |
| `/balance [user]` | `/balance` / `/balance @friend` | Check your own or someone else's Gold balance |
| `/leaderboard` | `/leaderboard` | Top 10 richest players in the current server |
| `/disclaimer` | `/disclaimer` | Legal disclaimer — Gold, tokens, no real money, no gambling |

### Debt System (Negative Balance)

Players can go into **negative Gold** (debt). There is no floor.

- If you attempt to start/join a **staked game** with ≤ 0 Gold, the bot shows a warning with **Continue / Cancel** buttons before proceeding.
- If you **win** while having been in debt at the start of the game, a **30% interest tax** is applied to your net profit — rounded **up** (`ceil(net_profit × 0.30)`).
- Use `/daily` to recover if you go broke.

---

## Games

### Ban-Luck (Malaysian 21) — `!bj`

| Mode | Command | Currency |
|---|---|---|
| Staked | `/bj bet:<amount>` | Gold 💰 |
| Free Play | `/bj` | Tokens 🎟️ (1 token per game) |

#### Lobby

- You open the lobby as the **Banker (庄家)**. Up to **4 other players** can join via the **🪑 Join** button (5 total at the table).
- **🚪 Leave** — Players can leave the lobby before the game starts. The **banker** can also click Leave to **disband the entire lobby**.
- **▶️ Start Game** — Banker force-starts early; table auto-starts when full.
- Lobby expires after **2 minutes** if not started.
- If the banker opens a staked lobby with ≤ 0 Gold, a public debt warning is shown.
- If a player joins while in debt, a private **Continue / Cancel** confirmation is shown before they are added.

#### Gameplay

- 2 hidden cards are dealt to everyone. Each player receives a private (ephemeral) message with their starting hand.
- Players take turns sequentially: **Hit**, **Stand**, or **Escape**. Banker plays last.
- **🃏 My Cards** — Sends a private message with your current hand and a rendered card image (available at any time).
- **🔄 Refresh** — Reposts the game board at the bottom of the chat and fully resets the 5-minute button timer.
- After every Hit, the bot sends you an ephemeral with your updated hand.
- The public board keeps all cards hidden (`[?]`) until the final reveal.

#### Rules

**Dynamic Ace Value:**

| Cards in hand | Ace counts as |
|---|---|
| 2 cards | 1, 10, or 11 (best value that doesn't bust) |
| 3 cards | 1 or 10 |
| 4 or 5 cards | 1 only |

**Minimum 16 Rule:** Any player or banker with < 16 points **must Hit**. The Stand button shows an error if points < 16 (bypassed for special hands).

**15/16 Escape (走):** On the initial 2-card deal, if the hand exactly equals 15 or 16, a **🏃 Escape** button appears. Clicking it refunds the bet and removes the player from the round.

#### Special Hands & Payout Multipliers

| Name | Trigger | Multiplier |
|---|---|---|
| Ban-Ban 双A ✨ | Two Aces on initial deal | 3× |
| Ban-Luck 过海 🌊 | Ace + (10/J/Q/K) on initial deal | 2× |
| Double 双对子 👯 | Two identical ranks on initial deal | 2× |
| 五龙 Five Dragon 🐲 | 5 cards without busting | 5× |
| 7-7-7 三条七 🎰 | Three 7s totalling 21 | 5× |
| Normal Win | Higher score than banker (no bust) | 1× |

**Clash Rule (神仙打架):** If both banker and player have special hands, the higher multiplier wins. Equal multipliers → Push (bet refunded).

The banker can go into debt to cover payouts — no escrow check blocks the game from starting.

#### Rematch

After every game a **🔄 Rematch** button appears. All previous participants must click it to agree. Once unanimous, a new game starts with a randomly selected banker.

---

### Heads or Tails — `!ht`

| Mode | Command | Currency |
|---|---|---|
| Staked | `/ht choice:<h\|t> bet:<amount>` | Gold 💰 |
| Free Play | `/ht choice:<h\|t>` | Tokens 🎟️ (1 token per flip) |

- Win: receive 1× your bet. Lose: lose your bet.
- If playing staked with ≤ 0 Gold, a **Continue / Cancel** confirmation is shown before the flip.
- Debt interest (30%) applies to winnings if you were in debt at the start.

---

### Slot Machine — `/slots`

```
/slots bet:100
```

- Bet must be a positive integer. Uses Gold 💰 only (no free play mode).
- A **3×3 emoji grid** is generated: 🍒 🍋 🍉 🔔 💎 🎰
- Only the **middle row** determines the outcome.
- A spinning animation `[ 🔄 | 🔄 | 🔄 ]` shows for 1.5 seconds, then the grid is revealed.

**Payout Multipliers:**

| Middle Row | Multiplier |
|---|---|
| 🎰 🎰 🎰 | **50×** — JACKPOT!! MASSIVE WIN! |
| 💎 💎 💎 | **20×** — MEGA WIN! |
| Any other 3 identical | **10×** — BIG WIN! |
| Any 2 identical | **1×** — Push (bet returned, no profit) |
| All different | **0** — Lose |

- If your Gold balance is ≤ 0 when running `/slots`, a **Continue / Cancel** confirmation is shown.
- Debt interest (30%) applies to winnings if you were in debt at the start.

---

## Token System (Free Play)

Used by `!bj` (no bet) and `!ht <h|t>` (no bet). Tracks a separate balance per user.

| Command | Description |
|---|---|
| `/tokens [user]` | Check your (or someone else's) token balance |
| `/resettoken` | Reset **all** token balances to 0 |

- Default bet: **1 token** per game
- Tokens can go **negative** (no floor)
- Payout multipliers are the same as staked games

---

## Project Structure

```
discord bot/
├── main.py           ← bot entry point + all commands + Discord UI views
├── blackjack.py      ← pure game logic (Card, Deck, Hand, PlayerState, GameTable)
├── card_renderer.py  ← fetches card PNGs from API, composites with Pillow
├── bot_data.db       ← SQLite database (auto-created, gitignored)
├── requirements.txt
├── Dockerfile        ← for Koyeb deployment
├── .env              ← secret token (never commit)
├── .gitignore
└── README.md
```

---

## Tech Stack

| | |
|---|---|
| Language | Python 3.12 |
| Bot library | discord.py |
| Database | PostgreSQL (Supabase) in production · SQLite locally |
| Image rendering | Pillow + aiohttp (card images from deckofcardsapi.com) |
| Config | python-dotenv |
| Hosting | Koyeb (Docker) + UptimeRobot keep-alive |

---

## Feature Changelog

- [x] Gold economy system (guild-specific, replaces XP)
- [x] `/daily` — 300 Gold every 24 hours
- [x] `/balance` — check own or another user's Gold
- [x] `/leaderboard` — top 10 richest per server
- [x] Debt system — negative Gold allowed, 30% interest on winnings
- [x] Debt confirmation gate (Continue / Cancel) for all staked games
- [x] **Ban-Luck** — staked (Gold) and free play (tokens) modes
- [x] **Ban-Luck** — dynamic Ace, must-hit-16, 15/16 Escape
- [x] **Ban-Luck** — special hands (Ban-Ban, Ban-Luck, Double, 五龙, 7-7-7)
- [x] **Ban-Luck** — ephemeral card images per player
- [x] **Ban-Luck** — game board always at bottom (delete + resend on every action)
- [x] **Ban-Luck** — 🔄 Refresh button resets 5-minute interaction timer
- [x] **Ban-Luck** — 🚪 Leave button (players leave; banker disbands lobby)
- [x] **Ban-Luck** — banker can go into debt (no escrow block)
- [x] **Ban-Luck** — Rematch system (unanimous vote, random banker)
- [x] **Heads or Tails** — staked (Gold) and free play (tokens) modes
- [x] **Slot Machine** (`/slots`) — 3×3 grid, 50×/20×/10×/1× multipliers, spinning animation
- [x] Token system — `/tokens`, `/resettoken`
- [x] All commands migrated to Discord slash commands (`/`) with autocomplete descriptions
- [x] `/disclaimer` — legal disclaimer (no gambling, tokens have no value, no real-money purchase/withdrawal)
- [x] Deploy 24/7 on Koyeb + Supabase
