"""
Discord Bot
-----------
Features:
  - Gold economy (guild-specific, debt allowed)
  - /daily        → claim 300 Gold once every 24 hours
  - /balance      → check Gold balance (self or @user)
  - /leaderboard  → top 10 richest players in the server
  - /ht           → wager Gold on a 50/50 coin flip
  - /bj           → Malaysian Ban-Luck (21)
  - /slots        → 3×3 slot machine
"""

import asyncio
import datetime
import math
import os
import random
import sqlite3
import discord
from discord.ext import commands
from discord import app_commands, ui
from typing import Literal
from datetime import timezone, timedelta
from dotenv import load_dotenv
from blackjack import GameTable, PlayerState
from card_renderer import render_hand_image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()
TOKEN: str = os.getenv("DISCORD_TOKEN")

DAILY_REWARD:        int   = 500   # Gold awarded by /daily (updated from 300 to 500)
DEBT_TAX_RATE:       float = 0.30  # 30% tax on net winnings when in debt
FREE_PLAY_TOKEN_BET: int   = 1     # token bet size in free-play mode
MAX_BET:             int   = 5000  # Maximum bet amount across all gambling commands
MAX_DEBT:            int   = -10000  # Minimum allowed Gold balance (debt floor)

DEBT_WARNING = (
    "⚠️ You are gambling on borrowed Gold! "
    "If you win, **30% interest** will be deducted from your profits! "
    "Run out of Gold? Use `/daily` to claim your daily relief fund!"
)


# ---------------------------------------------------------------------------
# Timezone Helper
# ---------------------------------------------------------------------------

def get_today_myt() -> str:
    """
    Return the current date in Malaysia Time (UTC+8) as 'YYYY-MM-DD' string.
    """
    myt = timezone(timedelta(hours=8))
    now_myt = datetime.datetime.now(myt)
    return now_myt.strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# Economic Firewall Validation
# ---------------------------------------------------------------------------

def validate_bet_with_firewall(guild_id: int, user_id: int, bet: int) -> tuple[bool, str]:
    """
    Validate bet against economic firewall rules.
    
    Returns (is_valid, error_message).
    If is_valid is True, error_message is empty.
    """
    # Check MAX_BET
    if bet > MAX_BET:
        return False, f"❌ Bet exceeds maximum allowed ({MAX_BET:,} 💰)"
    
    # Check MAX_DEBT for in-debt players
    current_gold = get_gold(guild_id, user_id)
    if current_gold <= 0:
        projected_debt = current_gold - int(bet * 1.3)
        if projected_debt < MAX_DEBT:
            return False, f"🚫 Ah Long credit limit reached! Maximum debt is {MAX_DEBT:,} 💰. Your projected debt would be {projected_debt:,} 💰."
    
    return True, ""

# ---------------------------------------------------------------------------
# Database  (PostgreSQL in cloud via Supabase, SQLite for local dev)
# ---------------------------------------------------------------------------

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    import psycopg2

    class _DB:
        """
        Thin psycopg2 wrapper that mimics the sqlite3 connection API.
        Translates SQLite-style '?' placeholders to PostgreSQL '%s'.
        Auto-reconnects on dropped connections.
        """

        def __init__(self, url: str) -> None:
            self._url  = url
            self._conn = self._connect()

        def _connect(self):
            conn = psycopg2.connect(self._url)
            conn.autocommit = False
            return conn

        def execute(self, sql: str, params: tuple = ()):
            sql = sql.replace("?", "%s")
            for attempt in range(2):
                try:
                    cur = self._conn.cursor()
                    cur.execute(sql, params)
                    return cur
                except psycopg2.OperationalError:
                    if attempt == 0:
                        self._conn = self._connect()
                    else:
                        raise

        def commit(self) -> None:
            self._conn.commit()

        def rollback(self) -> None:
            self._conn.rollback()

    db = _DB(DATABASE_URL)
    print("[DB] Connected → PostgreSQL (Supabase)")

else:
    DB_PATH: str = os.environ.get(
        "DB_PATH",
        os.path.join(os.path.dirname(__file__), "bot_data.db"),
    )
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    print(f"[DB] Connected → {DB_PATH}")

# user_gold: guild-specific Gold balances (debt allowed — no floor)
db.execute("""
    CREATE TABLE IF NOT EXISTS user_gold (
        guild_id   BIGINT  NOT NULL,
        user_id    BIGINT  NOT NULL,
        gold       INTEGER NOT NULL DEFAULT 0,
        last_daily TEXT,
        PRIMARY KEY (guild_id, user_id)
    )
""")

# user_tokens: free-play token balances (global, not guild-specific)
db.execute("""
    CREATE TABLE IF NOT EXISTS user_tokens (
        user_id  BIGINT  PRIMARY KEY,
        tokens   INTEGER NOT NULL DEFAULT 0
    )
""")

# user_slots_pity: per-guild slots spin count + which spin (1–5) is the guaranteed triple (random)
db.execute("""
    CREATE TABLE IF NOT EXISTS user_slots_pity (
        guild_id       BIGINT  NOT NULL,
        user_id        BIGINT  NOT NULL,
        spin_count     INTEGER NOT NULL DEFAULT 0,
        pity_trigger_at INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id)
    )
""")
try:
    db.execute("ALTER TABLE user_slots_pity ADD COLUMN pity_trigger_at INTEGER DEFAULT 0")
    db.commit()
except Exception:
    pass

# Add new columns for robbery tracking
try:
    db.execute("ALTER TABLE user_gold ADD COLUMN last_rob_date TEXT")
    db.commit()
except Exception:
    pass

try:
    db.execute("ALTER TABLE user_gold ADD COLUMN rob_count INTEGER DEFAULT 0")
    db.commit()
except Exception:
    pass

db.commit()


# ---------------------------------------------------------------------------
# Gold helpers  (guild-specific; no floor — negative balance / debt allowed)
# ---------------------------------------------------------------------------

def get_gold(guild_id: int, user_id: int) -> int:
    """Return the player's current Gold balance in this guild (0 for first-timers)."""
    row = db.execute(
        "SELECT gold FROM user_gold WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return row[0] if row else 0


def add_gold(guild_id: int, user_id: int, amount: int) -> int:
    """
    Add (or subtract) Gold for a player in this guild.
    No floor — balance may go negative (debt).
    Returns the new balance.
    """
    new_val = get_gold(guild_id, user_id) + amount
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET gold = excluded.gold
        """,
        (guild_id, user_id, new_val),
    )
    db.commit()
    return new_val


def set_gold(guild_id: int, user_id: int, new_val: int) -> int:
    """
    Set Gold balance for a player in this guild.
    Returns the new balance.
    """
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET gold = excluded.gold
        """,
        (guild_id, user_id, new_val),
    )
    db.commit()
    return new_val


def get_last_daily(guild_id: int, user_id: int) -> str | None:
    row = db.execute(
        "SELECT last_daily FROM user_gold WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return row[0] if row else None


def set_last_daily(guild_id: int, user_id: int, ts: str) -> None:
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold, last_daily) VALUES (?, ?, 0, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET last_daily = excluded.last_daily
        """,
        (guild_id, user_id, ts),
    )
    db.commit()


def get_rob_data(guild_id: int, user_id: int) -> tuple[str | None, int]:
    """
    Fetch (last_rob_date, rob_count) for a user.
    Returns (None, 0) for first-time users.

    On Postgres/Supabase, this also lazily adds the robbery columns if they
    were not created yet, so we don't crash with UndefinedColumn.
    """
    try:
        row = db.execute(
            "SELECT last_rob_date, rob_count FROM user_gold WHERE guild_id = ? AND user_id = ?",
            (guild_id, user_id),
        ).fetchone()
    except Exception:
        # Best-effort lazy migration: add columns if missing, then retry once.
        try:
            # Reset broken transaction state on Postgres if needed
            try:
                db.rollback()  # type: ignore[attr-defined]
            except Exception:
                pass
            db.execute("ALTER TABLE user_gold ADD COLUMN last_rob_date TEXT")
            db.commit()
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE user_gold ADD COLUMN rob_count INTEGER DEFAULT 0")
            db.commit()
        except Exception:
            pass
        try:
            row = db.execute(
                "SELECT last_rob_date, rob_count FROM user_gold WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        except Exception:
            return None, 0

    if row is None:
        return None, 0
    return row[0], row[1] if row[1] is not None else 0


def update_rob_data(guild_id: int, user_id: int, date: str, count: int) -> None:
    """
    Update last_rob_date and rob_count for a user.
    """
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold, last_rob_date, rob_count) 
        VALUES (?, ?, 0, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET 
            last_rob_date = excluded.last_rob_date,
            rob_count = excluded.rob_count
        """,
        (guild_id, user_id, date, count),
    )
    db.commit()


def reset_rob_count_if_new_day(guild_id: int, user_id: int, today: str) -> int:
    """
    Check if last_rob_date differs from today (MYT).
    If different, reset rob_count to 0 and update date.
    Returns current rob_count.
    """
    last_rob_date, rob_count = get_rob_data(guild_id, user_id)
    
    if last_rob_date != today:
        # New day - reset count
        update_rob_data(guild_id, user_id, today, 0)
        return 0
    
    return rob_count


# ---------------------------------------------------------------------------
# Token helpers  (free-play only; no floor — can go negative)
# ---------------------------------------------------------------------------

