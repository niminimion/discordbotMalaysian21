"""
Discord Bot MVP
---------------
Features:
  - XP earned per message is based on character count (anti-spam)
  - XP accumulates and unlocks Levels (0–99); Level ≠ XP
  - !level       → show caller's Level, current XP, and XP needed for next level
  - !cointoss    → wager XP on a 50/50 coin flip
  - !blackjack   → challenge another user to P2P Blackjack
"""

import asyncio
import os
import random
import sqlite3
import discord
from discord.ext import commands
from discord import ui
from dotenv import load_dotenv
from blackjack import GameTable, PlayerState
from card_renderer import render_hand_image

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()
TOKEN: str = os.getenv("DISCORD_TOKEN")

MAX_LEVEL: int = 99

# XP awarded per message: capped to prevent walls-of-text abuse.
# Formula: 1 XP per 5 characters, minimum 1, maximum 20.
# e.g. "hi" (2 chars) → 1 XP | "hello world" (11 chars) → 2 XP | 100+ chars → 20 XP
XP_PER_CHAR_DIVISOR: int = 5
XP_MIN_PER_MSG: int = 1
XP_MAX_PER_MSG: int = 20

# Rank titles assigned by level bracket — purely cosmetic.
# Level is just a badge; XP balance is what actually matters for betting.
RANK_TITLES: list[tuple[int, str]] = [
    (99, "👑 Legend"),
    (80, "💎 Diamond"),
    (60, "🏅 Platinum"),
    (40, "🥇 Gold"),
    (20, "🥈 Silver"),
    (10, "🥉 Bronze"),
    (0,  "🌱 Rookie"),
]

def rank_title(level: int) -> str:
    """Return the cosmetic rank title for a given level."""
    for threshold, title in RANK_TITLES:
        if level >= threshold:
            return title
    return RANK_TITLES[-1][1]


# ---------------------------------------------------------------------------
# Level Threshold Table  (precomputed at startup — read-only after that)
# ---------------------------------------------------------------------------
# Formula for XP required to advance FROM level n TO level n+1:
#   xp_to_next(n) = 5·n² + 50·n + 100
#
# This is the same curve used by many popular Discord bots (e.g. MEE6).
# At low levels it's quick; at high levels it demands real engagement.
#
# LEVEL_XP_TABLE[n] = total cumulative XP needed to *reach* level n.
#   LEVEL_XP_TABLE[0] = 0   (you start at level 0)
#   LEVEL_XP_TABLE[1] = 100
#   LEVEL_XP_TABLE[2] = 255  (100 + 155)
#   LEVEL_XP_TABLE[99] ≈ 170 700
#
# Think of this as a sorted lookup table — same pattern as a calibration
# table in firmware where you binary-search for the right range.

def _xp_to_next(level: int) -> int:
    """XP required to advance from `level` to `level + 1`."""
    return 5 * (level ** 2) + 50 * level + 100

LEVEL_XP_TABLE: list[int] = [0]
for _lvl in range(MAX_LEVEL):
    LEVEL_XP_TABLE.append(LEVEL_XP_TABLE[-1] + _xp_to_next(_lvl))
# LEVEL_XP_TABLE now has MAX_LEVEL+1 entries (indices 0 … 99)


# ---------------------------------------------------------------------------
# Database  (PostgreSQL in cloud via Supabase, SQLite for local dev)
# ---------------------------------------------------------------------------
# Set DATABASE_URL env var in Koyeb to use PostgreSQL.
# Leave it unset locally and SQLite is used automatically — no config needed.
#
# Schema: one table, one row per user.
#   user_id — Discord snowflake (64-bit integer)
#   xp      — current XP balance (floored at 0)

DATABASE_URL: str | None = os.environ.get("DATABASE_URL")

