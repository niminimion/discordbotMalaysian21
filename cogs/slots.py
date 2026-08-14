import asyncio
import random
import math
import discord
from discord.ext import commands
from discord import app_commands, ui
from config import DEBT_TAX_RATE, MAX_DEBT, MAX_BET, validate_bet_with_firewall, DEBT_WARNING
from database import get_gold, add_gold, ensure_pity_trigger_and_get, update_slots_pity_after_spin
from shared import active_tables, active_poker_tables, _check_active


SLOT_SYMBOLS: list[str] = ["🍒", "🍋", "🍉", "🔔", "💎", "🎰"]

PITY_TRIPLE_WEIGHTS: list[tuple[str, float]] = [
    ("🎰", 0.02),
    ("💎", 0.05),
    ("🍒", 0.2325), ("🍋", 0.2325), ("🍉", 0.2325), ("🔔", 0.2325),
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


class SlotsCog(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="slots", description="Spin the slot machine — Gold bets only")
    @app_commands.describe(bet="Amount of Gold to wager (must be a positive integer)")
    async def cmd_slots(self, interaction: discord.Interaction, bet: int) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        uid = interaction.user.id

        if bet <= 0:
            await interaction.response.send_message("Bet must be a positive integer.", ephemeral=True)
            return

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

        await interaction.response.defer()
        add_gold(guild_id, uid, -bet)
        await _run_slots(interaction.channel, guild_id, uid, bet, in_debt=False)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SlotsCog(bot))
