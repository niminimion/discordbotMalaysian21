import asyncio
import random
import math
import discord
from discord.ext import commands
from discord import app_commands, ui
from typing import Literal
from blackjack import GameTable, PlayerState
from card_renderer import render_hand_image
from config import DEBT_TAX_RATE, FREE_PLAY_TOKEN_BET, DEBT_WARNING, MAX_DEBT, validate_bet_with_firewall
from database import get_gold, add_gold, get_tokens, add_tokens
from shared import (active_tables, active_player_ids, _check_active, _cleanup_table,
                    _apply_debt_tax, _status_tag)


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

    if p_special:
        payout = int(bet * (1 + p_special[1]))
        return payout, f"🏆 {p_special[0]} — **+{payout - bet:,}** 💰"

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
            continue

        payout, desc    = _calc_payout(player, banker, table.bet)
        original_payout = payout

        if player.in_debt:
            payout, desc = _apply_debt_tax(payout, table.bet, desc)

        add_gold(table.guild_id, player.user_id, payout)
        lines.append(f"**{player.name}**: {desc}")
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
        if current.user_id == table.banker_id:
            await channel.send(f"⏰ <@{expected_uid}> (Banker) timed out — game ended, escrow forfeited to players!")
            await self._auto_end_game(channel)
        else:
            await channel.send(f"⏰ <@{expected_uid}> timed out — auto-quit, bet forfeited to banker!")
            await self._auto_quit_player(channel, current)

    async def _auto_quit_player(self, channel: discord.abc.Messageable, player) -> None:
        table = self.table
        free_play = table.bet == 0
        if free_play:
            add_tokens(table.banker_id, FREE_PLAY_TOKEN_BET)
        else:
            add_gold(table.guild_id, table.banker_id, table.bet)
        player.escaped = True
        setattr(player, 'quit', True)
        active_player_ids.discard(player.user_id)
        active_after = [p for p in table.players if not p.escaped]
        if not active_after:
            await self._resolve_no_interaction(channel)
            return
        should_resolve = table.advance()
        if should_resolve:
            await self._resolve_no_interaction(channel)
        else:
            nxt = table.current_participant
            embed = build_board_embed(table)
            if nxt:
                embed.set_footer(text=f"⏰ {player.name} timed out  |  Now: {nxt.name}")
            if self.message:
                try:
                    await self.message.delete()
                except Exception:
                    pass
            self.message = await channel.send(embed=embed, view=self)
            if nxt:
                await channel.send(f"▶️ <@{nxt.user_id}> it's your turn!")
            self._start_turn_timer(channel)

    async def _auto_end_game(self, channel: discord.abc.Messageable) -> None:
        table = self.table
        table.phase = "finished"
        free_play = table.bet == 0
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
                lines.append(f"**{p.name}**: Bet returned + **{share:,}** 💰 from banker's escrow")
        if not free_play:
            remainder = table.banker_escrow % n if n > 0 else table.banker_escrow
            if remainder > 0:
                add_gold(table.guild_id, table.banker_id, remainder)
            lines.append(f"**{table.banker.name} (Banker)**: ⏰ Timed out — **{table.banker_escrow:,}** 💰 escrow forfeited")
        else:
            lines.append(f"**{table.banker.name} (Banker)**: ⏰ Timed out — game ended early")
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
            title="⏰  Banker Timed Out — Game Ended",
            description=f"{all_mentions}\n\n" + "\n".join(lines),
            color=discord.Color.orange(),
        )
        participants = [(p.user_id, p.name) for p in table.all_participants]
        rematch_view = RematchView(bet=table.bet, guild_id=table.guild_id, participants=participants)
        _cleanup_table(table)
        await channel.send(embed=result_embed, view=rematch_view)

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

        new_view         = GameView(table)
        new_view.message = None
        self.stop()

        await interaction.response.defer()
        if self.message:
            try:
                await self.message.delete()
            except Exception:
                pass

        new_view.message = await interaction.channel.send(
            embed=build_board_embed(table), view=new_view
        )

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

        self.started    = True
        button.disabled = True
        self.stop()

        num_others = total - 1

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

        for p in debt_players:
            stored = self.interactions.get(p.user_id)
            if stored:
                try:
                    await stored.followup.send(
                        f"<@{p.user_id}> {DEBT_WARNING}", ephemeral=False
                    )
                except Exception:
                    pass

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
        lv.interactions[uid] = interaction

        if table.is_full:
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

        if not any(p.user_id == uid for p in table.players):
            await interaction.response.send_message(
                "You're not in this lobby.", ephemeral=True
            )
            return

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

        for p in debt_players:
            stored = self.interactions.get(p.user_id)
            if stored:
                try:
                    await stored.followup.send(
                        f"<@{p.user_id}> {DEBT_WARNING}", ephemeral=False
                    )
                except Exception:
                    pass

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