if DATABASE_URL:
    # ── Cloud: PostgreSQL (Supabase) ─────────────────────────────────────
    import psycopg2

    class _DB:
        """
        Thin psycopg2 wrapper that mimics the sqlite3 connection API
        (execute + fetchone, commit).  Auto-reconnects on dropped connections.
        Translates SQLite-style '?' placeholders to PostgreSQL '%s'.
        """

        def __init__(self, url: str) -> None:
            self._url = url
            self._conn = self._connect()

        def _connect(self):
            conn = psycopg2.connect(self._url)
            conn.autocommit = False
            return conn

        def execute(self, sql: str, params: tuple = ()):
            sql = sql.replace("?", "%s")   # SQLite → PostgreSQL placeholder
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

    db = _DB(DATABASE_URL)
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_xp (
            user_id  BIGINT  PRIMARY KEY,
            xp       INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.commit()
    print("[DB] Connected → PostgreSQL (Supabase)")

else:
    # ── Local: SQLite ────────────────────────────────────────────────────
    DB_PATH: str = os.environ.get(
        "DB_PATH",
        os.path.join(os.path.dirname(__file__), "bot_data.db"),
    )
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_xp (
            user_id  INTEGER PRIMARY KEY,
            xp       INTEGER NOT NULL DEFAULT 0
        )
    """)
    db.commit()
    print(f"[DB] Connected → {DB_PATH}")

# Tokens table — shared between both DB backends (BIGINT is valid in SQLite too)
db.execute("""
    CREATE TABLE IF NOT EXISTS user_tokens (
        user_id  BIGINT  PRIMARY KEY,
        tokens   INTEGER NOT NULL DEFAULT 0
    )
""")
db.commit()


# ---------------------------------------------------------------------------
# Bot / Intent Setup
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True     # Required to read message text

bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------------------------------------------------------------------
# Pure helper functions  (no side-effects — easy to unit-test)
# ---------------------------------------------------------------------------

def get_total_xp(user_id: int) -> int:
    """Read total XP from DB, returning 0 for first-time users."""
    row = db.execute(
        "SELECT xp FROM user_xp WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else 0


def add_xp(user_id: int, amount: int) -> None:
    """
    Upsert XP for a user (INSERT or UPDATE on conflict).
    XP is always clamped to >= 0 before writing.

    INSERT … ON CONFLICT … DO UPDATE is the SQLite equivalent of
    a read-modify-write with a single atomic statement — no separate
    SELECT needed, no race condition.
    """
    new_xp = max(0, get_total_xp(user_id) + amount)
    db.execute(
        """
        INSERT INTO user_xp (user_id, xp) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET xp = excluded.xp
        """,
        (user_id, new_xp),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Token helpers  (free-play only; no floor — can go negative)
# ---------------------------------------------------------------------------

FREE_PLAY_TOKEN_BET: int = 1   # default bet size for every free-play game


def get_tokens(user_id: int) -> int:
    """Return the player's current token balance (0 for first-timers)."""
    row = db.execute(
        "SELECT tokens FROM user_tokens WHERE user_id = ?", (user_id,)
    ).fetchone()
    return row[0] if row else 0


def add_tokens(user_id: int, amount: int) -> None:
    """Upsert token balance — no floor, may go negative."""
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
    """Zero-out every row in the tokens table."""
    db.execute("UPDATE user_tokens SET tokens = 0")
    db.commit()


def level_from_xp(total_xp: int) -> int:
    """
    Derive the current level from total XP via a linear scan of the table.
    Returns a value clamped to [0, MAX_LEVEL].

    Equivalent to: find the highest index n such that LEVEL_XP_TABLE[n] <= total_xp.
    """
    level = 0
    for n in range(MAX_LEVEL + 1):
        if total_xp >= LEVEL_XP_TABLE[n]:
            level = n
        else:
            break
    return level


def xp_progress(total_xp: int) -> tuple[int, int, int]:
    """
    Returns a 3-tuple describing progress within the current level:
      (current_level, xp_earned_in_this_level, xp_needed_for_next_level)

    At MAX_LEVEL, xp_earned and xp_needed are both 0 (no next level exists).
    """
    lvl = level_from_xp(total_xp)
    if lvl >= MAX_LEVEL:
        return lvl, 0, 0
    xp_start = LEVEL_XP_TABLE[lvl]
    xp_end   = LEVEL_XP_TABLE[lvl + 1]
    return lvl, total_xp - xp_start, xp_end - xp_start


def xp_for_message(content: str) -> int:
    """
    Calculate XP to award for a message based on its character count.
    Clamped to [XP_MIN_PER_MSG, XP_MAX_PER_MSG] to discourage spam.
    """
    char_count = len(content.strip())
    return max(XP_MIN_PER_MSG, min(char_count // XP_PER_CHAR_DIVISOR, XP_MAX_PER_MSG))


# ---------------------------------------------------------------------------
# Health check server  (Koyeb port check + UptimeRobot keep-alive)
# ---------------------------------------------------------------------------

async def _start_health_server() -> None:
    """
    Tiny HTTP server that answers GET / with "OK".
    - Koyeb requires an open port to confirm the service is running.
    - UptimeRobot pings this URL every 5 minutes to prevent auto-sleep.
    Port is read from the PORT env var (set automatically by Koyeb).
    """
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
    print(f"[BOT] Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"[BOT] Level cap: {MAX_LEVEL} | XP to reach lv.99: {LEVEL_XP_TABLE[MAX_LEVEL]:,}")
    print("[BOT] Ready. Listening for messages...")


# ---------------------------------------------------------------------------
# Event: on_message  — XP accumulator (character-based)
# ---------------------------------------------------------------------------

@bot.event
async def on_message(message: discord.Message) -> None:
    """
    ISR equivalent: fires on every visible message.

    1. Guard — ignore bot messages (prevents feedback loops).
    2. Award XP proportional to character count.
    3. Forward to command dispatcher (mandatory; without this, !commands are deaf).
    """
    if message.author.bot:
        return

    xp_gained = xp_for_message(message.content)
    add_xp(message.author.id, xp_gained)

    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Command: !level
# ---------------------------------------------------------------------------

@bot.command(name="level")
async def cmd_level(ctx: commands.Context) -> None:
    """
    Usage: !level

    XP balance is the primary number — it is your actual betting budget and
    reflects wins/losses from gambling.  Level is a cosmetic rank title derived
    from your current XP; it rises and falls with your balance.
    """
    total_xp              = get_total_xp(ctx.author.id)
    lvl, xp_now, xp_next = xp_progress(total_xp)
    title                 = rank_title(lvl)

    embed = discord.Embed(
        title=f"{ctx.author.display_name}",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="💰 XP Balance",
        value=f"**{total_xp:,} XP**  *(your betting budget)*",
        inline=False,
    )

    if lvl >= MAX_LEVEL:
        embed.add_field(
            name="🏆 Rank",
            value=f"{title}  —  **Level {lvl} (MAX)**",
            inline=False,
        )
    else:
        filled  = round(xp_now / xp_next * 10) if xp_next else 10
        bar     = "▓" * filled + "░" * (10 - filled)
        pct     = round(xp_now / xp_next * 100) if xp_next else 100
        embed.add_field(
            name="📊 Rank",
            value=f"{title}  —  **Level {lvl}**",
            inline=True,
        )
        embed.add_field(
            name=f"→ Level {lvl + 1}",
            value=f"`[{bar}]` {pct}%\n{xp_now:,} / {xp_next:,} XP",
            inline=True,
        )

    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# Command: !cointoss
# ---------------------------------------------------------------------------

@bot.command(name="ht", aliases=["cointoss"])
async def cmd_cointoss(
    ctx: commands.Context,
    bet_amount: int,
    choice: str,
) -> None:
    """
    Usage: !ht <bet_xp> <h|t>

    Wagers <bet_xp> of the caller's total XP on a 50/50 coin flip.
    Win  → +bet_xp  (may trigger level-up)
    Lose → -bet_xp  (may trigger level-down; floored at 0)
    """
    uid      = ctx.author.id
    total_xp = get_total_xp(uid)

    # --- Input validation (check bounds before touching state) --------------

    choice = choice.lower()
    # Normalise shorthand: h → heads, t → tails
    if choice == "h":
        choice = "heads"
    elif choice == "t":
        choice = "tails"

    if choice not in ("heads", "tails"):
        await ctx.send(f"Invalid choice. Use `h` (heads) or `t` (tails).")
        return

    if bet_amount <= 0:
        await ctx.send("Bet amount must be greater than 0.")
        return

    if bet_amount > total_xp:
        await ctx.send(
            f"Not enough XP! You wagered **{bet_amount}** but only have **{total_xp}** XP."
        )
        return

    # --- Coin flip ----------------------------------------------------------

    result: str = random.choice(("heads", "tails"))
    won: bool   = (result == choice)

    # --- State update -------------------------------------------------------

    lvl_before = level_from_xp(total_xp)

    if won:
        add_xp(uid, +bet_amount)
    else:
        add_xp(uid, -bet_amount)    # add_xp clamps total XP to 0

    new_total_xp = get_total_xp(uid)
    lvl_after, xp_now, xp_next = xp_progress(new_total_xp)

    # Build outcome message
    outcome_line = (
        f"🪙 Coin landed **{result}** — you guessed right! +**{bet_amount}** XP."
        if won else
        f"🪙 Coin landed **{result}** — wrong guess. -**{bet_amount}** XP."
    )

    # Append level change annotation if level shifted
    if lvl_after > lvl_before:
        level_line = f"⬆️ Level up! You are now **Level {lvl_after}**."
    elif lvl_after < lvl_before:
        level_line = f"⬇️ Level down. You are now **Level {lvl_after}**."
    else:
        if lvl_after >= MAX_LEVEL:
            level_line = f"Level **{lvl_after}** *(MAX)* | Total XP: **{new_total_xp:,}**"
        else:
            level_line = f"Level **{lvl_after}** | XP: **{xp_now}** / **{xp_next}**"

    await ctx.send(f"{outcome_line}\n{level_line}")


# ---------------------------------------------------------------------------
# Error handler for !cointoss bad arguments
# ---------------------------------------------------------------------------

@cmd_cointoss.error
async def cointoss_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.BadArgument):
        await ctx.send(
            "Bad arguments. Usage: `!ht <bet_xp> <h|t>`\n"
            "Example: `!ht 50 h`"
        )
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Missing arguments. Usage: `!ht <bet_xp> <h|t>`")
    else:
        raise error


# ---------------------------------------------------------------------------
# Blackjack — In-Memory Game State
# ---------------------------------------------------------------------------
# active_tables:     banker_id → GameTable  (one entry per open/running game)
# active_player_ids: all user IDs currently in a lobby or live game
#   Acts as a mutex — prevents anyone from joining two games at once.

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
    Calculate XP returned to a single player after the game ends.
    The player's bet was already deducted at game start.

    Ban-Luck payout rules:
      0       → full loss
      bet     → push / refund
      bet * 2 → normal win
      special → bet * multiplier (2x / 3x / 5x depending on hand)

    Clash Rule: both player AND banker have specials → higher multiplier wins;
      equal multipliers → push.
    """
    # Escaped players are handled separately in resolve_table (skipped here)
    p_hand = player.hand
    b_hand = banker.hand

    # Bust always loses
    if p_hand.is_bust:
        return 0, "💥 Bust — lost"

    p_special = p_hand.special
    b_special = b_hand.special

    # Clash Rule: both have special hands
    if p_special and b_special:
        p_mult = p_special[1]
        b_mult = b_special[1]
        if p_mult > b_mult:
            # Total return = stake back + multiplier × bet
            payout = int(bet * (1 + p_mult))
            return payout, f"🏆 Clash! {p_special[0]} beats {b_special[0]} — **+{payout - bet}** XP"
        if b_mult > p_mult:
            return 0, f"❌ Clash! {b_special[0]} beats {p_special[0]} — lost"
        return bet, f"🤝 Clash! Equal specials — push"

    # Only player has special
    if p_special:
        # Total return = stake back + multiplier × bet
        # e.g. 2x Ban-Luck → +2×bet profit → get back bet + 2×bet = 3×bet total
        payout = int(bet * (1 + p_special[1]))
        return payout, f"🏆 {p_special[0]} — **+{payout - bet}** XP"

    # Only banker has special → player loses
    if b_special:
        return 0, f"❌ Banker has {b_special[0]} — lost"

    # Banker bust (neither has special)
    if b_hand.is_bust:
        return bet * 2, "🏆 Banker busted — won"

    # Normal score comparison
    if p_hand.score > b_hand.score:
        return bet * 2, f"🏆 Won ({p_hand.score} vs {b_hand.score})"
    if p_hand.score < b_hand.score:
        return 0, f"❌ Lost ({p_hand.score} vs {b_hand.score})"
    return bet, f"🤝 Push ({p_hand.score} each)"


def _cleanup_table(table: GameTable) -> None:
    active_tables.pop(table.banker_id, None)
    active_player_ids.difference_update(table.all_player_ids)


async def _ping_turn(interaction: discord.Interaction, table: GameTable) -> None:
    """
    Send a NEW (non-ephemeral) channel message pinging the current player.
    A fresh message is required so Discord actually notifies them —
    editing an existing message does NOT trigger a ping.
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
# Ban-Luck — Embed Builders
# ---------------------------------------------------------------------------

def build_lobby_embed(table: GameTable) -> discord.Embed:
    """Waiting-room embed shown while players join."""
    current   = len(table.players) + 1
    capacity  = table.MAX_PLAYERS + 1
    free_play = table.bet == 0

    if free_play:
        stakes_line = "🎮 **Free Play** (no XP wagered)"
    else:
        stakes_line = (
            f"Bet: **{table.bet} XP** each  |  "
            f"Banker escrow: **{table.banker_escrow} XP** (covers 6× max payout)"
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
    Public game board. First card hidden per player while in progress.
    Players see their own full hand + card image via 🃏 My Cards (ephemeral).
    """
    free_play = table.bet == 0
    embed = discord.Embed(
        title="🃏  Ban-Luck" + (" — Free Play 🎮" if free_play else ""),
        description="🎮 No XP wagered" if free_play else f"Bet: **{table.bet} XP** each",
        color=discord.Color.gold() if reveal else (discord.Color.teal() if free_play else discord.Color.green()),
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
            text=f"▶️ {current.name}'s turn  |  Bet: {table.bet} XP each"
        )
    return embed


# ---------------------------------------------------------------------------
# Ban-Luck — Resolve Table (payout + cleanup)
# ---------------------------------------------------------------------------

async def resolve_table(
    table:     GameTable,
    game_view: "GameView",
    interaction: discord.Interaction,
) -> None:
    """
    Calculate payouts, update SQLite, then:
      1. Edit the board message to reveal all cards with disabled buttons.
      2. Send a NEW channel message with the full result breakdown +
         a mention for every participant so they get notified.
      3. Attach a RematchView to the results message.

    Escrow model (zero-sum):
      Banker deducted bet × num_players × 5 at start (5× coverage).
      banker_return = banker_escrow + sum(bet - xp_ret) for non-escaped players.
    """
    table.phase = "finished"
    banker      = table.banker
    banker_net  = 0
    free_play   = table.bet == 0
    lines: list[str] = []

    if free_play:
        # ── Free play: settle tokens (no XP changes) ────────────────────────
        tok_banker_net = 0
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
            sign = "+" if tok_delta >= 0 else ""
            new_bal = get_tokens(player.user_id)
            lines.append(f"**{player.name}**: {desc}")
            tok_lines.append(
                f"**{player.name}**: {sign}{tok_delta:,} 🪙 → **{new_bal:,}**"
            )
        add_tokens(table.banker_id, tok_banker_net)
        b_sign = "+" if tok_banker_net >= 0 else ""
        b_bal  = get_tokens(table.banker_id)
        lines.append(f"**{banker.name} (Banker)**: Net **{b_sign}{tok_banker_net:,}** tokens")
        tok_lines.append(
            f"**{banker.name} (Banker)**: {b_sign}{tok_banker_net:,} 🪙 → **{b_bal:,}**"
        )
    else:
        # ── Staked play: settle XP ───────────────────────────────────────────
        tok_lines = None
        for player in table.players:
            if player.escaped:
                lines.append(f"**{player.name}**: 🏃 Escaped (bet refunded)")
                continue
            xp_ret, desc = _calc_payout(player, banker, table.bet)
            add_xp(player.user_id, xp_ret)
            lines.append(f"**{player.name}**: {desc}")
            banker_net += (table.bet - xp_ret)
        banker_total = table.banker_escrow + banker_net
        add_xp(table.banker_id, max(0, banker_total))
        net_str = f"+{banker_net}" if banker_net >= 0 else str(banker_net)
        lines.append(f"**{banker.name} (Banker)**: Net **{net_str}** XP")

    # ── 1. Edit board to reveal cards; disable all buttons ──────────────────
    board_embed = build_board_embed(table, reveal=True)
    for item in game_view.children:
        item.disabled = True  # type: ignore[attr-defined]
    await interaction.response.edit_message(content="", embed=board_embed, view=game_view)

    # ── 2. New channel message with results + everyone mentioned ────────────
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
        result_embed.set_footer(text=f"Free Play — bet {FREE_PLAY_TOKEN_BET} tokens each | use !tokens to check balance")
    else:
        result_embed.set_footer(text=f"Bet: {table.bet} XP each")

    # ── 3. Rematch button ────────────────────────────────────────────────────
    participants  = [(p.user_id, p.name) for p in table.all_participants]
    rematch_view  = RematchView(bet=table.bet, participants=participants)

    _cleanup_table(table)   # clean up BEFORE sending so players can re-join immediately
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

    # --- Hit ----------------------------------------------------------------

    @ui.button(label="🎯 Hit", style=discord.ButtonStyle.primary)
    async def hit(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table   = self.table
        current = table.current_participant

        if current is None or interaction.user.id != current.user_id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return

        card = table.deck.deal()
        current.hand.add(card)

        special = current.hand.special

        # Auto-end conditions: bust or triggered 五龙 / 7-7-7
        # (Ban-Luck / Ban-Ban / Double only trigger on initial 2-card deal,
        #  so those won't appear here after a Hit adds a 3rd card)
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
            # Edit board WITHOUT content — edits don't trigger pings
            await interaction.response.edit_message(content="", embed=embed, view=self)
            # Only ping when the turn actually moved to someone new (bust / special auto-stand).
            # If the same player is still going (e.g. hit 16→18, turn continues), stay silent.
            if auto_end:
                await _ping_turn(interaction, table)

        # Ephemeral card image — only you can see this
        buf = await render_hand_image(current.hand)
        if buf:
            await interaction.followup.send(
                followup_txt, file=discord.File(buf, "hand.png"), ephemeral=True
            )
        else:
            await interaction.followup.send(followup_txt, ephemeral=True)

    # --- Stand  (blocked if score < 16) -------------------------------------

    @ui.button(label="🛑 Stand", style=discord.ButtonStyle.secondary)
    async def stand(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table   = self.table
        current = table.current_participant

        if current is None or interaction.user.id != current.user_id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return

        # Minimum 16 Rule — cannot stand below 16,
        # UNLESS the player already holds a special hand (e.g. Double 3-3).
        # Forcing them to hit would only risk busting and losing the special payout.
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
            await interaction.response.edit_message(content="", embed=embed, view=self)
            await _ping_turn(interaction, table)

    # --- Escape (🏃 走) — only on initial 2-card deal with 15 or 16 ---------

    @ui.button(label="🏃 Escape (走)", style=discord.ButtonStyle.danger)
    async def escape(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table   = self.table
        current = table.current_participant

        if current is None or interaction.user.id != current.user_id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return

        if not current.hand.can_escape:
            await interaction.response.send_message(
                "Escape (走) is only available on your initial 2-card deal "
                "when your score is exactly 15 or 16.",
                ephemeral=True,
            )
            return

        # Refund bet and mark as escaped
        add_xp(current.user_id, table.bet)
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
            await interaction.response.edit_message(content="", embed=embed, view=self)
            await _ping_turn(interaction, table)

    # --- My Cards (ephemeral — only you can see this) -----------------------

    @ui.button(label="🃏 My Cards", style=discord.ButtonStyle.grey)
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

    # --- Timeout ------------------------------------------------------------

    async def on_timeout(self) -> None:
        """Refund all non-escaped players + full banker escrow on timeout."""
        table = self.table
        if table.phase == "finished":
            return

        table.phase = "finished"
        for p in table.players:
            if not p.escaped:   # escaped players were already refunded
                add_xp(p.user_id, table.bet)
        add_xp(table.banker_id, table.banker_escrow)

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
    Once the last vote is cast the game auto-starts:
      - Banker is randomly chosen from those with enough escrow XP.
      - Everyone is auto-joined (no separate lobby needed).
      - Each player receives their starting hand via ephemeral (using the
        interactions stored when they clicked Rematch).
    """

    def __init__(self, bet: int, participants: list[tuple[int, str]]) -> None:
        super().__init__(timeout=120)   # 2 minutes to gather all votes
        self.bet             = bet
        self.participants    = participants      # [(user_id, display_name), ...]
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

        # Guard: only original participants may vote
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

        if uid in active_player_ids:
            await interaction.response.send_message(
                "You're already in another game — can't join the rematch.", ephemeral=True
            )
            return

        if uid in self.votes:
            await interaction.response.send_message(
                "You've already voted ✅  Waiting for the others…", ephemeral=True
            )
            return

        # Record vote and store interaction for later ephemeral hand reveal
        self.votes.add(uid)
        self.interactions[uid] = interaction
        count = len(self.votes)
        button.label = f"🔄 Rematch ({count}/{total})"

        # ── Not all in yet — show who's still missing ────────────────────────
        if count < total:
            waiting = [name for uid2, name in self.participants if uid2 not in self.votes]
            await interaction.response.edit_message(
                content=f"⏳ Waiting for **{', '.join(waiting)}** to vote…",
                view=self,
            )
            return

        # ── All voted — start the game ───────────────────────────────────────
        self.started = True
        button.disabled = True
        self.stop()

        # Escrow needed if this person becomes banker (worst-case: all others join)
        num_others    = total - 1
        escrow_needed = self.bet * num_others * 6  # 0 in free-play mode

        eligible = [
            (uid2, name)
            for uid2, name in self.participants
            if uid2 not in active_player_ids
            and (escrow_needed == 0 or get_total_xp(uid2) >= escrow_needed)
        ]
        if not eligible:
            await interaction.response.edit_message(
                content=(
                    f"❌ Rematch cancelled — nobody has enough XP to be banker.\n"
                    f"(Need **{escrow_needed} XP** to cover the 6× escrow for {num_others} players.)"
                ),
                embed=None, view=None,
            )
            return

        banker_id, banker_name = random.choice(eligible)

        # Build table and auto-add all other voters
        table = GameTable(banker_id=banker_id, banker_name=banker_name, bet=self.bet)
        for uid2, name in self.participants:
            if uid2 != banker_id and uid2 not in active_player_ids:
                table.add_player(uid2, name)

        # Register active state
        active_tables[banker_id] = table
        for p in table.all_participants:
            active_player_ids.add(p.user_id)

        # Deduct bets
        for p in table.players:
            add_xp(p.user_id, -table.bet)
        add_xp(banker_id, -table.banker_escrow)

        # Deal cards
        table.start()
        game_view = GameView(table)

        # Edit the results message into the new game board
        board_embed = build_board_embed(table)
        await interaction.response.edit_message(
            content=f"🔄 **Rematch!** New banker: **{banker_name}**",
            embed=board_embed,
            view=game_view,
        )
        game_view.message = interaction.message

        # Send each player their starting hand via their stored Rematch interaction
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
                pass  # Interaction expired — silently skip

        # Ping the first player
        first = table.current_participant
        if first:
            await interaction.followup.send(
                f"▶️ <@{first.user_id}> your turn first!", ephemeral=False
            )

    async def on_timeout(self) -> None:
        """If not everyone voted in time, disable the button silently."""
        if self.started:
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
        # Stores each participant's Discord interaction so we can send them
        # a private ephemeral (only-you-can-see) when the game starts.
        # Key = user_id.  Value = the interaction that was triggered when
        # that player clicked Join (or the banker clicked Start Game).
        self.interactions: dict[int, discord.Interaction] = {}

    @ui.button(label="🪑 Join", style=discord.ButtonStyle.green)
    async def join(
        self, interaction: discord.Interaction, button: ui.Button
    ) -> None:
        table = self.table
        uid   = interaction.user.id

        if uid in active_player_ids:
            await interaction.response.send_message(
                "You're already in a game or lobby.", ephemeral=True
            )
            return

        if table.bet > 0 and get_total_xp(uid) < table.bet:
            await interaction.response.send_message(
                f"Not enough XP to join! Need **{table.bet}**, you have **{get_total_xp(uid)}**.",
                ephemeral=True,
            )
            return

        if not table.add_player(uid, interaction.user.display_name):
            await interaction.response.send_message(
                "Can't join — table is full or game already started.", ephemeral=True
            )
            return

        active_player_ids.add(uid)
        # Store interaction now; used later in _launch_game to send ephemeral hand reveal
        self.interactions[uid] = interaction

        if table.is_full:
            await self._launch_game(interaction)
        else:
            await interaction.response.edit_message(
                embed=build_lobby_embed(table), view=self
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

        # Store banker's interaction so they also get the auto ephemeral on game start
        self.interactions[interaction.user.id] = interaction
        await self._launch_game(interaction)

    async def _launch_game(self, interaction: discord.Interaction) -> None:
        """
        LOBBY → PLAYING:
        1. Validate banker has banker_escrow XP (bet × players × 5).
        2. Deduct bets (players: bet each; banker: banker_escrow).
        3. Deal 2 cards, position first active player.
        4. Show game board.
        """
        if self.resolved:
            return

        table         = self.table
        banker_needed = table.banker_escrow   # 0 in free-play mode

        # Escrow / XP validation only applies when there are real stakes
        if banker_needed > 0 and get_total_xp(table.banker_id) < banker_needed:
            for p in table.players:
                active_player_ids.discard(p.user_id)
            _cleanup_table(table)
            self.resolved = True
            self.stop()
            await interaction.response.edit_message(
                content=(
                    f"❌ Game cancelled — banker **{table.banker_name}** doesn't have "
                    f"enough XP for the 6× escrow "
                    f"(needs **{banker_needed}**, has **{get_total_xp(table.banker_id)}**)."
                ),
                embed=None, view=None,
            )
            return

        self.resolved = True
        self.stop()

        if banker_needed > 0:
            for p in table.players:
                add_xp(p.user_id, -table.bet)
            add_xp(table.banker_id, -banker_needed)

        table.start()
        game_view = GameView(table)

        if table.phase == "finished":
            await self._resolve_immediate(table, game_view, interaction)
            return

        embed = build_board_embed(table)
        await interaction.response.edit_message(content="", embed=embed, view=game_view)
        game_view.message = interaction.message

        # ── Send each participant their starting hand as an ephemeral ────────
        # We stored the Discord interaction for everyone who clicked Join, and
        # the banker who clicked Start Game. Discord allows followup messages
        # on a prior interaction for up to 15 minutes — well within the 2-min
        # lobby window — so this always works.
        for p in table.all_participants:
            stored = self.interactions.get(p.user_id)
            if stored is None:
                continue   # no interaction stored (e.g. banker on auto-start)
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
                pass  # Interaction expired — silently skip

        # ── If anyone has no stored interaction, tell them to click My Cards ─
        no_ix = [p for p in table.all_participants if p.user_id not in self.interactions]
        fallback_mentions = " ".join(f"<@{p.user_id}>" for p in no_ix)
        if fallback_mentions:
            await interaction.followup.send(
                f"🃏 {fallback_mentions} — click **🃏 My Cards** to see your hand (only you can see it).",
                ephemeral=False,
            )

        # ── First turn ping (new message = real notification) ────────────────
        first = table.current_participant
        if first:
            await interaction.followup.send(
                f"▶️ <@{first.user_id}> your turn first!", ephemeral=False
            )

    async def _resolve_immediate(
        self,
        table:     GameTable,
        game_view: "GameView",
        interaction: discord.Interaction,
    ) -> None:
        """Immediate resolution when all turns end on the deal (edge case)."""
        banker     = table.banker
        banker_net = 0
        free_play  = table.bet == 0
        lines: list[str] = []

        if free_play:
            tok_banker_net = 0
            tok_lines: list[str] = []
            for player in table.players:
                if player.escaped:
                    lines.append(f"**{player.name}**: 🏃 Escaped")
                    tok_lines.append(f"**{player.name}**: 🏃 Escaped")
                    continue
                tok_ret, desc = _calc_payout(player, banker, FREE_PLAY_TOKEN_BET)
                tok_delta = tok_ret - FREE_PLAY_TOKEN_BET
                add_tokens(player.user_id, tok_delta)
                tok_banker_net -= tok_delta
                sign   = "+" if tok_delta >= 0 else ""
                new_bal = get_tokens(player.user_id)
                lines.append(f"**{player.name}**: {desc}")
                tok_lines.append(f"**{player.name}**: {sign}{tok_delta:,} 🪙 → **{new_bal:,}**")
            add_tokens(table.banker_id, tok_banker_net)
            b_sign = "+" if tok_banker_net >= 0 else ""
            b_bal  = get_tokens(table.banker_id)
            lines.append(f"**{banker.name} (Banker)**: Net **{b_sign}{tok_banker_net:,}** tokens")
            tok_lines.append(f"**{banker.name} (Banker)**: {b_sign}{tok_banker_net:,} 🪙 → **{b_bal:,}**")
        else:
            tok_lines = None
            for player in table.players:
                if player.escaped:
                    lines.append(f"**{player.name}**: 🏃 Escaped")
                    continue
                xp_ret, desc = _calc_payout(player, banker, table.bet)
                add_xp(player.user_id, xp_ret)
                lines.append(f"**{player.name}**: {desc}")
                banker_net += (table.bet - xp_ret)
            add_xp(table.banker_id, max(0, table.banker_escrow + banker_net))
            net_str = f"+{banker_net}" if banker_net >= 0 else str(banker_net)
            lines.append(f"**{banker.name} (Banker)**: Net **{net_str}** XP")

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
            result_embed.set_footer(text=f"Free Play — bet {FREE_PLAY_TOKEN_BET} tokens each | use !tokens to check balance")
        else:
            result_embed.set_footer(text=f"Bet: {table.bet} XP each")

        participants = [(p.user_id, p.name) for p in table.all_participants]
        rematch_view = RematchView(bet=table.bet, participants=participants)

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
# Command: !banluck
# ---------------------------------------------------------------------------

@bot.command(name="bj")
async def cmd_bj(ctx: commands.Context, bet: int = 0) -> None:
    """
    Usage: !bj [bet]

    !bj        → Free Play mode (no XP wagered — just for fun)
    !bj 100    → Stakes mode (bet 100 XP; banker escrow = bet × players × 6)

    Opens a Malaysian Ban-Luck (21) lobby as the Banker (庄家).
    Up to 4 players join by clicking the button.
    """
    uid  = ctx.author.id
    name = ctx.author.display_name

    if bet < 0:
        await ctx.send("Bet cannot be negative. Use `!bj` for free play or `!bj <amount>` for stakes.")
        return

    if uid in active_player_ids:
        await ctx.send("You already have an open lobby or are in a game.")
        return

    # Only check XP when there are real stakes; the escrow itself is
    # validated at game-start (when the player count is known).
    if bet > 0 and get_total_xp(uid) < bet:
        await ctx.send(
            f"Not enough XP to open a table! Need at least **{bet}** XP "
            f"(you have **{get_total_xp(uid)}**).\n"
            f"Note: the full escrow of **{bet} × players × 6** is deducted at game start."
        )
        return

    table = GameTable(banker_id=uid, banker_name=name, bet=bet)
    active_tables[uid]   = table
    active_player_ids.add(uid)

    view = LobbyView(table)
    msg  = await ctx.send(embed=build_lobby_embed(table), view=view)
    view.message = msg


@bot.command(name="tokens")
async def cmd_tokens(ctx: commands.Context, member: discord.Member = None) -> None:
    """
    !tokens          — show your own token balance
    !tokens @someone — show that player's token balance
    """
    target = member or ctx.author
    bal    = get_tokens(target.id)
    sign   = "+" if bal > 0 else ""
    color  = discord.Color.green() if bal >= 0 else discord.Color.red()
    embed  = discord.Embed(
        title=f"🪙 {target.display_name}'s Token Balance",
        description=f"**{sign}{bal:,} tokens**",
        color=color,
    )
    embed.set_footer(text="Earned via Free Play (!bj) | reset with !resettoken")
    await ctx.send(embed=embed)


@bot.command(name="resettoken")
async def cmd_reset_token(ctx: commands.Context) -> None:
    """Zero-out every player's token balance."""
    reset_all_tokens()
    await ctx.send("✅ All token balances have been reset to **0**.")


@cmd_bj.error
async def bj_error(ctx: commands.Context, error: commands.CommandError) -> None:
    if isinstance(error, commands.BadArgument):
        await ctx.send(
            "Bad argument. Usage: `!bj` (free play) or `!bj <bet>` (with stakes)\n"
            "Example: `!bj 50`"
        )
    else:
        raise error


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not TOKEN or TOKEN == "your_token_here":
        raise RuntimeError(
            "Bot token not set. Open .env and paste your token after DISCORD_TOKEN="
        )
    bot.run(TOKEN)