def get_tokens(user_id: int) -> int:
    row = db.execute(
        "SELECT tokens FROM user_tokens WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else 0


def add_tokens(user_id: int, amount: int) -> None:
    new_val = get_tokens(user_id) + amount
    db.execute(
        """
        INSERT INTO user_tokens (user_id, tokens) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET tokens = excluded.tokens
        """,
        (user_id, new_val),
    )
    db.commit()


def reset_all_tokens() -> None:
    db.execute("UPDATE user_tokens SET tokens = 0")
    db.commit()


# ---------------------------------------------------------------------------
# Slots pity (guarantee 3-of-a-kind within first 5 spins for new players)
# ---------------------------------------------------------------------------

def get_slots_pity(guild_id: int, user_id: int) -> tuple[int, int]:
    """Return (spin_count, pity_trigger_at). spin_count = completed spins; pity_trigger_at = 1–5 or 0 if not set."""
    row = db.execute(
        "SELECT spin_count, pity_trigger_at FROM user_slots_pity WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    if row is None:
        return 0, 0
    return row[0], row[1] or 0


def ensure_pity_trigger_and_get(guild_id: int, user_id: int) -> tuple[int, int]:
    """
    If user is in first 5 spins and pity_trigger_at not set, set it to random 1–5 and save.
    Return (spin_count, pity_trigger_at).
    """
    spin_count, pity_trigger_at = get_slots_pity(guild_id, user_id)
    next_spin = spin_count + 1
    if next_spin <= 5 and pity_trigger_at <= 0:
        pity_trigger_at = random.randint(1, 5)
        db.execute(
            """
            INSERT INTO user_slots_pity (guild_id, user_id, spin_count, pity_trigger_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET pity_trigger_at = excluded.pity_trigger_at
            """,
            (guild_id, user_id, spin_count, pity_trigger_at),
        )
        db.commit()
    return spin_count, pity_trigger_at


def update_slots_pity_after_spin(guild_id: int, user_id: int) -> None:
    """Increment spin count after a spin."""
    spin_count, pity_trigger_at = get_slots_pity(guild_id, user_id)
    new_count = spin_count + 1
    db.execute(
        """
        INSERT INTO user_slots_pity (guild_id, user_id, spin_count, pity_trigger_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET spin_count = excluded.spin_count
        """,
        (guild_id, user_id, new_count, pity_trigger_at or 0),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Bot / Intent Setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    """Global error handler for slash commands."""
    if isinstance(error, app_commands.CommandOnCooldown):
        retry_after = int(error.retry_after)
        minutes, seconds = divmod(retry_after, 60)
        if minutes > 0:
            time_str = f"{minutes}m {seconds}s"
        else:
            time_str = f"{seconds}s"

        if interaction.response.is_done():
            await interaction.followup.send(
                f"⏳ [v3] This command is on cooldown. Try again in **{time_str}**.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"⏳ [v3] This command is on cooldown. Try again in **{time_str}**.",
                ephemeral=True,
            )
        return

    # Fallback generic error message (include error detail to help debugging)
    detail = f"{error.__class__.__name__}: {error}"
    if interaction.response.is_done():
        await interaction.followup.send(
            f"❌ An error occurred while running this command.\n`{detail}`",
            ephemeral=True,
        )
    else:
        await interaction.response.send_message(
            f"❌ An error occurred while running this command.\n`{detail}`",
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Health check server  (Koyeb port check + UptimeRobot keep-alive)
# ---------------------------------------------------------------------------

async def _start_health_server() -> None:
    from aiohttp import web as _web

    port = int(os.environ.get("PORT", 8080))

    async def _ok(request):
        return _web.Response(text="OK")

    app = _web.Application()
    app.router.add_get("/", _ok)
    app.router.add_get("/health", _ok)

    runner = _web.AppRunner(app)
    await runner.setup()
    await _web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"[HEALTH] Listening on port {port}")


# ---------------------------------------------------------------------------
# Event: on_ready
# ---------------------------------------------------------------------------

@bot.event
async def on_ready() -> None:
    asyncio.create_task(_start_health_server())
    await bot.tree.sync()
    print(f"[BOT] Logged in as {bot.user} (ID: {bot.user.id})")
    print("[BOT] Ready. Slash commands synced.")


# ---------------------------------------------------------------------------
# Event: on_message
# ---------------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Command: !daily
# ---------------------------------------------------------------------------

@bot.tree.command(name="daily", description="Claim 500 Gold once per day (resets at midnight MYT)")
async def cmd_daily(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    guild_id = interaction.guild.id
    uid      = interaction.user.id
    today    = get_today_myt()
    last_str = get_last_daily(guild_id, uid)

    # Check if already claimed today (MYT)
    if last_str == today:
        await interaction.response.send_message(
            "❌ You already claimed your daily relief! Wait until midnight (MYT) for the reset.",
            ephemeral=True,
        )
        return

    # Award 500 Gold and update last_daily
    new_bal = add_gold(guild_id, uid, DAILY_REWARD)
    set_last_daily(guild_id, uid, today)

    await interaction.response.send_message(
        "✅ Here is your daily 500 gold. Don't lose it all at once!"
    )


# ---------------------------------------------------------------------------
# Command: /work, /scratch, /yolo  (Debt Redemption)
# ---------------------------------------------------------------------------

@bot.tree.command(name="work", description="Wash dishes to earn 50 Gold (10 min cooldown)")
@app_commands.checks.cooldown(1, 10 * 60)
async def cmd_work(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    guild_id = interaction.guild.id
    uid = interaction.user.id
    gold_before = get_gold(guild_id, uid)

    # 1% Rolex event
    if random.randint(1, 100) == 1 and gold_before < 0:
        set_gold(guild_id, uid, 0)
        await interaction.response.send_message(
            "✨ Holy cow! You found a discarded Rolex in the drain! Your Ah Long debt is completely wiped out!"
        )
        return

    add_gold(guild_id, uid, 50)
    await interaction.response.send_message(
        "🍽️ You washed dishes in the back kitchen for an hour and earned 50 gold."
    )


@bot.tree.command(name="scratch", description="Free scratch card for broke players (24h cooldown)")
@app_commands.checks.cooldown(1, 24 * 60 * 60)
async def cmd_scratch(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    guild_id = interaction.guild.id
    uid = interaction.user.id
    gold_before = get_gold(guild_id, uid)

    if gold_before >= 0:
        await interaction.response.send_message(
            "❌ Only broke people in debt can get a free scratch card!"
        )
        return

    # 1% jackpot event
    if random.randint(1, 100) == 1:
        set_gold(guild_id, uid, 0)
        await interaction.response.send_message(
            "🎉 Jackpot! You won the Ah Long Debt Relief Grand Prize! All debts are cleared!"
        )
        return

    await interaction.response.send_message(
        "🗑️ Better luck next time. The scratch card says: Go back to washing dishes!"
    )


@bot.tree.command(name="yolo", description="Russian roulette for desperate players (24h cooldown)")
@app_commands.checks.cooldown(1, 24 * 60 * 60)
async def cmd_yolo(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    guild_id = interaction.guild.id
    uid = interaction.user.id
    gold_before = get_gold(guild_id, uid)

    if gold_before >= -1000:
        await interaction.response.send_message(
            "❌ Only desperate players with gold below -1000 can use /yolo.",
            ephemeral=True,
        )
        return

    # Win (10% chance)
    if random.randint(1, 100) <= 10:
        set_gold(guild_id, uid, 500)
        await interaction.response.send_message(
            "🔫 *Click*. The gun is empty! Ah Long respects your guts. He clears your debt and gives you 500 gold to start over!"
        )
        return

    # Lose case (not specified in your message, keep consistent with prior design)
    new_gold = int(gold_before * 1.5)
    set_gold(guild_id, uid, new_gold)
    await interaction.response.send_message(
        "💥 *Bang*! You lost! Ah Long beats you up and adds the medical bills to your tab. Your debt increases by 50%!"
    )


# ---------------------------------------------------------------------------
# Command: /rob
# ---------------------------------------------------------------------------

@bot.tree.command(name="rob", description="Rob another player for Gold (3 attempts per day)")
@app_commands.describe(target="The player to rob")
async def cmd_rob(interaction: discord.Interaction, target: discord.Member) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    guild_id = interaction.guild.id
    robber_id = interaction.user.id
    target_id = target.id
    today = get_today_myt()

    # Validation 1: Cannot rob yourself
    if robber_id == target_id:
        await interaction.response.send_message("❌ You cannot rob yourself!", ephemeral=True)
        return

    # Validation 2: Check and reset rob_count if new day
    rob_count = reset_rob_count_if_new_day(guild_id, robber_id, today)

    # Validation 3: Check daily limit (3 attempts per day)
    if rob_count >= 3:
        await interaction.response.send_message(
            "🛑 Heat is too high! You've already robbed 3 times today. Lay low until midnight.",
            ephemeral=True,
        )
        return

    # Validation 4: Check target has Gold > 0
    target_gold = get_gold(guild_id, target_id)
    if target_gold <= 0:
        await interaction.response.send_message(
            "❌ Target is too broke. Even Ah Long is looking for them. Pick someone else!",
            ephemeral=True,
        )
        return

    # Increment rob_count
    update_rob_data(guild_id, robber_id, today, rob_count + 1)

    # Execute robbery with 40% success rate
    success = random.random() < 0.40
    robber_gold = get_gold(guild_id, robber_id)

    if success:
        # Success: Steal 20-30% of target's Gold
        steal_percentage = random.uniform(0.20, 0.30)
        stolen_amount = int(target_gold * steal_percentage)
        
        # Transfer Gold
        add_gold(guild_id, target_id, -stolen_amount)
        add_gold(guild_id, robber_id, stolen_amount)
        
        new_robber_gold = get_gold(guild_id, robber_id)
        new_target_gold = get_gold(guild_id, target_id)
        
        await interaction.response.send_message(
            f"💰 **Robbery Successful!**\n"
            f"{interaction.user.mention} stole **{stolen_amount:,} 💰** from {target.mention}!\n\n"
            f"**{interaction.user.display_name}**: {robber_gold:,} → **{new_robber_gold:,} 💰**\n"
            f"**{target.display_name}**: {target_gold:,} → **{new_target_gold:,} 💰**"
        )
    else:
        # Failure: Lose 10-15% of robber's Gold as penalty
        penalty_percentage = random.uniform(0.10, 0.15)
        penalty_amount = int(abs(robber_gold) * penalty_percentage)
        
        # Deduct penalty
        add_gold(guild_id, robber_id, -penalty_amount)
        
        new_robber_gold = get_gold(guild_id, robber_id)
        
        await interaction.response.send_message(
            f"🚨 **Robbery Failed!**\n"
            f"{interaction.user.mention} got caught trying to rob {target.mention}!\n\n"
            f"**Penalty**: -{penalty_amount:,} 💰\n"
            f"**{interaction.user.display_name}**: {robber_gold:,} → **{new_robber_gold:,} 💰**"
        )


# ---------------------------------------------------------------------------
# Command: !balance
# ---------------------------------------------------------------------------

@bot.tree.command(name="balance", description="Check your own or another player's Gold balance")
@app_commands.describe(member="The player to check — leave blank for yourself")
async def cmd_balance(interaction: discord.Interaction, member: discord.Member = None) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    target = member or interaction.user
    gold   = get_gold(interaction.guild.id, target.id)
    color  = discord.Color.green() if gold >= 0 else discord.Color.red()
    embed  = discord.Embed(
        title=f"💰 {target.display_name}'s Balance",
        description=f"**{gold:,} 💰**" + (" *(💸 In Debt!)*" if gold < 0 else ""),
        color=color,
    )
    await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Command: !leaderboard
# ---------------------------------------------------------------------------

@bot.tree.command(name="leaderboard", description="Top 10 richest players in this server")
async def cmd_leaderboard(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    await interaction.response.defer()

    rows = db.execute(
        "SELECT user_id, gold FROM user_gold WHERE guild_id = ? ORDER BY gold DESC LIMIT 10",
        (interaction.guild.id,),
    ).fetchall()

    if not rows:
        await interaction.followup.send("No Gold records yet in this server.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines  = []
    for i, (uid, gold) in enumerate(rows):
        # get_member only hits the cache; fetch_member makes an API call for misses
        member = interaction.guild.get_member(uid)
        if member is None:
            try:
                member = await interaction.guild.fetch_member(uid)
            except (discord.NotFound, discord.HTTPException):
                member = None
        name  = member.display_name if member else f"Unknown User ({uid})"
        medal = medals[i] if i < 3 else f"**{i + 1}.**"
        sign  = "+" if gold > 0 else ""
        lines.append(f"{medal} **{name}** — {sign}{gold:,} 💰")

    embed = discord.Embed(
        title=f"🏆 {interaction.guild.name} Leaderboard",
        description="\n".join(lines),
        color=discord.Color.gold(),
    )
    await interaction.followup.send(embed=embed)


# ---------------------------------------------------------------------------
# Command: /disclaimer
# ---------------------------------------------------------------------------

@bot.tree.command(name="disclaimer", description="Legal disclaimer — game tokens, no real money")
async def cmd_disclaimer(interaction: discord.Interaction) -> None:
    embed = discord.Embed(
        title="⚠️ Legal Disclaimer",
        color=discord.Color.dark_grey(),
    )
    embed.add_field(
        name="Not Gambling",
        value=(
            "This bot provides **entertainment-only** games. "
            "This is **not gambling** — no real money is involved."
        ),
        inline=False,
    )
    embed.add_field(
        name="Gold & Tokens",
        value=(
            "**Gold** 💰 and **Tokens** 🪙 are **game tokens only**. "
            "They have **no monetary value** and cannot be exchanged for real currency. "
            "There is **no real-money purchase** system — you cannot buy Gold or Tokens with cash. "
            "There is **no real-money withdrawal** — you cannot cash out or convert tokens to money."
        ),
        inline=False,
    )
    embed.add_field(
        name="Stay Away from Illegal Gambling",
        value=(
            "We encourage everyone to **stay away from illegal gambling**. "
            "If you or someone you know struggles with gambling addiction, please seek help. "
            "For support, visit: [BeGambleAware.org](https://www.begambleaware.org/) | "
            "[Gamblers Anonymous](https://www.gamblersanonymous.org/)"
        ),
        inline=False,
    )
    embed.add_field(
        name="No Liability",
        value=(
            "This bot is provided \"as is\" for entertainment purposes. "
            "The developers assume no liability for any misuse or misunderstanding. "
            "By using this bot, you agree that you understand these terms."
        ),
        inline=False,
    )
    embed.set_footer(text="For entertainment only — no real money involved")
    await interaction.response.send_message(embed=embed)


# ---------------------------------------------------------------------------
# Debt Confirmation View — !ht
# ---------------------------------------------------------------------------

class HTDebtConfirmView(ui.View):
    """
    Shown when a player with ≤ 0 Gold uses !ht.
    The coin is NOT flipped until they explicitly confirm.
    """

    def __init__(self, guild_id: int, uid: int, bet_amount: int, choice: str) -> None:
        super().__init__(timeout=60)
        self.guild_id   = guild_id
        self.uid        = uid
        self.bet_amount = bet_amount
        self.choice     = choice
        self.done       = False
        self.message:   discord.Message | None = None

    def _check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.uid and not self.done

    @ui.button(label="▶️ Continue (30% interest applies)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not self._check(interaction):
            await interaction.response.send_message("This isn't your bet!", ephemeral=True)
            return

        # Check MAX_DEBT before executing
        current_gold = get_gold(self.guild_id, self.uid)
        projected_debt = current_gold - int(self.bet_amount * 1.3)
        if projected_debt < MAX_DEBT:
            await interaction.response.send_message(
                f"🚫 Ah Long credit limit reached! Maximum debt is {MAX_DEBT:,} 💰. Your projected debt would be {projected_debt:,} 💰.",
                ephemeral=True
            )
            return

        self.done = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]

        guild_id   = self.guild_id
        uid        = self.uid
        bet_amount = self.bet_amount
        choice     = self.choice

        add_gold(guild_id, uid, -bet_amount)
        result = random.choice(("heads", "tails"))
        won    = (result == choice)

        if won:
            tax        = math.ceil(bet_amount * DEBT_TAX_RATE)
            net_profit = bet_amount - tax
            add_gold(guild_id, uid, bet_amount + net_profit)
            new_gold = get_gold(guild_id, uid)
            outcome  = (
                f"🪙 Coin landed **{result}** — you guessed right! "
                f"+**{net_profit:,}** 💰 profit\n"
                f"🏦 **{tax:,}** Gold interest deducted!\n"
                f"Balance: **{new_gold:,} 💰**"
            )
        else:
            new_gold = get_gold(guild_id, uid)
            outcome  = (
                f"🪙 Coin landed **{result}** — wrong guess. "
                f"-**{bet_amount:,}** 💰\n"
                f"Balance: **{new_gold:,} 💰**"
            )
            if new_gold <= 0:
                outcome += "\n💸 Broke? Use `/daily` to claim your daily Gold!"

        await interaction.response.edit_message(content=outcome, embed=None, view=self)

    @ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not self._check(interaction):
            await interaction.response.send_message("This isn't your bet!", ephemeral=True)
            return

        self.done = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            content="❌ Bet cancelled — no Gold was deducted.", embed=None, view=self
        )

    async def on_timeout(self) -> None:
        if self.done:
            return
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(
                    content="⏰ Confirmation timed out — bet cancelled.", embed=None, view=self
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Command: !ht (Heads or Tails)
# ---------------------------------------------------------------------------

@bot.tree.command(name="ht", description="Flip a coin — guess heads or tails")
@app_commands.describe(
    choice="Pick heads or tails",
    bet="Gold to wager (leave blank or 0 for free play with tokens)",
)
async def cmd_cointoss(
    interaction: discord.Interaction,
    choice: Literal["heads", "tails"],
    bet: int = 0,
) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    guild_id   = interaction.guild.id
    uid        = interaction.user.id
    free_play  = (bet == 0)
    bet_amount = bet

    if not free_play and bet_amount <= 0:
        await interaction.response.send_message("Bet amount must be greater than 0.", ephemeral=True)
        return

    # ── Economic Firewall Validation ──────────────────────────────────────────
    if not free_play:
        is_valid, error_msg = validate_bet_with_firewall(guild_id, uid, bet_amount)
        if not is_valid:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

    # ── Free Play (tokens) ────────────────────────────────────────────────────
    if free_play:
        add_tokens(uid, -FREE_PLAY_TOKEN_BET)
        result = random.choice(("heads", "tails"))
        won    = (result == choice)

        if won:
            add_tokens(uid, FREE_PLAY_TOKEN_BET * 2)
        new_bal = get_tokens(uid)
        outcome = (
            f"🪙 Coin landed **{result}** — "
            + (f"you guessed right! +**1** 🪙" if won else "wrong guess. -**1** 🪙")
            + f"\n🎮 Token Balance: **{new_bal:,} 🪙**"
        )
        await interaction.response.send_message(outcome)
        return

    # ── Staked (Gold) ─────────────────────────────────────────────────────────
    gold_before = get_gold(guild_id, uid)
    in_debt     = gold_before <= 0

    if in_debt:
        # Show warning embed with Continue / Cancel buttons — do NOT flip yet
        embed = discord.Embed(
            title="⚠️ You're in Debt!",
            description=DEBT_WARNING,
            color=discord.Color.red(),
        )
        embed.add_field(name="Current Balance", value=f"**{gold_before:,} 💰**", inline=True)
        embed.add_field(name="Bet", value=f"**{bet_amount:,} 💰** on **{choice}**", inline=True)
        view = HTDebtConfirmView(guild_id, uid, bet_amount, choice)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        view.message = msg
        return

    # No debt — execute flip immediately
    add_gold(guild_id, uid, -bet_amount)
    result: str = random.choice(("heads", "tails"))
    won: bool   = (result == choice)

    if won:
        add_gold(guild_id, uid, bet_amount * 2)
        new_gold = get_gold(guild_id, uid)
        outcome  = (
            f"🪙 Coin landed **{result}** — you guessed right! +**{bet_amount:,}** 💰 profit\n"
            f"Balance: **{new_gold:,} 💰**"
        )
    else:
        new_gold = get_gold(guild_id, uid)
        outcome  = (
            f"🪙 Coin landed **{result}** — wrong guess. -**{bet_amount:,}** 💰\n"
            f"Balance: **{new_gold:,} 💰**"
        )
        if new_gold <= 0:
            outcome += "\n💸 Broke? Use `/daily` to claim your daily Gold!"

    await interaction.response.send_message(outcome)


# ---------------------------------------------------------------------------
# Ban-Luck — In-Memory Game State
# ---------------------------------------------------------------------------

active_tables:     dict[int, GameTable] = {}
active_player_ids: set[int]             = set()


# ---------------------------------------------------------------------------
# Ban-Luck — Pure Helpers (no Discord I/O)
# ---------------------------------------------------------------------------

def _status_tag(status: str, escaped: bool = False) -> str:
    """Small emoji tag appended to a player's name on the board."""
    if escaped:
        return " 🏃"
    return {"stood": " 🛑", "bust": " 💥", "special": " 🏅"}.get(status, "")


def _calc_payout(
    player: PlayerState,
    banker: PlayerState,
    bet:    int,
) -> tuple[int, str]:
    """
    Calculate Gold returned to a single player after the game ends.
    The player's bet was already deducted at game start.

    Return value (payout) semantics:
      payout > bet  → player won
      payout == bet → push (stake returned)
      payout == 0   → player lost their bet
      payout < 0    → player owes extra (banker had a special; payout = -(extra))

    Debt interest is NOT applied here — caller handles it via _apply_debt_tax().
    """
    p_hand = player.hand
    b_hand = banker.hand

    if p_hand.is_bust:
        return 0, "💥 Bust — lost"

    p_special = p_hand.special
    b_special = b_hand.special

    # Clash Rule: both have special hands
    if p_special and b_special:
        p_mult = p_special[1]
        b_mult = b_special[1]
        if p_mult > b_mult:
            payout = int(bet * (1 + p_mult))
            return payout, f"🏆 Clash! {p_special[0]} beats {b_special[0]} — **+{payout - bet:,}** 💰"
        if b_mult > p_mult:
            extra = int(bet * (b_mult - 1))
            return -extra, f"❌ Clash! {b_special[0]} beats {p_special[0]} — lost **{int(b_mult)}x**"
        return bet, "🤝 Clash! Equal specials — push"

    # Only player has special
    if p_special:
        payout = int(bet * (1 + p_special[1]))
        return payout, f"🏆 {p_special[0]} — **+{payout - bet:,}** 💰"

    # Only banker has special — banker's multiplier applies;
    # player owes (mult × bet) total; they already paid 1× at game start.
    if b_special:
        mult  = b_special[1]
        extra = int(bet * (mult - 1))
        return -extra, f"❌ Banker has {b_special[0]} — lost **{int(mult)}x**"

    if b_hand.is_bust:
        return bet * 2, "🏆 Banker busted — won"

    if p_hand.score > b_hand.score:
        return bet * 2, f"🏆 Won ({p_hand.score} vs {b_hand.score})"
    if p_hand.score < b_hand.score:
        return 0, f"❌ Lost ({p_hand.score} vs {b_hand.score})"
    return bet, f"🤝 Push ({p_hand.score} each)"


def _apply_debt_tax(payout: int, bet: int, desc: str) -> tuple[int, str]:
    """
    Apply 30% ceiling tax on net profit for an in-debt player.
    Only modifies payout when net_profit > 0 (player actually won).
    Returns (adjusted_payout, updated_description).
    """
    net_profit = payout - bet
    if net_profit <= 0:
        return payout, desc
    tax      = math.ceil(net_profit * DEBT_TAX_RATE)
    adjusted = payout - tax
    return adjusted, desc + f"\n  🏦 **{tax:,}** Gold interest deducted!"


def _cleanup_table(table: GameTable) -> None:
    active_tables.pop(table.banker_id, None)
    active_player_ids.difference_update(table.all_player_ids)


def _check_active(uid: int, guild_id: int) -> bool:
    """Return True only if uid is genuinely in a live table/lobby in this guild.
    Removes stale active_player_ids entries caused by restarts or missed cleanups."""
    if uid not in active_player_ids:
        return False
    for table in active_tables.values():
        if table.guild_id == guild_id and any(p.user_id == uid for p in table.all_participants):
            return True
    active_player_ids.discard(uid)
    return False


async def _ping_turn(interaction: discord.Interaction, table: GameTable) -> None:
    """
    Send a NEW (non-ephemeral) channel message pinging the current player.
    A fresh message is required so Discord actually notifies them.
    """
    current = table.current_participant
    if current is None or table.phase != "playing":
        return
    is_banker = current.user_id == table.banker_id
    role      = " (庄家 Banker)" if is_banker else ""
    await interaction.followup.send(
        f"▶️ <@{current.user_id}>{role} it's your turn!", ephemeral=False
    )


# ---------------------------------------------------------------------------
# Ban-Luck — Shared Payout Settlement Helpers
# ---------------------------------------------------------------------------

def _settle_staked(table: GameTable) -> tuple[list[str], int]:
    """
    Settle all player payouts for a Gold-staked game.
    Applies 30% debt tax for in-debt players who won.
    Escaped players' bets were already refunded in GameView.escape — skip them.

    Returns (result_lines, banker_net).
    banker_net is used by the caller to compute: banker_total = escrow + banker_net.
    """
    banker     = table.banker
    banker_net = 0
    lines: list[str] = []

    for player in table.players:
        if player.escaped:
            if getattr(player, 'quit', False):
                lines.append(f"**{player.name}**: 🚪 Quit (bet forfeited to banker)")
            else:
                lines.append(f"**{player.name}**: 🏃 Escaped (bet refunded)")
            # bet already handled in quit/escape handler — do not add_gold again
            continue

        payout, desc    = _calc_payout(player, banker, table.bet)
        original_payout = payout   # banker settles on the pre-tax amount

        if player.in_debt:
            payout, desc = _apply_debt_tax(payout, table.bet, desc)

        add_gold(table.guild_id, player.user_id, payout)
        lines.append(f"**{player.name}**: {desc}")
        # Banker's profit/loss is based on the original (pre-tax) payout;
        # the tax is a house take that exits the economy.
        banker_net += (table.bet - original_payout)

    return lines, banker_net


def _settle_free_play(table: GameTable) -> tuple[list[str], list[str]]:
    """
    Settle all player payouts for a free-play (token) game.
    No Gold changes, no debt tax.

    Returns (result_lines, token_balance_lines).
    """
    banker         = table.banker
    tok_banker_net = 0
    lines:     list[str] = []
    tok_lines: list[str] = []

    for player in table.players:
        if player.escaped:
            lines.append(f"**{player.name}**: 🏃 Escaped")
            tok_lines.append(f"**{player.name}**: 🏃 Escaped")
            continue

        tok_ret, desc = _calc_payout(player, banker, FREE_PLAY_TOKEN_BET)
        tok_delta     = tok_ret - FREE_PLAY_TOKEN_BET
        add_tokens(player.user_id, tok_delta)
        tok_banker_net -= tok_delta
        sign    = "+" if tok_delta >= 0 else ""
        new_bal = get_tokens(player.user_id)
        lines.append(f"**{player.name}**: {desc}")
        tok_lines.append(f"**{player.name}**: {sign}{tok_delta:,} 🪙 → **{new_bal:,}**")

    add_tokens(table.banker_id, tok_banker_net)
    b_sign = "+" if tok_banker_net >= 0 else ""
    b_bal  = get_tokens(table.banker_id)
    lines.append(f"**{banker.name} (Banker)**: Net **{b_sign}{tok_banker_net:,}** tokens")
    tok_lines.append(
        f"**{banker.name} (Banker)**: {b_sign}{tok_banker_net:,} 🪙 → **{b_bal:,}**"
    )
    return lines, tok_lines


# ---------------------------------------------------------------------------
# Ban-Luck — Embed Builders
# ---------------------------------------------------------------------------

def build_lobby_embed(table: GameTable) -> discord.Embed:
    """Waiting-room embed shown while players join."""
    current   = len(table.players) + 1
    capacity  = table.MAX_PLAYERS + 1
    free_play = table.bet == 0

    if free_play:
        stakes_line = "🎮 **Free Play** (no Gold wagered)"
    else:
        stakes_line = (
            f"Bet: **{table.bet:,} 💰** each  |  "
            f"Banker escrow: **{table.banker_escrow:,} 💰** (covers 6× max payout)"
        )

    embed = discord.Embed(
        title="🃏  Ban-Luck — Lobby",
        description=f"{stakes_line}  |  Players: **{current} / {capacity}**",
        color=discord.Color.blue() if not free_play else discord.Color.teal(),
    )
    embed.add_field(name="👑 Banker", value=table.banker_name, inline=False)

    plist = (
        "\n".join(f"{i+1}. {p.name}" for i, p in enumerate(table.players))
        if table.players else "*Waiting for players...*"
    )
    embed.add_field(name="Players", value=plist, inline=False)
    embed.set_footer(text=f"Banker can start early | Auto-starts at {capacity} players")
    return embed


def build_board_embed(table: GameTable, *, reveal: bool = False) -> discord.Embed:
    """
    Public game board. Cards are hidden while in progress.
    Players see their own full hand via 🃏 My Cards (ephemeral).
    """
    free_play = table.bet == 0
    embed = discord.Embed(
        title="🃏  Ban-Luck" + (" — Free Play 🎮" if free_play else ""),
        description="🎮 No Gold wagered" if free_play else f"Bet: **{table.bet:,} 💰** each",
        color=discord.Color.gold() if reveal else (
            discord.Color.teal() if free_play else discord.Color.green()
        ),
    )

    current = table.current_participant

    for p in table.all_participants:
        is_banker = (p.user_id == table.banker_id)
        is_active = (p is current) and not reveal
        prefix    = "▶️ " if is_active else ("👑 " if is_banker else "")
        label     = (
            f"{prefix}{p.name}{' (Banker)' if is_banker else ''}"
            f"{_status_tag(p.status, p.escaped)}"
        )
        if p.escaped:
            value = "*Escaped 🏃*"
        elif table.phase in ("playing", "finished"):
            value = p.hand.show(hide_all=not reveal)
        else:
            value = "—"
        embed.add_field(name=label, value=value or "—", inline=False)

    if not reveal and table.phase == "playing" and current:
        embed.set_footer(
            text=f"▶️ {current.name}'s turn  |  Bet: {table.bet:,} 💰 each"
        )
    return embed


# ---------------------------------------------------------------------------
# Ban-Luck — Resolve Table (payout + cleanup)
# ---------------------------------------------------------------------------

async def resolve_table(
    table:       GameTable,
    game_view:   "GameView",
    interaction: discord.Interaction,
) -> None:
    """
    Calculate payouts, update Gold/tokens, then:
      1. Edit the board message to reveal all cards with disabled buttons.
      2. Send a NEW channel message with the full result breakdown.
      3. Attach a RematchView to the results message.
    """
    table.phase = "finished"
    banker      = table.banker
    free_play   = table.bet == 0

    if free_play:
        lines, tok_lines = _settle_free_play(table)
    else:
        lines, banker_net = _settle_staked(table)
        banker_total      = table.banker_escrow + banker_net
        add_gold(table.guild_id, table.banker_id, max(0, banker_total))
        net_str = f"+{banker_net:,}" if banker_net >= 0 else f"{banker_net:,}"
        lines.append(f"**{banker.name} (Banker)**: Net **{net_str}** 💰")
        tok_lines = None

    # Edit board to reveal cards + disable all buttons
    board_embed = build_board_embed(table, reveal=True)
    for item in game_view.children:
        item.disabled = True  # type: ignore[attr-defined]
    await interaction.response.edit_message(content="", embed=board_embed, view=game_view)

    # New channel message with results + everyone mentioned
    all_mentions = " ".join(f"<@{p.user_id}>" for p in table.all_participants)
    result_embed = discord.Embed(
        title="🏁  Game Over — Results",
        description=f"{all_mentions}\n\n" + "\n".join(lines),
        color=discord.Color.blurple(),
    )
    if free_play and tok_lines:
        result_embed.add_field(
            name="🪙 Token Balances",
            value="\n".join(tok_lines),
            inline=False,
        )
        result_embed.set_footer(
            text=f"Free Play — bet {FREE_PLAY_TOKEN_BET} tokens each | use !tokens to check balance"
        )
    else:
        result_embed.set_footer(text=f"Bet: {table.bet:,} 💰 each")

    participants = [(p.user_id, p.name) for p in table.all_participants]
    rematch_view = RematchView(
        bet=table.bet, guild_id=table.guild_id, participants=participants
    )

    _cleanup_table(table)
    await interaction.followup.send(embed=result_embed, view=rematch_view, ephemeral=False)


# ---------------------------------------------------------------------------
# Ban-Luck — GameView  (Hit / Stand / Escape / My Cards)
# ---------------------------------------------------------------------------

class GameView(ui.View):
    """
    Attached to the public game board.
    Hit / Stand / Escape are turn-gated (only the current player).
    My Cards is available to any participant at any time — ephemeral response.
    """

    def __init__(self, table: GameTable) -> None:
        super().__init__(timeout=300)
        self.table   = table
        self.message: discord.Message | None = None
        self._turn_task: asyncio.Task | None = None

    # --- Turn timer ---------------------------------------------------------

    def _cancel_turn_timer(self) -> None:
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self._turn_task = None

    def _start_turn_timer(self, channel: discord.abc.Messageable) -> None:
        self._cancel_turn_timer()
        table = self.table
        if table.phase != "playing" or table.current_participant is None:
            return
        uid = table.current_participant.user_id
        self._turn_task = asyncio.create_task(self._auto_act(channel, uid))

    async def _auto_act(self, channel: discord.abc.Messageable, expected_uid: int) -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return
        table = self.table
        if table.phase != "playing":
            return
        current = table.current_participant
        if current is None or current.user_id != expected_uid:
            return
        current.status = "stood"
        should_resolve = table.advance()
        await channel.send(f"⏰ <@{expected_uid}> timed out — auto-stood!")
        if should_resolve:
            await self._resolve_no_interaction(channel)
        else:
            nxt = table.current_participant
            embed = build_board_embed(table)
            if nxt:
                embed.set_footer(text=f"⏰ {current.name} timed out  |  Now: {nxt.name}")
            if self.message:
                try:
                    await self.message.delete()
                except Exception:
                    pass
            self.message = await channel.send(embed=embed, view=self)
            if nxt:
                await channel.send(f"▶️ <@{nxt.user_id}> it's your turn!")
            self._start_turn_timer(channel)

    async def _resolve_no_interaction(self, channel: discord.abc.Messageable) -> None:
        table = self.table
        table.phase = "finished"
        free_play = table.bet == 0
        if free_play:
            lines, tok_lines = _settle_free_play(table)
        else:
            lines, banker_net = _settle_staked(table)
            banker_total = table.banker_escrow + banker_net
            add_gold(table.guild_id, table.banker_id, max(0, banker_total))
            net_str = f"+{banker_net:,}" if banker_net >= 0 else f"{banker_net:,}"
            lines.append(f"**{table.banker.name} (Banker)**: Net **{net_str}** 💰")
            tok_lines = None
        board_embed = build_board_embed(table, reveal=True)
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(content="", embed=board_embed, view=self)
            except Exception:
                pass
        all_mentions = " ".join(f"<@{p.user_id}>" for p in table.all_participants)
        result_embed = discord.Embed(
            title="🏁  Game Over — Results",
            description=f"{all_mentions}\n\n" + "\n".join(lines),
            color=discord.Color.blurple(),
        )
        if free_play and tok_lines:
            result_embed.add_field(name="🪙 Token Balances", value="\n".join(tok_lines), inline=False)
            result_embed.set_footer(text=f"Free Play — bet {FREE_PLAY_TOKEN_BET} tokens each")
        else:
            result_embed.set_footer(text=f"Bet: {table.bet:,} 💰 each")
        participants = [(p.user_id, p.name) for p in table.all_participants]
        rematch_view = RematchView(bet=table.bet, guild_id=table.guild_id, participants=participants)
        _cleanup_table(table)
        await channel.send(embed=result_embed, view=rematch_view)

    # --- Hit ----------------------------------------------------------------

    @ui.button(label="🎯 Hit", style=discord.ButtonStyle.primary, row=0)
    async def hit(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table   = self.table
        current = table.current_participant

        if current is None or interaction.user.id != current.user_id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return

        self._cancel_turn_timer()
        card = table.deck.deal()
        current.hand.add(card)

        special = current.hand.special

        auto_end = current.hand.is_bust or (
            special is not None
            and not any(kw in special[0] for kw in ("Ban-Luck", "Ban-Ban", "Double"))
        )

        if current.hand.is_bust:
            current.status = "bust"
        elif auto_end and special:
            current.status = "special"

        should_resolve = table.advance() if auto_end else False

        followup_txt = f"Your updated hand:\n{current.hand.show()}"
        if special:
            followup_txt += f"\n🎉 **{special[0]}**!"
        if current.hand.must_hit and not auto_end:
            followup_txt += "\n*(score < 16 — you must keep hitting)*"

        if should_resolve:
            await resolve_table(table, self, interaction)
        else:
            embed = build_board_embed(table)
            if auto_end:
                nxt = table.current_participant
                tag = "💥 Bust" if current.hand.is_bust else f"🏅 {special[0] if special else 'Auto-stand'}"
                embed.set_footer(text=f"{tag} — {current.name}  |  Now: {nxt.name if nxt else '?'}")
            # Defer, delete old board, resend at bottom so players never scroll up
            await interaction.response.defer()
            if self.message:
                try:
                    await self.message.delete()
                except Exception:
                    pass
            self.message = await interaction.channel.send(embed=embed, view=self)
            if auto_end:
                await _ping_turn(interaction, table)
            self._start_turn_timer(interaction.channel)

        buf = await render_hand_image(current.hand)
        if buf:
            await interaction.followup.send(
                followup_txt, file=discord.File(buf, "hand.png"), ephemeral=True
            )
        else:
            await interaction.followup.send(followup_txt, ephemeral=True)

    # --- Stand  (blocked if score < 16) -------------------------------------

    @ui.button(label="🛑 Stand", style=discord.ButtonStyle.secondary, row=0)
    async def stand(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table   = self.table
        current = table.current_participant

        if current is None or interaction.user.id != current.user_id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return

        self._cancel_turn_timer()

        if current.hand.must_hit and not current.hand.special:
            await interaction.response.send_message(
                f"❌ You must Hit — your score is **{current.hand.score}** (minimum 16 required).",
                ephemeral=True,
            )
            return

        current.status = "stood"
        should_resolve = table.advance()

        if should_resolve:
            await resolve_table(table, self, interaction)
        else:
            nxt   = table.current_participant
            embed = build_board_embed(table)
            embed.set_footer(
                text=f"✋ {current.name} stands  |  Now: {nxt.name if nxt else '?'}"
            )
            await interaction.response.defer()
            if self.message:
                try:
                    await self.message.delete()
                except Exception:
                    pass
            self.message = await interaction.channel.send(embed=embed, view=self)
            await _ping_turn(interaction, table)
            self._start_turn_timer(interaction.channel)

    # --- Escape (🏃 走) — only on initial 2-card deal with 15 or 16 ---------

    @ui.button(label="🏃 Escape (走)", style=discord.ButtonStyle.danger, row=0)
    async def escape(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table   = self.table
        current = table.current_participant

        if current is None or interaction.user.id != current.user_id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return

        self._cancel_turn_timer()

        if not current.hand.can_escape:
            await interaction.response.send_message(
                "Escape (走) is only available on your initial 2-card deal "
                "when your score is exactly 15 or 16.",
                ephemeral=True,
            )
            return

        # Refund bet and mark as escaped
        add_gold(table.guild_id, current.user_id, table.bet)
        current.escaped = True
        should_resolve  = table.advance()

        if should_resolve:
            await resolve_table(table, self, interaction)
        else:
            nxt   = table.current_participant
            embed = build_board_embed(table)
            embed.set_footer(
                text=f"🏃 {current.name} escaped (bet refunded)  |  Now: {nxt.name if nxt else '?'}"
            )
            await interaction.response.defer()
            if self.message:
                try:
                    await self.message.delete()
                except Exception:
                    pass
            self.message = await interaction.channel.send(embed=embed, view=self)
            await _ping_turn(interaction, table)
            self._start_turn_timer(interaction.channel)

    # --- My Cards (ephemeral — only you can see this) -----------------------

    @ui.button(label="🃏 My Cards", style=discord.ButtonStyle.grey, row=1)
    async def my_cards(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table = self.table
        uid   = interaction.user.id

        participant = next(
            (p for p in table.all_participants if p.user_id == uid), None
        )
        if participant is None:
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return

        if not participant.hand.cards:
            await interaction.response.send_message("No cards dealt yet.", ephemeral=True)
            return

        content = f"**Your hand:**\n{participant.hand.show()}"
        buf     = await render_hand_image(participant.hand)
        if buf:
            await interaction.response.send_message(
                content, file=discord.File(buf, "hand.png"), ephemeral=True
            )
        else:
            await interaction.response.send_message(content, ephemeral=True)

    # --- Refresh (repost board + reset 5-min button timer) ------------------

    @ui.button(label="🔄 Refresh", style=discord.ButtonStyle.grey, row=1)
    async def refresh(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table = self.table
        uid   = interaction.user.id

        if table.phase == "finished":
            await interaction.response.send_message("This game has already ended.", ephemeral=True)
            return

        if not any(p.user_id == uid for p in table.all_participants):
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return

        # Create a fresh GameView so the 5-minute button timeout resets
        new_view         = GameView(table)
        new_view.message = None
        self.stop()  # cancel old view's timeout (won't trigger on_timeout)

        await interaction.response.defer()
        if self.message:
            try:
                await self.message.delete()
            except Exception:
                pass

        new_view.message = await interaction.channel.send(
            embed=build_board_embed(table), view=new_view
        )

    # --- Quit (player forfeits bet to banker) --------------------------------

    @ui.button(label="🚪 Quit", style=discord.ButtonStyle.danger, row=2)
    async def quit_game(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table = self.table
        uid   = interaction.user.id

        if uid == table.banker_id:
            await interaction.response.send_message(
                "Banker cannot quit — use 🏳️ End Game instead.", ephemeral=True
            )
            return

        player = next((p for p in table.players if p.user_id == uid and not p.escaped), None)
        if player is None:
            await interaction.response.send_message("You're not an active player.", ephemeral=True)
            return

        if table.phase != "playing":
            await interaction.response.send_message("Game is not in progress.", ephemeral=True)
            return

        is_current = table.current_participant is not None and table.current_participant.user_id == uid
        if is_current:
            self._cancel_turn_timer()

        # Forfeit bet to banker
        free_play = table.bet == 0
        if free_play:
            add_tokens(table.banker_id, FREE_PLAY_TOKEN_BET)
        else:
            add_gold(table.guild_id, table.banker_id, table.bet)

        player.escaped = True
        setattr(player, 'quit', True)
        active_player_ids.discard(uid)

        active_after = [p for p in table.players if not p.escaped]
        if not active_after:
            await resolve_table(table, self, interaction)
            return

        should_resolve = table.advance() if is_current else False

        if should_resolve:
            await resolve_table(table, self, interaction)
        else:
            embed = build_board_embed(table)
            await interaction.response.defer()
            if self.message:
                try:
                    await self.message.delete()
                except Exception:
                    pass
            self.message = await interaction.channel.send(embed=embed, view=self)
            await interaction.followup.send(
                f"🚪 **{interaction.user.display_name}** quit — bet forfeited to banker.",
                ephemeral=False,
            )
            if is_current:
                nxt = table.current_participant
                if nxt:
                    await interaction.followup.send(f"▶️ <@{nxt.user_id}> it's your turn!", ephemeral=False)
                self._start_turn_timer(interaction.channel)

    # --- End Game (banker surrenders escrow) ---------------------------------

    @ui.button(label="🏳️ End Game", style=discord.ButtonStyle.secondary, row=2)
    async def end_game(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table = self.table

        if interaction.user.id != table.banker_id:
            await interaction.response.send_message("Only the banker can end the game early.", ephemeral=True)
            return

        if table.phase != "playing":
            await interaction.response.send_message("Game is not in progress.", ephemeral=True)
            return

        self._cancel_turn_timer()
        table.phase = "finished"
        free_play   = table.bet == 0

        active_players = [p for p in table.players if not p.escaped]
        n = len(active_players)
        lines: list[str] = []

        for p in table.players:
            if p.escaped:
                label = "🚪 Already quit" if getattr(p, 'quit', False) else "🏃 Already escaped"
                lines.append(f"**{p.name}**: {label}")
                continue
            if free_play:
                add_tokens(p.user_id, FREE_PLAY_TOKEN_BET)
                lines.append(f"**{p.name}**: 🪙 Token returned")
            else:
                share = table.banker_escrow // n if n > 0 else 0
                add_gold(table.guild_id, p.user_id, table.bet + share)
                lines.append(f"**{p.name}**: Bet returned + **{share:,}** 💰 bonus from banker")

        if not free_play:
            remainder = table.banker_escrow % n if n > 0 else table.banker_escrow
            if remainder > 0:
                add_gold(table.guild_id, table.banker_id, remainder)
            lines.append(
                f"**{table.banker.name} (Banker)**: 🏳️ Forfeited **{table.banker_escrow:,}** 💰 escrow to players"
            )
        else:
            lines.append(f"**{table.banker.name} (Banker)**: 🏳️ Ended game early")

        board_embed = build_board_embed(table, reveal=True)
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]

        all_mentions = " ".join(f"<@{p.user_id}>" for p in table.all_participants)
        result_embed = discord.Embed(
            title="🏳️  Banker Ended Game Early",
            description=f"{all_mentions}\n\n" + "\n".join(lines),
            color=discord.Color.orange(),
        )
        _cleanup_table(table)
        await interaction.response.edit_message(content="", embed=board_embed, view=self)
        await interaction.followup.send(embed=result_embed, ephemeral=False)

    # --- Timeout ------------------------------------------------------------

    async def on_timeout(self) -> None:
        """Refund all non-escaped players + full banker escrow on timeout."""
        self._cancel_turn_timer()
        table = self.table
        if table.phase == "finished":
            return

        table.phase = "finished"
        for p in table.players:
            if not p.escaped:
                add_gold(table.guild_id, p.user_id, table.bet)
        add_gold(table.guild_id, table.banker_id, table.banker_escrow)

        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]

        _cleanup_table(table)
        if self.message:
            await self.message.edit(
                content="⏰ Game timed out — all players refunded.", view=self
            )


# ---------------------------------------------------------------------------
# Ban-Luck — RematchView  (🔄 Rematch — unanimous vote required)
# ---------------------------------------------------------------------------

class RematchView(ui.View):
    """
    All previous participants must click Rematch before anything happens.
    Once the last vote is cast the game auto-starts with a randomly chosen banker.
    """

    def __init__(
        self,
        bet:          int,
        guild_id:     int,
        participants: list[tuple[int, str]],
    ) -> None:
        super().__init__(timeout=120)
        self.bet             = bet
        self.guild_id        = guild_id
        self.participants    = participants
        self.participant_set = {uid for uid, _ in participants}
        self.votes:         set[int]                       = set()
        self.interactions:  dict[int, discord.Interaction] = {}
        self.started        = False

    @ui.button(label="🔄 Rematch", style=discord.ButtonStyle.green)
    async def rematch(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        uid   = interaction.user.id
        total = len(self.participants)

        if uid not in self.participant_set:
            await interaction.response.send_message(
                "Only players from the previous game can vote for rematch.", ephemeral=True
            )
            return

        if self.started:
            await interaction.response.send_message(
                "The rematch has already started!", ephemeral=True
            )
            return

        if _check_active(uid, self.guild_id):
            await interaction.response.send_message(
                "You're already in another game — can't join the rematch.", ephemeral=True
            )
            return

        if uid in self.votes:
            await interaction.response.send_message(
                "You've already voted ✅  Waiting for the others…", ephemeral=True
            )
            return

        self.votes.add(uid)
        self.interactions[uid] = interaction
        count = len(self.votes)
        button.label = f"🔄 Rematch ({count}/{total})"

        if count < total:
            waiting = [name for uid2, name in self.participants if uid2 not in self.votes]
            await interaction.response.edit_message(
                content=f"⏳ Waiting for **{', '.join(waiting)}** to vote…",
                view=self,
            )
            return

        # All voted — start the game
        self.started    = True
        button.disabled = True
        self.stop()

        num_others = total - 1

        # Anyone who isn't already in another game can be the banker — debt is allowed
        eligible = [
            (uid2, name)
            for uid2, name in self.participants
            if uid2 not in active_player_ids
        ]
        if not eligible:
            await interaction.response.edit_message(
                content="❌ Rematch cancelled — all players are already in another game.",
                embed=None, view=None,
            )
            return

        banker_id, banker_name = random.choice(eligible)

        table = GameTable(
            guild_id=self.guild_id,
            banker_id=banker_id,
            banker_name=banker_name,
            bet=self.bet,
        )
        for uid2, name in self.participants:
            if uid2 != banker_id and uid2 not in active_player_ids:
                table.add_player(uid2, name)

        active_tables[banker_id] = table
        for p in table.all_participants:
            active_player_ids.add(p.user_id)

        # Debt check BEFORE deducting bets
        debt_players: list[PlayerState] = []
        for p in table.players:
            if get_gold(self.guild_id, p.user_id) <= 0:
                p.in_debt = True
                debt_players.append(p)

        for p in table.players:
            add_gold(self.guild_id, p.user_id, -table.bet)
        add_gold(self.guild_id, banker_id, -table.banker_escrow)

        table.start()
        game_view = GameView(table)

        board_embed = build_board_embed(table)
        await interaction.response.edit_message(
            content=f"🔄 **Rematch!** New banker: **{banker_name}**",
            embed=board_embed,
            view=game_view,
        )
        game_view.message = interaction.message
        game_view._start_turn_timer(interaction.channel)

        # Debt warnings (public)
        for p in debt_players:
            stored = self.interactions.get(p.user_id)
            if stored:
                try:
                    await stored.followup.send(
                        f"<@{p.user_id}> {DEBT_WARNING}", ephemeral=False
                    )
                except Exception:
                    pass

        # Send each player their starting hand as an ephemeral
        for p in table.all_participants:
            stored = self.interactions.get(p.user_id)
            if stored is None:
                continue
            hand_txt = f"🃏 **Your starting hand:**\n{p.hand.show()}"
            buf = await render_hand_image(p.hand)
            try:
                if buf:
                    await stored.followup.send(
                        hand_txt, file=discord.File(buf, "hand.png"), ephemeral=True
                    )
                else:
                    await stored.followup.send(hand_txt, ephemeral=True)
            except Exception:
                pass

        first = table.current_participant
        if first:
            await interaction.followup.send(
                f"▶️ <@{first.user_id}> your turn first!", ephemeral=False
            )

    async def on_timeout(self) -> None:
        if self.started:
            return
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Debt Confirmation View — !bj Join
# ---------------------------------------------------------------------------

class BjDebtConfirmView(ui.View):
    """
    Shown (ephemeral) when a player with ≤ 0 Gold tries to join a staked lobby.
    They must explicitly confirm before being added to the table.
    """

    def __init__(self, lobby_view: "LobbyView", uid: int, name: str) -> None:
        super().__init__(timeout=60)
        self.lobby_view = lobby_view
        self.uid        = uid
        self.name       = name
        self.done       = False

    def _check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.uid and not self.done

    @ui.button(label="▶️ Continue (30% interest applies)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not self._check(interaction):
            await interaction.response.send_message("This isn't your confirmation!", ephemeral=True)
            return

        # Check MAX_DEBT before joining
        table = self.lobby_view.table
        current_gold = get_gold(table.guild_id, self.uid)
        projected_debt = current_gold - int(table.bet * 1.3)
        if projected_debt < MAX_DEBT:
            await interaction.response.send_message(
                f"🚫 Ah Long credit limit reached! Maximum debt is {MAX_DEBT:,} 💰. Your projected debt would be {projected_debt:,} 💰.",
                ephemeral=True
            )
            return

        self.done = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        # Acknowledge the button press (disables the buttons in the ephemeral)
        await interaction.response.edit_message(view=self)

        lv    = self.lobby_view
        table = lv.table
        uid   = self.uid

        if _check_active(uid, lv.table.guild_id):
            await interaction.followup.send("You're already in a game or lobby.", ephemeral=True)
            return

        if not table.add_player(uid, self.name):
            await interaction.followup.send(
                "Can't join — table is full or game already started.", ephemeral=True
            )
            return

        active_player_ids.add(uid)
        # Store THIS interaction so the game start can send the ephemeral card reveal
        lv.interactions[uid] = interaction

        if table.is_full:
            # interaction.response is already used — can't call _launch_game which
            # needs a fresh response to edit the lobby message.
            # Instead update the lobby message directly and ask the banker to start.
            if lv.message:
                try:
                    await lv.message.edit(embed=build_lobby_embed(table), view=lv)
                except Exception:
                    pass
            await interaction.followup.send(
                "✅ Joined! The table is now full — **banker, please click ▶️ Start Game**.",
                ephemeral=True,
            )
        else:
            if lv.message:
                try:
                    await lv.message.edit(embed=build_lobby_embed(table), view=lv)
                except Exception:
                    pass
            await interaction.followup.send("✅ Joined the lobby!", ephemeral=True)

    @ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not self._check(interaction):
            await interaction.response.send_message("This isn't your confirmation!", ephemeral=True)
            return

        self.done = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            content="❌ Cancelled — you did not join the lobby.", view=self
        )

    async def on_timeout(self) -> None:
        if self.done:
            return
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Ban-Luck — LobbyView  (Join / Start Game)
# ---------------------------------------------------------------------------

class LobbyView(ui.View):
    """
    Shown while the game is in the lobby phase.
    Anyone can join; only the banker can force-start.
    Auto-starts when full (5 players total).
    """

    def __init__(self, table: GameTable) -> None:
        super().__init__(timeout=120)
        self.table        = table
        self.resolved     = False
        self.message: discord.Message | None = None
        self.interactions: dict[int, discord.Interaction] = {}

    @ui.button(label="🪑 Join", style=discord.ButtonStyle.green)
    async def join(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table = self.table
        uid   = interaction.user.id

        if _check_active(uid, table.guild_id):
            await interaction.response.send_message(
                "You're already in a game or lobby.", ephemeral=True
            )
            return

        # Debt gate: show confirm/cancel view before adding to the table
        if table.bet > 0 and get_gold(table.guild_id, uid) <= 0:
            gold_now = get_gold(table.guild_id, uid)
            embed = discord.Embed(
                title="⚠️ You're in Debt!",
                description=DEBT_WARNING,
                color=discord.Color.red(),
            )
            embed.add_field(name="Current Balance", value=f"**{gold_now:,} 💰**", inline=True)
            embed.add_field(name="Bet to join", value=f"**{table.bet:,} 💰**", inline=True)
            confirm_view = BjDebtConfirmView(self, uid, interaction.user.display_name)
            await interaction.response.send_message(embed=embed, view=confirm_view, ephemeral=True)
            return

        if not table.add_player(uid, interaction.user.display_name):
            await interaction.response.send_message(
                "Can't join — table is full or game already started.", ephemeral=True
            )
            return

        active_player_ids.add(uid)
        self.interactions[uid] = interaction

        if table.is_full:
            await self._launch_game(interaction)
        else:
            await interaction.response.edit_message(
                embed=build_lobby_embed(table), view=self
            )

    @ui.button(label="🚪 Leave", style=discord.ButtonStyle.danger)
    async def leave(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table = self.table
        uid   = interaction.user.id

        # Banker disbands the whole lobby
        if uid == table.banker_id:
            if self.resolved:
                await interaction.response.send_message(
                    "The game has already started.", ephemeral=True
                )
                return

            self.resolved = True
            self.stop()
            _cleanup_table(table)

            for item in self.children:
                item.disabled = True  # type: ignore[attr-defined]

            await interaction.response.edit_message(
                embed=discord.Embed(
                    title="🚪 Lobby Disbanded",
                    description=f"**{interaction.user.display_name}** (banker) closed the lobby.",
                    color=discord.Color.dark_grey(),
                ),
                view=self,
            )
            return

        # Check the user is actually in this lobby
        if not any(p.user_id == uid for p in table.players):
            await interaction.response.send_message(
                "You're not in this lobby.", ephemeral=True
            )
            return

        # Remove from table and global tracking
        table.players = [p for p in table.players if p.user_id != uid]
        active_player_ids.discard(uid)
        self.interactions.pop(uid, None)

        await interaction.response.edit_message(
            embed=build_lobby_embed(table), view=self
        )
        await interaction.followup.send(
            f"👋 **{interaction.user.display_name}** left the lobby.", ephemeral=False
        )

    @ui.button(label="▶️ Start Game", style=discord.ButtonStyle.primary)
    async def start_game(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        if interaction.user.id != self.table.banker_id:
            await interaction.response.send_message("Only the banker can start.", ephemeral=True)
            return

        if len(self.table.players) == 0:
            await interaction.response.send_message(
                "Need at least 1 player before starting.", ephemeral=True
            )
            return

        self.interactions[interaction.user.id] = interaction
        await self._launch_game(interaction)

    async def _launch_game(self, interaction: discord.Interaction) -> None:
        """
        LOBBY → PLAYING:
        1. Debt-check every player (and banker) before deducting — warn if ≤ 0.
        2. Deduct bets + banker escrow (all parties may go into debt).
        3. Deal 2 cards, show game board.
        """
        if self.resolved:
            return

        self.resolved = True
        self.stop()

        table         = self.table
        banker_needed = table.banker_escrow

        # Debt check BEFORE deducting bets (only for staked games)
        debt_players: list[PlayerState] = []
        if banker_needed > 0:
            for p in table.players:
                if get_gold(table.guild_id, p.user_id) <= 0:
                    p.in_debt = True
                    debt_players.append(p)

            for p in table.players:
                add_gold(table.guild_id, p.user_id, -table.bet)
            add_gold(table.guild_id, table.banker_id, -banker_needed)

        table.start()
        game_view = GameView(table)

        if table.phase == "finished":
            await self._resolve_immediate(table, game_view, interaction)
            return

        embed = build_board_embed(table)
        await interaction.response.edit_message(content="", embed=embed, view=game_view)
        game_view.message = interaction.message
        game_view._start_turn_timer(interaction.channel)

        # Debt warnings for in-debt players (public so everyone sees)
        for p in debt_players:
            stored = self.interactions.get(p.user_id)
            if stored:
                try:
                    await stored.followup.send(
                        f"<@{p.user_id}> {DEBT_WARNING}", ephemeral=False
                    )
                except Exception:
                    pass

        # Send each participant their starting hand as an ephemeral
        for p in table.all_participants:
            stored = self.interactions.get(p.user_id)
            if stored is None:
                continue
            hand_txt = f"🃏 **Your starting hand:**\n{p.hand.show()}"
            buf = await render_hand_image(p.hand)
            try:
                if buf:
                    await stored.followup.send(
                        hand_txt, file=discord.File(buf, "hand.png"), ephemeral=True
                    )
                else:
                    await stored.followup.send(hand_txt, ephemeral=True)
            except Exception:
                pass

        no_ix = [p for p in table.all_participants if p.user_id not in self.interactions]
        fallback_mentions = " ".join(f"<@{p.user_id}>" for p in no_ix)
        if fallback_mentions:
            await interaction.followup.send(
                f"🃏 {fallback_mentions} — click **🃏 My Cards** to see your hand (only you can see it).",
                ephemeral=False,
            )

        first = table.current_participant
        if first:
            await interaction.followup.send(
                f"▶️ <@{first.user_id}> your turn first!", ephemeral=False
            )

    async def _resolve_immediate(
        self,
        table:       GameTable,
        game_view:   "GameView",
        interaction: discord.Interaction,
    ) -> None:
        """Immediate resolution when all turns end on the deal (edge case)."""
        banker    = table.banker
        free_play = table.bet == 0

        if free_play:
            lines, tok_lines = _settle_free_play(table)
        else:
            lines, banker_net = _settle_staked(table)
            banker_total      = table.banker_escrow + banker_net
            add_gold(table.guild_id, table.banker_id, max(0, banker_total))
            net_str = f"+{banker_net:,}" if banker_net >= 0 else f"{banker_net:,}"
            lines.append(f"**{banker.name} (Banker)**: Net **{net_str}** 💰")
            tok_lines = None

        board_embed = build_board_embed(table, reveal=True)
        for item in game_view.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(content="", embed=board_embed, view=game_view)

        all_mentions = " ".join(f"<@{p.user_id}>" for p in table.all_participants)
        result_embed = discord.Embed(
            title="🏁  Game Over — Results",
            description=f"{all_mentions}\n\n" + "\n".join(lines),
            color=discord.Color.blurple(),
        )
        if free_play and tok_lines:
            result_embed.add_field(
                name="🪙 Token Balances",
                value="\n".join(tok_lines),
                inline=False,
            )
            result_embed.set_footer(
                text=f"Free Play — bet {FREE_PLAY_TOKEN_BET} tokens each | use !tokens to check balance"
            )
        else:
            result_embed.set_footer(text=f"Bet: {table.bet:,} 💰 each")

        participants = [(p.user_id, p.name) for p in table.all_participants]
        rematch_view = RematchView(
            bet=table.bet, guild_id=table.guild_id, participants=participants
        )

        _cleanup_table(table)
        await interaction.followup.send(embed=result_embed, view=rematch_view, ephemeral=False)

    async def on_timeout(self) -> None:
        if self.resolved:
            return
        self.resolved = True
        _cleanup_table(self.table)
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            await self.message.edit(content="⏰ Lobby timed out.", embed=None, view=self)


# ---------------------------------------------------------------------------
# Command: !bj
# ---------------------------------------------------------------------------

@bot.tree.command(name="bj", description="Open a Ban-Luck (21) lobby as banker")
@app_commands.describe(bet="Gold bet per player (leave blank or 0 for free play with tokens)")
async def cmd_bj(interaction: discord.Interaction, bet: int = 0) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    uid      = interaction.user.id
    name     = interaction.user.display_name
    guild_id = interaction.guild.id

    if bet < 0:
        await interaction.response.send_message(
            "Bet cannot be negative. Use `/bj` for free play or `/bj <amount>` for stakes.",
            ephemeral=True,
        )
        return

    # Economic Firewall Validation (for Gold bets only)
    if bet > 0:
        is_valid, error_msg = validate_bet_with_firewall(guild_id, uid, bet)
        if not is_valid:
            await interaction.response.send_message(error_msg, ephemeral=True)
            return

    if _check_active(uid, guild_id):
        await interaction.response.send_message(
            "You already have an open lobby or are in a game.", ephemeral=True
        )
        return

    table = GameTable(guild_id=guild_id, banker_id=uid, banker_name=name, bet=bet)
    active_tables[uid]   = table
    active_player_ids.add(uid)

    view = LobbyView(table)

    # Warn banker if they're in debt (game still opens — debt is allowed)
    if bet > 0 and get_gold(guild_id, uid) <= 0:
        await interaction.response.send_message(f"<@{uid}> {DEBT_WARNING}")
        msg = await interaction.followup.send(embed=build_lobby_embed(table), view=view)
    else:
        await interaction.response.send_message(embed=build_lobby_embed(table), view=view)
        msg = await interaction.original_response()

    view.message = msg


@bot.tree.command(name="leave", description="Leave your current Ban-Luck lobby as a player")
async def cmd_leave(interaction: discord.Interaction) -> None:
    """Slash command fallback: leave an open Ban-Luck lobby without pressing the button."""
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return

    guild_id = interaction.guild.id
    uid      = interaction.user.id

    # Find a lobby in this guild where the user is a joined player (not banker)
    target_table: GameTable | None = None
    for table in active_tables.values():
        if table.guild_id != guild_id:
            continue
        if table.phase != "lobby":
            continue
        if any(p.user_id == uid for p in table.players):
            target_table = table
            break

    if target_table is None:
        await interaction.response.send_message(
            "You're not currently in any Ban-Luck lobby as a player.\n"
            "If a game is already running, please finish the round.",
            ephemeral=True,
        )
        return

    # Remove from lobby and global tracking
    target_table.players = [p for p in target_table.players if p.user_id != uid]
    active_player_ids.discard(uid)

    await interaction.response.send_message(
        f"👋 **{interaction.user.display_name}** left the Ban-Luck lobby "
        f"(banker: <@{target_table.banker_id}>, bet: {target_table.bet:,} 💰)."
    )


@bot.tree.command(name="tokens", description="Check your free-play token balance")
@app_commands.describe(member="The player to check — leave blank for yourself")
async def cmd_tokens(interaction: discord.Interaction, member: discord.Member = None) -> None:
    target = member or interaction.user
    bal    = get_tokens(target.id)
    sign   = "+" if bal > 0 else ""
    color  = discord.Color.green() if bal >= 0 else discord.Color.red()
    embed  = discord.Embed(
        title=f"🪙 {target.display_name}'s Token Balance",
        description=f"**{sign}{bal:,} tokens**",
        color=color,
    )
    embed.set_footer(text="Earned via Free Play (/bj) | reset with /resettoken")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="resettoken", description="Reset all token balances to zero")
async def cmd_reset_token(interaction: discord.Interaction) -> None:
    reset_all_tokens()
    await interaction.response.send_message("✅ All token balances have been reset to **0**.")


# ---------------------------------------------------------------------------
# Slot Machine — helpers
# ---------------------------------------------------------------------------

SLOT_SYMBOLS: list[str] = ["🍒", "🍋", "🍉", "🔔", "💎", "🎰"]

# Pity: guarantee 3-of-a-kind within first 5 spins. Weights when forcing (small chance jackpot/diamond).
PITY_TRIPLE_WEIGHTS: list[tuple[str, float]] = [
    ("🎰", 0.02),   # 2% jackpot
    ("💎", 0.05),   # 5% diamond
    ("🍒", 0.2325), ("🍋", 0.2325), ("🍉", 0.2325), ("🔔", 0.2325),  # 93% others
]


def _spin_grid() -> list[list[str]]:
    """Generate a fresh 3×3 grid of random slot symbols."""
    return [[random.choice(SLOT_SYMBOLS) for _ in range(3)] for _ in range(3)]


def _spin_grid_with_pity(guild_id: int, uid: int) -> list[list[str]]:
    """
    Generate 3×3 grid. For first 5 spins, one randomly chosen spin (1–5) is guaranteed triple;
    which spin is chosen at random when they first spin, so players can't predict it.
    """
    spin_count, pity_trigger_at = ensure_pity_trigger_and_get(guild_id, uid)
    next_spin_number = spin_count + 1
    force_triple = next_spin_number <= 5 and next_spin_number == pity_trigger_at

    if force_triple:
        symbols = [s for s, _ in PITY_TRIPLE_WEIGHTS]
        weights = [w for _, w in PITY_TRIPLE_WEIGHTS]
        chosen = random.choices(symbols, weights=weights, k=1)[0]
        middle = [chosen, chosen, chosen]
        top    = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        bottom = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        return [top, middle, bottom]

    return _spin_grid()


def _calc_slots_payout(middle_row: list[str], bet: int) -> tuple[int, str]:
    """
    Evaluate the middle row and return (payout, description).
    payout is the amount added back to the player's balance
    (bet was already deducted, so net profit = payout - bet).
    """
    a, b, c = middle_row
    if a == b == c:
        if a == "🎰":
            return bet * 50, "🎰 🎰 🎰  **JACKPOT!! MASSIVE WIN!** — **50×** payout!"
        if a == "💎":
            return bet * 20, "💎 💎 💎  **Diamond! MEGA WIN!** — **20×** payout!"
        return bet * 10, f"{a} {a} {a}  **Three of a kind! BIG WIN!** — **10×** payout!"
    if a == b or b == c or a == c:
        return bet, "Two of a kind — bet returned."
    return 0, "No match — lost."


def _fmt_grid(grid: list[list[str]]) -> str:
    """Render the 3×3 grid as a Discord-friendly string, marking the middle row."""
    rows = []
    for i, row in enumerate(grid):
        line = "[ " + " | ".join(row) + " ]"
        if i == 1:
            line += "  ◄"
        rows.append(line)
    return "\n".join(rows)


async def _run_slots(
    channel,
    guild_id: int,
    uid:      int,
    bet:      int,
    in_debt:  bool,
) -> None:
    """
    Core slot machine coroutine.
    Assumes bet has already been deducted from the player's balance.
    Sends spinning animation, waits 1.5 s, then edits to reveal result and settle.
    """
    spin_embed = discord.Embed(
        title="🎰 Slot Machine",
        description=(
            "[ 🔄 | 🔄 | 🔄 ]\n"
            "[ 🔄 | 🔄 | 🔄 ]  ◄\n"
            "[ 🔄 | 🔄 | 🔄 ]\n\n"
            "*Pulling the lever...*"
        ),
        color=discord.Color.orange(),
    )
    spin_embed.set_footer(text=f"Bet: {bet:,} 💰")
    msg = await channel.send(embed=spin_embed)

    await asyncio.sleep(1.5)

    grid   = _spin_grid_with_pity(guild_id, uid)
    middle = grid[1]
    payout, result_desc = _calc_slots_payout(middle, bet)
    net = payout - bet

    update_slots_pity_after_spin(guild_id, uid)

    # Debt tax on net winnings
    tax_line = ""
    if in_debt and net > 0:
        tax      = math.ceil(net * DEBT_TAX_RATE)
        payout  -= tax
        net      = payout - bet
        tax_line = f"\n🏦 **{tax:,}** Gold interest deducted!"

    add_gold(guild_id, uid, payout)
    new_gold = get_gold(guild_id, uid)

    color = (
        discord.Color.gold()    if net > 0 else
        discord.Color.blurple() if net == 0 else
        discord.Color.red()
    )
    net_str = (
        f"+**{net:,}** 💰"    if net > 0 else
        "±**0** 💰"           if net == 0 else
        f"-**{bet:,}** 💰"
    )

    result_embed = discord.Embed(
        title="🎰 Slot Machine — Result",
        description=_fmt_grid(grid),
        color=color,
    )
    result_embed.add_field(name="Outcome", value=result_desc + tax_line, inline=False)
    result_embed.add_field(name="Net",     value=net_str,                inline=True)
    result_embed.add_field(name="Balance", value=f"**{new_gold:,} 💰**", inline=True)
    if new_gold <= 0 and net < 0:
        result_embed.set_footer(text="💸 Broke? Use /daily to claim your daily Gold!")

    await msg.edit(embed=result_embed)


# ---------------------------------------------------------------------------
# Debt Confirmation View — !slots
# ---------------------------------------------------------------------------

class SlotsDebtConfirmView(ui.View):
    """
    Shown when a player with ≤ 0 Gold uses !slots.
    The reels do NOT spin until they explicitly confirm.
    """

    def __init__(self, channel, guild_id: int, uid: int, bet: int) -> None:
        super().__init__(timeout=60)
        self.channel  = channel
        self.guild_id = guild_id
        self.uid      = uid
        self.bet      = bet
        self.done     = False
        self.message: discord.Message | None = None

    def _check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.uid and not self.done

    @ui.button(label="▶️ Continue (30% interest applies)", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not self._check(interaction):
            await interaction.response.send_message("This isn't your game!", ephemeral=True)
            return
        
        # Check MAX_DEBT before executing
        current_gold = get_gold(self.guild_id, self.uid)
        projected_debt = current_gold - int(self.bet * 1.3)
        if projected_debt < MAX_DEBT:
            await interaction.response.send_message(
                f"🚫 Ah Long credit limit reached! Maximum debt is {MAX_DEBT:,} 💰. Your projected debt would be {projected_debt:,} 💰.",
                ephemeral=True
            )
            return
        
        self.done = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)
        add_gold(self.guild_id, self.uid, -self.bet)
        await _run_slots(self.channel, self.guild_id, self.uid, self.bet, in_debt=True)

    @ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if not self._check(interaction):
            await interaction.response.send_message("This isn't your game!", ephemeral=True)
            return
        self.done = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(
            content="❌ Bet cancelled — no Gold was deducted.", embed=None, view=self
        )

    async def on_timeout(self) -> None:
        if self.done:
            return
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(
                    content="⏰ Confirmation timed out — bet cancelled.", embed=None, view=self
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Command: !slots
# ---------------------------------------------------------------------------

@bot.tree.command(name="slots", description="Spin the slot machine — Gold bets only")
@app_commands.describe(bet="Amount of Gold to wager (must be a positive integer)")
async def cmd_slots(interaction: discord.Interaction, bet: int) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    guild_id = interaction.guild.id
    uid = interaction.user.id

    if bet <= 0:
        await interaction.response.send_message("Bet must be a positive integer.", ephemeral=True)
        return

    # Economic Firewall Validation
    is_valid, error_msg = validate_bet_with_firewall(guild_id, uid, bet)
    if not is_valid:
        await interaction.response.send_message(error_msg, ephemeral=True)
        return

    gold_before = get_gold(guild_id, uid)
    in_debt     = gold_before <= 0

    if in_debt:
        embed = discord.Embed(
            title="⚠️ You're in Debt!",
            description=DEBT_WARNING,
            color=discord.Color.red(),
        )
        embed.add_field(name="Current Balance", value=f"**{gold_before:,} 💰**", inline=True)
        embed.add_field(name="Bet",             value=f"**{bet:,} 💰**",          inline=True)
        view = SlotsDebtConfirmView(interaction.channel, guild_id, uid, bet)
        await interaction.response.send_message(embed=embed, view=view)
        msg = await interaction.original_response()
        view.message = msg
        return

    # No debt — deduct bet and spin immediately
    await interaction.response.defer()
    add_gold(guild_id, uid, -bet)
    await _run_slots(interaction.channel, guild_id, uid, bet, in_debt=False)


# ---------------------------------------------------------------------------
# Commands: /joinvc  /leavevc  (admin-only voice channel idle)
# ---------------------------------------------------------------------------

@bot.tree.command(name="joinvc", description="[Admin] Join the voice channel you're currently in")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_joinvc(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    member = interaction.guild.get_member(interaction.user.id)
    user_vc = member.voice.channel if (member and member.voice) else None
    channel_vc = interaction.channel if isinstance(interaction.channel, discord.VoiceChannel) else None

    target_vc = user_vc or channel_vc
    if target_vc is None:
        await interaction.response.send_message(
            "❌ Join a voice channel first, or run this command in a voice channel's chat.", ephemeral=True
        )
        return
    current_vc = interaction.guild.voice_client

    if current_vc is not None:
        if current_vc.channel == target_vc:
            await interaction.response.send_message(
                f"Already in **{target_vc.name}**.", ephemeral=True
            )
            return
        await current_vc.move_to(target_vc)
        await interaction.guild.change_voice_state(channel=target_vc, self_deaf=True, self_mute=True)
    else:
        await target_vc.connect(self_deaf=True, self_mute=True)

    await interaction.response.send_message(f"✅ Joined **{target_vc.name}**.")


@bot.tree.command(name="leavevc", description="[Admin] Leave the current voice channel")
@app_commands.checks.has_permissions(administrator=True)
async def cmd_leavevc(interaction: discord.Interaction) -> None:
    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    vc = interaction.guild.voice_client
    if vc is None:
        await interaction.response.send_message("❌ Not in any voice channel.", ephemeral=True)
        return

    channel_name = vc.channel.name
    await vc.disconnect()
    await interaction.response.send_message(f"👋 Left **{channel_name}**.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not TOKEN or TOKEN == "your_token_here":
        raise RuntimeError(
            "Bot token not set. Open .env and paste your token after DISCORD_TOKEN="
        )
    bot.run(TOKEN)