class BanLuckCog(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="ht", description="Flip a coin — guess heads or tails")
    @app_commands.describe(
        choice="Pick heads or tails",
        bet="Gold to wager (leave blank or 0 for free play with tokens)",
    )
    async def cmd_cointoss(
        self,
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

        if not free_play:
            is_valid, error_msg = validate_bet_with_firewall(guild_id, uid, bet_amount)
            if not is_valid:
                await interaction.response.send_message(error_msg, ephemeral=True)
                return

        if free_play:
            from config import FREE_PLAY_TOKEN_BET as _FPT
            add_tokens(uid, -_FPT)
            result = random.choice(("heads", "tails"))
            won    = (result == choice)

            if won:
                add_tokens(uid, _FPT * 2)
            new_bal = get_tokens(uid)
            outcome = (
                f"🪙 Coin landed **{result}** — "
                + (f"you guessed right! +**1** 🪙" if won else "wrong guess. -**1** 🪙")
                + f"\n🎮 Token Balance: **{new_bal:,} 🪙**"
            )
            await interaction.response.send_message(outcome)
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
            embed.add_field(name="Bet", value=f"**{bet_amount:,} 💰** on **{choice}**", inline=True)
            view = HTDebtConfirmView(guild_id, uid, bet_amount, choice)
            await interaction.response.send_message(embed=embed, view=view)
            msg = await interaction.original_response()
            view.message = msg
            return

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

    @app_commands.command(name="bj", description="Open a Ban-Luck (21) lobby as banker")
    @app_commands.describe(bet="Gold bet per player (leave blank or 0 for free play with tokens)")
    async def cmd_bj(self, interaction: discord.Interaction, bet: int = 0) -> None:
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

        if bet > 0 and get_gold(guild_id, uid) <= 0:
            await interaction.response.send_message(f"<@{uid}> {DEBT_WARNING}")
            msg = await interaction.followup.send(embed=build_lobby_embed(table), view=view)
        else:
            await interaction.response.send_message(embed=build_lobby_embed(table), view=view)
            msg = await interaction.original_response()

        view.message = msg

    @app_commands.command(name="leave", description="Leave your current Ban-Luck lobby as a player")
    async def cmd_leave(self, interaction: discord.Interaction) -> None:
        """Slash command fallback: leave an open Ban-Luck lobby without pressing the button."""
        if not interaction.guild:
            await interaction.response.send_message(
                "This command can only be used in a server.", ephemeral=True
            )
            return

        guild_id = interaction.guild.id
        uid      = interaction.user.id

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

        target_table.players = [p for p in target_table.players if p.user_id != uid]
        active_player_ids.discard(uid)

        await interaction.response.send_message(
            f"👋 **{interaction.user.display_name}** left the Ban-Luck lobby "
            f"(banker: <@{target_table.banker_id}>, bet: {target_table.bet:,} 💰)."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BanLuckCog(bot))
