# Discord Bot MVP

A lightweight, economy-driven Discord bot built with Python 3 and `discord.py`.  
XP is persistently stored in a local **SQLite** database (`bot_data.db`) — no data lost on restart.

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
python main.py  ← restart (XP is saved in bot_data.db)
```

---

## Commands

| Command | Example | Description |
|---|---|---|
| `!level` | `!level` | Show your current Level and XP progress |
| `!cointoss` | `!cointoss 50 heads` | Wager XP on a 50/50 coin flip |
| `!bj` | `!bj 50` | Open a Ban-Luck lobby as banker; others join via button |

---

## XP & Level System

**Earning XP:** XP is awarded per message based on **character count** (not message count) to discourage spam.

| Message length | XP gained |
|---|---|
| 1–4 chars | 1 XP (minimum) |
| ~25 chars | 5 XP |
| ~50 chars | 10 XP |
| 100+ chars | 20 XP (maximum per message) |

The bot ignores its own messages to prevent infinite loops.

**Level thresholds (0–99):** XP required to advance from level `n` to `n+1`:

```
xp_to_next(n) = 5·n² + 50·n + 100
```

**Clamping:** XP is always floored at 0 (cannot go negative), enforced at the DB write layer.

---

## Feature Spec: P2P Ban-Luck (Malaysian 21)

> **Note for AI Assistant (Cursor):** Implement steps sequentially. Do not proceed to the next step until the current one is fully functional. Treat this like a State Machine. **CRITICAL: This is NOT standard Blackjack. Read the custom rules below carefully.**

### Multi-Player Lobby System

Command: `!bj <bet>` — you become the **Banker (庄家)**.

- Up to **4 other players** join by clicking the **Join** button (5 total at the table).
- Banker can force-start early via **Start Game**; table auto-starts when full.
- Lobby expires after **2 minutes** if not started.
- **Strict Escrow (资金冻结):** Banker's XP requirement = `bet × num_players × 5` (covers the 5× maximum payout for every player).
- Players must have at least `bet` XP to join.

### Custom Rules & Mechanics (Ban-Luck Logic)

**Dynamic Ace Value:**

| Cards in hand | Ace counts as |
|---|---|
| 2 cards | 1, 10, or 11 (best value that doesn't bust) |
| 3 cards | 1 or 10 |
| 4 or 5 cards | 1 only |

**Minimum 16 Rule:** Any player or banker with < 16 points **must Hit**. The Stand button returns an error if points < 16.

**15/16 Escape (Surrender):** On the initial 2-card deal, if the hand exactly equals 15 or 16, a **🏃 Escape (走)** button appears. Clicking it refunds the bet and removes the player from the current round.

### Turn Order

- 2 hidden cards dealt to everyone.
- Players take turns sequentially (Hit / Stand / Escape). Banker plays last.
- **My Cards** sends an ephemeral message — *only you can see this* — with full hand + rendered card image.
- After every Hit, the bot automatically sends you an ephemeral with your updated hand + image.
- Public board always shows first card hidden (`[?]`).
- If a player busts (> 21), they lose immediately and their turn ends.
- Once all players are done, the Banker plays. Banker's final hand is compared against all surviving players.

### Special Hands & Payout Model

| Name | Trigger | Payout |
|---|---|---|
| Ban-Ban 双A ✨ | Two Aces on initial 2-card deal | 3× |
| Ban-Luck 过海 🌊 | Ace + (10, J, Q, K) on initial deal | 2× |
| Double 双对子 👯 | Two identical ranks on initial deal (e.g. 8-8) | 2× |
| 五龙 Five Dragon 🐲 | 5 cards without busting | 5× (economy cap) |
| 7-7-7 三条七 🎰 | Three 7s totalling 21 | 5× (economy cap) |
| Normal Win | Higher points than banker without busting | 1× |

**Clash Rule (神仙打架):** If both banker and player have special hands, the higher multiplier wins. Equal multipliers → Push (refund).

- Each player-banker pair is settled independently.
- Payouts drawn from banker's frozen escrow. Remaining escrow returned to banker.

---

## Project Structure

```
discord bot/
├── main.py           ← bot entry point + all commands + Discord UI views
├── blackjack.py      ← pure game logic (Card, Deck, Hand, PlayerState, GameTable)
├── card_renderer.py  ← fetches card PNGs from API, composites with Pillow
├── bot_data.db       ← SQLite database (auto-created, gitignored)
├── requirements.txt
├── .env              ← secret token (never commit)
├── .gitignore
└── README.md
```

---

## Tech Stack

| | |
|---|---|
| Language | Python 3.12 |
| Bot library | discord.py 2.7.1 |
| Database | SQLite (via Python built-in `sqlite3`) |
| Image rendering | Pillow + aiohttp (card images from deckofcardsapi.com) |
| Config | python-dotenv |

---

## Roadmap

- [x] XP tracking per user (character-based, anti-spam)
- [x] Level system (0–99) with progressive XP thresholds
- [x] `!level` command — show level and XP progress
- [x] `!cointoss` minigame — wager XP on a coin flip
- [x] Persist XP to SQLite (survive restarts)
- [x] **P2P Ban-Luck** — Lobby system (banker + up to 4 players)
- [x] **P2P Ban-Luck** — Dynamic Ace, must-hit-16, 15/16 Escape
- [x] **P2P Ban-Luck** — Special hands (Ban-Ban, Ban-Luck, Double, 五龙, 7-7-7)
- [x] **P2P Ban-Luck** — Ephemeral card images + strict 5× escrow
- [ ] XP cooldown (rate limit per user)
- [ ] `!leaderboard` — top users by level
- [ ] Deploy 24/7 on Railway (GitHub Student Pack)
