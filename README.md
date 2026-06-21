# Discord Bot

An economy-driven Discord gambling bot built with Python 3 and `discord.py`.  
All data (Gold balances, daily cooldowns) persists across restarts via database.

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

> On Windows, set `$env:PYTHONUTF8=1` before running to avoid Unicode errors.

---

## Economy System — Gold 💰

Gold is the primary currency. Balances are **guild-specific** — your Gold in Server A is completely separate from Server B.

| Command | Description |
|---|---|
| `/daily` | Claim **500 Gold** once per day (resets at midnight MYT) |
| `/work` | Wash dishes to earn **50 Gold** (10-minute cooldown). 1% chance to find a Rolex and wipe your debt |
| `/scratch` | Free scratch card for players **in debt** (24h cooldown). 1% jackpot clears all debt |
| `/yolo` | Russian roulette for players below **-1000 Gold** (24h cooldown). 10% chance: debt cleared + 500 Gold; 90% chance: debt increases 50% |
| `/rob @target` | Rob another player (3 attempts/day). 40% success: steal 20–30% of their Gold. Failure: lose 10–15% of yours |
| `/balance [user]` | Check your own or someone else's Gold balance |
| `/leaderboard` | Top 10 richest players in the current server |
| `/disclaimer` | Legal disclaimer — Gold has no real-money value |

### Debt System (Negative Balance)

Players can go into **negative Gold** (debt). There is no floor.

- If you attempt to start/join a **staked game** with ≤ 0 Gold, the bot shows a warning with **Continue / Cancel** buttons before proceeding.
- If you **win** while having been in debt at the start of the game, a **30% interest tax** is applied to your net profit — rounded up (`ceil(net_profit × 0.30)`).
- Use `/daily`, `/work`, `/scratch`, or `/yolo` to try to recover.

---

## Games

### Ban-Luck (Malaysian 21) — `/bj`

| Mode | Command | Currency |
|---|---|---|
| Staked | `/bj bet:<amount>` | Gold 💰 |
| Free Play | `/bj` | Tokens 🎟️ (1 token per game) |

#### Lobby

- You open the lobby as the **Banker (庄家)**. Up to **4 other players** can join via the **🪑 Join** button (5 total at the table).
- **🚪 Leave** — Players can leave the lobby before the game starts. The banker can also click Leave to **disband the entire lobby**.
- **▶️ Start Game** — Banker force-starts early; table auto-starts when full.
- Lobby expires after **2 minutes** if not started.

#### Gameplay

- 2 hidden cards are dealt to everyone. Each player receives a private (ephemeral) message with their starting hand.
- Players take turns sequentially: **Hit**, **Stand**, or **Escape**. Banker plays last.
- **🃏 My Cards** — Sends a private ephemeral with your current hand and a rendered card image (available at any time).
- **🔄 Refresh** — Reposts the game board at the bottom of the chat and resets the 5-minute button timer.
- After every Hit, the bot sends you an ephemeral with your updated hand.
- The public board keeps all cards hidden until the final reveal.
- **60-second turn timer** — auto-Stand on timeout.

#### Rules

**Dynamic Ace Value:**

| Cards in hand | Ace counts as |
|---|---|
| 2 cards | 1, 10, or 11 (best value that doesn't bust) |
| 3 cards | 1 or 10 |
| 4 or 5 cards | 1 only |

**Minimum 16 Rule:** Any player or banker with < 16 points **must Hit**. Stand shows an error if points < 16 (bypassed for special hands).

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

#### Rematch

After every game a **🔄 Rematch** button appears. All previous participants must click it to agree. Once unanimous, a new game starts with a randomly selected banker.

---

### Texas Hold'em Poker — `/poker`

| Command | Description |
|---|---|
| `/poker [blind:<amount>]` | Open a poker table (default blind: 50, min: 10) |
| `/pokerstop` | Close your open lobby (host only, lobby phase only) |

Uses **Gold 💰** only — no free play mode.

#### Lobby

- The player who runs `/poker` is the **host**. Up to **8 players** can join via **🪑 Join**.
- **🚪 Leave** — Leave before the game starts.
- **▶️ Start Game** — Host starts when ≥ 2 players are ready; table auto-starts when full.
- Lobby expires after **5 minutes** if not started.

#### Gameplay

- Standard Texas Hold'em: 2 hole cards per player + 5 community cards (Flop → Turn → River).
- Blinds: **Small Blind = blind**, **Big Blind = blind × 2**.
- On game start, each player receives their hole cards via **DM** (private, not visible to others).
- Community cards are displayed on the public board with coloured suit emojis and a rendered card image.
- **60-second turn timer** — auto-fold on timeout.

#### Buttons

| Button | Description |
|---|---|
| 🃏 Fold | Fold your hand and exit the current round |
| ✅ Check / 📞 Call | Check (free) or call the current bet — label updates dynamically |
| 💰 Raise | Open a modal to enter your raise amount (min = current bet + blind) |
| ⚡ All In | Go all-in with your remaining Gold |
| 🂠 My Cards | Ephemerally shows your hole cards + best hand so far |
| 🔄 Refresh | Reposts the board at the bottom of the chat |

After each action button, the bot automatically sends you an ephemeral showing your cards and current best hand.

#### Hand Rankings (high → low)

| Rank | Name |
|---|---|
| 9 | Royal Flush 👑 |
| 8 | Straight Flush 🌊 |
| 7 | Four of a Kind 🎰 |
| 6 | Full House 🏠 |
| 5 | Flush 🎨 |
| 4 | Straight ➡️ |
| 3 | Three of a Kind 🎲 |
| 2 | Two Pair 👯 |
| 1 | One Pair |
| 0 | High Card |

Best 5-card hand is picked automatically from each player's 2 hole cards + 5 community cards.

#### Showdown & Payouts

- At showdown all remaining players' hands are revealed publicly.
- Pot is split equally among tied winners; remainder Gold (if odd) goes to first winner.
- A **🔄 Rematch** button appears after every game — all participants must agree to restart.

---

### Heads or Tails — `/ht`

| Mode | Command | Currency |
|---|---|---|
| Staked | `/ht choice:<h\|t> bet:<amount>` | Gold 💰 |
| Free Play | `/ht choice:<h\|t>` | Tokens 🎟️ (1 token per flip) |

- Win: receive 1× your bet. Lose: lose your bet.
- Debt interest (30%) applies to winnings if you were in debt at the start.

---

### Slot Machine — `/slots`

```
/slots bet:100
```

- Bet must be a positive integer. Gold 💰 only — no free play mode.
- A **3×3 emoji grid** is generated: 🍒 🍋 🍉 🔔 💎 🎰
- Only the **middle row** determines the outcome.
- A spinning animation shows for 1.5 seconds, then the grid is revealed.

**Payout Multipliers:**

| Middle Row | Multiplier |
|---|---|
| 🎰 🎰 🎰 | **50×** — JACKPOT!! |
| 💎 💎 💎 | **20×** — MEGA WIN! |
| Any other 3 identical | **10×** — BIG WIN! |
| Any 2 identical | **1×** — Push (bet returned) |
| All different | **0** — Lose |

---

## Token System (Free Play)

Used by `/bj` (no bet) and `/ht` (no bet). Separate balance per user, not guild-specific.

| Command | Description |
|---|---|
| `/tokens [user]` | Check your (or someone else's) token balance |
| `/resettoken` | Reset **all** token balances to 0 |

- Default cost: **1 token** per game
- Tokens can go **negative** (no floor)
- Payout multipliers are the same as staked games

---

## Project Structure

```
discord bot/
├── main.py           ← bot entry point + all commands + Discord UI views
├── blackjack.py      ← Ban-Luck game logic (Card, Deck, Hand, GameTable)
├── texas_poker.py    ← Texas Hold'em logic (PokerPlayer, PokerTable, hand evaluator)
├── card_renderer.py  ← fetches card PNGs from deckofcardsapi.com, composites with Pillow
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
| Bot library | discord.py 2.x (slash commands, `ui.View`, `ui.Modal`) |
| Database | PostgreSQL (Supabase) in production · SQLite locally |
| Image rendering | Pillow + aiohttp (card images from deckofcardsapi.com) |
| Config | python-dotenv |
| Hosting | Koyeb (Docker) + UptimeRobot keep-alive |

---

## Feature Changelog

- [x] Gold economy system (guild-specific)
- [x] `/daily` — 500 Gold every 24 hours (midnight MYT reset)
- [x] `/work` — 50 Gold every 10 minutes, 1% Rolex event wipes debt
- [x] `/scratch` — free card for players in debt, 1% jackpot
- [x] `/yolo` — Russian roulette for players below -1000 Gold
- [x] `/rob` — steal Gold from another player (3 attempts/day, 40% success)
- [x] `/balance` — check own or another user's Gold
- [x] `/leaderboard` — top 10 richest per server
- [x] Debt system — negative Gold allowed, 30% interest on winnings
- [x] Debt confirmation gate (Continue / Cancel) for all staked games
- [x] **Ban-Luck** — staked (Gold) and free play (tokens) modes
- [x] **Ban-Luck** — dynamic Ace, must-hit-16, 15/16 Escape
- [x] **Ban-Luck** — special hands (Ban-Ban, Ban-Luck, Double, 五龙, 7-7-7)
- [x] **Ban-Luck** — ephemeral card images per player, board always at bottom
- [x] **Ban-Luck** — 60s turn timer with auto-Stand on timeout
- [x] **Ban-Luck** — 🔄 Refresh button, 🚪 Leave button
- [x] **Ban-Luck** — Rematch system (unanimous vote, random banker)
- [x] **Texas Hold'em** — full betting rounds (preflop → flop → turn → river → showdown)
- [x] **Texas Hold'em** — Gold as betting currency, pot tracking, split pot on tie
- [x] **Texas Hold'em** — hole cards delivered via DM (private to each player)
- [x] **Texas Hold'em** — community card image rendered and posted on every action
- [x] **Texas Hold'em** — ephemeral hand strength shown after every action
- [x] **Texas Hold'em** — 60s turn timer with auto-fold on timeout
- [x] **Texas Hold'em** — all-in side-pot awareness, fast-forward to showdown
- [x] **Texas Hold'em** — Rematch system (unanimous vote, random host)
- [x] **Heads or Tails** — staked (Gold) and free play (tokens) modes
- [x] **Slot Machine** (`/slots`) — 3×3 grid, 50×/20×/10×/1× multipliers
- [x] Token system — `/tokens`, `/resettoken`
- [x] All commands as Discord slash commands with autocomplete descriptions
- [x] `/disclaimer` — legal disclaimer
- [x] Deploy 24/7 on Koyeb + Supabase
