import asyncio
import random
import math
import discord
from discord.ext import commands
from discord import app_commands, ui
from texas_poker import PokerTable, PokerPlayer, best_hand, HAND_NAMES as POKER_HAND_NAMES
from card_renderer import render_cards_image
from config import MAX_DEBT, MAX_BET, DEBT_WARNING
from database import get_gold, add_gold
from shared import active_poker_tables, active_player_ids, _check_active, _poker_cleanup


_SUIT_EMOJI = {"♠": "♠️", "♥": "♥️", "♦": "♦️", "♣": "♣️"}


def _fmt_card(card) -> str:
    """Format one card as bold rank + coloured suit emoji: **K**♠️"""
    return f"**{card.rank}**{_SUIT_EMOJI.get(card.suit, card.suit)}"


def build_poker_lobby_embed(table: PokerTable) -> discord.Embed:
    n = len(table.players)
    embed = discord.Embed(
        title="♠️  Texas Hold'em — Lobby",
        description=(
            f"Blinds: **{table.blind:,} / {table.blind * 2:,}** 💰  |  "
            f"Players: **{n} / {table.MAX_PLAYERS}**"
        ),
        color=discord.Color.dark_green(),
    )
    seat_list = "\n".join(
        f"{i + 1}. {p.name}" + (" 👑 Host" if p.user_id == table.host_id else "")
        for i, p in enumerate(table.players)
    ) or "*Waiting for players...*"
    embed.add_field(name="Seats", value=seat_list, inline=False)
    embed.set_footer(text=f"Need at least 2 players | Host can start early | Auto-starts at {table.MAX_PLAYERS}")
    return embed


def build_poker_board_embed(table: PokerTable, *, reveal: bool = False) -> discord.Embed:
    phase_label = {
        "preflop": "Pre-Flop",
        "flop":    "Flop",
        "turn":    "Turn",
        "river":   "River",
        "showdown":"Showdown 🏆",
    }.get(table.phase, table.phase.title())

    revealed = [_fmt_card(c) for c in table.community]
    hidden   = ["🂠"] * (5 - len(table.community))
    comm_line = "  ".join(revealed + hidden)

    embed = discord.Embed(
        title=f"♠️  Texas Hold'em — {phase_label}",
        description=(
            f"**Community:** {comm_line}\n"
            f"**Pot:** {table.pot:,} 💰  |  Blinds: {table.blind:,}/{table.blind * 2:,} 💰"
        ),
        color=discord.Color.gold() if reveal else discord.Color.dark_green(),
    )

    current = table.current_player
    dealer  = table.players[table.dealer_idx] if table.players else None

    for p in table.players:
        tags = []
        if dealer and p.user_id == dealer.user_id:
            tags.append("🎴 Dealer")
        if p.status == "folded":
            tags.append("🃏 Folded")
        elif p.status == "all_in":
            tags.append("⚡ All-In")
        is_active_turn = (current and p.user_id == current.user_id and not reveal)
        prefix = "▶️ " if is_active_turn else ""
        label  = f"{prefix}{p.name}" + (f"  [{', '.join(tags)}]" if tags else "")

        if reveal and p.status != "folded":
            cards_str  = " ".join(str(c) for c in p.hole_cards)
            _, hand_nm = best_hand(p.hole_cards + table.community) if len(p.hole_cards) + len(table.community) >= 5 else ((0, []), "—")
            value = f"[{cards_str}] — *{hand_nm}*\nIn pot: **{p.total_in_pot:,}** 💰"
        elif p.status == "folded":
            value = f"[🃏 folded]\nIn pot: **{p.total_in_pot:,}** 💰"
        else:
            value = f"[🂠 🂠]\nIn pot: **{p.total_in_pot:,}** 💰"
            if p.bet_this_round > 0 and table.phase not in ("showdown",):
                value += f"  (this round: {p.bet_this_round:,})"

        embed.add_field(name=label, value=value, inline=False)

    if not reveal and current and table.phase not in ("lobby", "showdown"):
        to_call = table.current_bet - current.bet_this_round
        if to_call > 0:
            embed.set_footer(text=f"▶️ {current.name}'s turn  |  To call: {to_call:,} 💰  |  Current bet: {table.current_bet:,}")
        else:
            embed.set_footer(text=f"▶️ {current.name}'s turn  |  Can check (no outstanding bet)")

    return embed


async def resolve_poker_showdown(
    table:     PokerTable,
    game_view: "PokerGameView",
    channel:   discord.abc.Messageable,
) -> None:
    """Evaluate hands, pay the pot, send results + rematch view."""
    table.phase = "showdown"
    winners, all_results = table.determine_winners()

    n_winners  = len(winners)
    share      = table.pot // n_winners if n_winners else 0
    remainder  = table.pot % n_winners  if n_winners else 0

    lines: list[str] = []
    winner_ids = {p.user_id for p, _, _ in winners}

    for i, (p, _, hand_name) in enumerate(winners):
        my_share = share + (1 if i < remainder else 0)
        add_gold(table.guild_id, p.user_id, my_share)
        net  = my_share - p.total_in_pot
        sign = "+" if net >= 0 else ""
        cards_str = " ".join(str(c) for c in p.hole_cards)
        lines.append(
            f"🏆 **{p.name}**: {hand_name} [{cards_str}]"
            f"\n    Won **{my_share:,}** 💰  (net {sign}{net:,})"
        )

    for p, _, hand_name in all_results:
        if p.user_id in winner_ids:
            continue
        cards_str = " ".join(str(c) for c in p.hole_cards)
        lines.append(f"**{p.name}**: {hand_name} [{cards_str}] — Lost **{p.total_in_pot:,}** 💰")

    for p in table.players:
        if p.status == "folded":
            lines.append(f"**{p.name}**: 🃏 Folded — Lost **{p.total_in_pot:,}** 💰")

    board_embed = build_poker_board_embed(table, reveal=True)
    for item in game_view.children:
        item.disabled = True  # type: ignore[attr-defined]
    if game_view.message:
        try:
            await game_view.message.edit(embed=board_embed, view=game_view)
        except Exception:
            pass

    all_mentions = " ".join(f"<@{p.user_id}>" for p in table.players)
    result_embed = discord.Embed(
        title="🏆  Texas Hold'em — Showdown",
        description=f"{all_mentions}\n\n" + "\n".join(lines),
        color=discord.Color.gold(),
    )
    result_embed.set_footer(text=f"Pot: {table.pot:,} 💰 | Blinds: {table.blind:,}/{table.blind * 2:,}")

    participants = [(p.user_id, p.name) for p in table.players]
    rematch      = PokerRematchView(blind=table.blind, guild_id=table.guild_id, participants=participants)
    _poker_cleanup(table)
    await channel.send(embed=result_embed, view=rematch)


async def resolve_poker_win_by_fold(
    table:     PokerTable,
    game_view: "PokerGameView",
    winner:    PokerPlayer,
    channel:   discord.abc.Messageable,
) -> None:
    """One player left — they win without showdown (no card reveal)."""
    table.phase = "showdown"
    add_gold(table.guild_id, winner.user_id, table.pot)
    net  = table.pot - winner.total_in_pot
    sign = "+" if net >= 0 else ""

    lines = [f"🏆 **{winner.name}**: Won **{table.pot:,}** 💰 (net {sign}{net:,}) — everyone else folded"]
    for p in table.players:
        if p.status == "folded":
            lines.append(f"**{p.name}**: 🃏 Folded — Lost **{p.total_in_pot:,}** 💰")

    board_embed = build_poker_board_embed(table, reveal=False)
    for item in game_view.children:
        item.disabled = True  # type: ignore[attr-defined]
    if game_view.message:
        try:
            await game_view.message.edit(embed=board_embed, view=game_view)
        except Exception:
            pass

    all_mentions = " ".join(f"<@{p.user_id}>" for p in table.players)
    result_embed = discord.Embed(
        title="🏆  Texas Hold'em — Winner!",
        description=f"{all_mentions}\n\n" + "\n".join(lines),
        color=discord.Color.gold(),
    )
    result_embed.set_footer(text=f"Pot: {table.pot:,} 💰 | Blinds: {table.blind:,}/{table.blind * 2:,}")

    participants = [(p.user_id, p.name) for p in table.players]
    rematch      = PokerRematchView(blind=table.blind, guild_id=table.guild_id, participants=participants)
    _poker_cleanup(table)
    await channel.send(embed=result_embed, view=rematch)


class PokerRaiseModal(ui.Modal, title="💰 Raise"):
    amount_input: ui.TextInput = ui.TextInput(
        label="Raise total bet to (enter Gold amount)",
        placeholder="e.g. 500",
        min_length=1,
        max_length=10,
    )

    def __init__(self, game_view: "PokerGameView") -> None:
        super().__init__()
        table   = game_view.table
        current = table.current_player
        min_r   = table.current_bet + table.blind
        self.amount_input.placeholder = (
            f"Min raise: {min_r:,} | Current pot: {table.pot:,}"
        )
        self.game_view = game_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        game_view = self.game_view
        table     = game_view.table
        uid       = interaction.user.id

        try:
            raise_to = int(self.amount_input.value.replace(",", "").strip())
        except ValueError:
            await interaction.response.send_message("❌ Invalid amount — enter a number.", ephemeral=True)
            return

        current = table.current_player
        if current is None or current.user_id != uid:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return

        min_raise = table.current_bet + table.blind
        if raise_to < min_raise:
            await interaction.response.send_message(
                f"❌ Minimum raise is **{min_raise:,}** 💰 (current bet {table.current_bet:,} + 1 blind {table.blind:,}).",
                ephemeral=True,
            )
            return

        amount_to_pay = raise_to - current.bet_this_round
        player_gold   = get_gold(table.guild_id, uid)
        if player_gold < amount_to_pay:
            await interaction.response.send_message(
                f"❌ Not enough gold. You have **{player_gold:,}** 💰, need **{amount_to_pay:,}** 💰 to raise to {raise_to:,}.",
                ephemeral=True,
            )
            return

        player = table.get_player(uid)
        game_view._cancel_turn_timer()
        add_gold(table.guild_id, uid, -amount_to_pay)
        table.apply_raise(uid, raise_to)

        await interaction.response.defer()
        await game_view._post_board(interaction.channel)

        nxt = table.current_player
        announce = f"💰 **{interaction.user.display_name}** raised to **{raise_to:,}** 💰"
        if nxt:
            announce += f"  |  ▶️ <@{nxt.user_id}> your turn!"
        await interaction.channel.send(announce)
        game_view._start_turn_timer(interaction.channel)

        if player:
            await game_view._show_cards_followup(interaction, player)


class PokerGameView(ui.View):
    """
    Attached to the public poker board.
    Fold / Check-Call / Raise / All-In are turn-gated (current player only).
    My Cards is available to any participant — ephemeral.
    """

    def __init__(self, table: PokerTable) -> None:
        super().__init__(timeout=300)
        self.table   = table
        self.message: discord.Message | None = None
        self._turn_task: asyncio.Task | None = None

    def _sync_check_call_label(self) -> None:
        """Update the Check/Call button label to reflect current game state."""
        table   = self.table
        current = table.current_player
        to_call = (table.current_bet - current.bet_this_round) if current else 0
        new_label = "✅ Check" if to_call <= 0 else f"📞 Call ({to_call:,} 💰)"
        for item in self.children:
            if isinstance(item, ui.Button) and item.row == 0 and (
                (item.label or "").startswith("✅") or (item.label or "").startswith("📞")
            ):
                item.label = new_label
                break

    async def _post_board(
        self,
        channel: discord.abc.Messageable,
        footer:  str = "",
    ) -> None:
        """Delete old board, render community card image, send fresh board."""
        self._sync_check_call_label()
        embed = build_poker_board_embed(self.table)
        if footer:
            embed.set_footer(text=footer)

        files: list[discord.File] = []
        if self.table.community:
            buf = await render_cards_image(self.table.community)
            if buf:
                embed.set_image(url="attachment://community.png")
                files.append(discord.File(buf, "community.png"))

        if self.message:
            try:
                await self.message.delete()
            except Exception:
                pass

        self.message = await channel.send(embed=embed, view=self, files=files)

    async def _show_cards_followup(
        self,
        interaction: discord.Interaction,
        player:      PokerPlayer,
    ) -> None:
        """Send the player their hole cards + current best hand as an ephemeral followup."""
        if not player or not player.hole_cards:
            return
        cards_str = " ".join(_fmt_card(c) for c in player.hole_cards)
        lines     = [f"🂠 **Your hole cards:** {cards_str}"]
        if self.table.community:
            _, hand_nm = best_hand(player.hole_cards + self.table.community)
            lines.append(f"🃏 **Best hand so far:** {hand_nm}")
        content = "\n".join(lines)
        buf     = await render_cards_image(player.hole_cards)
        try:
            if buf:
                await interaction.followup.send(
                    content, file=discord.File(buf, "hole_cards.png"), ephemeral=True
                )
            else:
                await interaction.followup.send(content, ephemeral=True)
        except Exception:
            pass

    async def _handle_result(
        self,
        result:      str,
        interaction: discord.Interaction,
        action_desc: str,
    ) -> None:
        """Common post-action handler: settle, advance phase, or continue."""
        table   = self.table
        channel = interaction.channel

        if result == "win":
            winner = table.active_players[0]
            self._cancel_turn_timer()
            for item in self.children:
                item.disabled = True  # type: ignore[attr-defined]
            await interaction.response.defer()
            await resolve_poker_win_by_fold(table, self, winner, channel)
            return

        if result == "round_over":
            self._cancel_turn_timer()
            phase_result = table.advance_phase()

            if not table.can_act_players and phase_result != "showdown":
                table.fast_forward_to_showdown()
                phase_result = "showdown"

            if phase_result == "showdown":
                for item in self.children:
                    item.disabled = True  # type: ignore[attr-defined]
                await interaction.response.defer()
                await resolve_poker_showdown(table, self, channel)
                return

            await interaction.response.defer()
            await self._post_board(channel, footer=f"New round: {table.phase.title()}")
            nxt = table.current_player
            if nxt:
                await channel.send(
                    f"🃏 **{table.phase.title()}!**  ▶️ <@{nxt.user_id}> your turn!"
                )
            self._start_turn_timer(channel)
            return

        await interaction.response.defer()
        await self._post_board(channel)
        nxt = table.current_player
        if nxt:
            await channel.send(f"▶️ <@{nxt.user_id}> it's your turn!")
        self._start_turn_timer(channel)

    def _cancel_turn_timer(self) -> None:
        if self._turn_task and not self._turn_task.done():
            self._turn_task.cancel()
        self._turn_task = None

    def _start_turn_timer(self, channel: discord.abc.Messageable) -> None:
        self._cancel_turn_timer()
        table = self.table
        if table.phase not in ("preflop", "flop", "turn", "river"):
            return
        current = table.current_player
        if current is None:
            return
        self._turn_task = asyncio.create_task(self._auto_fold_on_timeout(channel, current.user_id))

    async def _auto_fold_on_timeout(self, channel: discord.abc.Messageable, expected_uid: int) -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return
        table   = self.table
        current = table.current_player
        if current is None or current.user_id != expected_uid:
            return
        if table.phase not in ("preflop", "flop", "turn", "river"):
            return

        await channel.send(f"⏰ <@{expected_uid}> timed out — auto-folded!")

        result = table.apply_fold(expected_uid)

        if result == "win":
            winner = table.active_players[0]
            for item in self.children:
                item.disabled = True  # type: ignore[attr-defined]
            await resolve_poker_win_by_fold(table, self, winner, channel)
            return

        if result == "round_over":
            phase_result = table.advance_phase()
            if not table.can_act_players and phase_result != "showdown":
                table.fast_forward_to_showdown()
                phase_result = "showdown"
            if phase_result == "showdown":
                for item in self.children:
                    item.disabled = True  # type: ignore[attr-defined]
                await resolve_poker_showdown(table, self, channel)
                return
            await self._post_board(channel, footer=f"New round: {table.phase.title()}")
            nxt = table.current_player
            if nxt:
                await channel.send(f"🃏 **{table.phase.title()}!**  ▶️ <@{nxt.user_id}> your turn!")
            self._start_turn_timer(channel)
            return

        await self._post_board(channel)
        nxt = table.current_player
        if nxt:
            await channel.send(f"▶️ <@{nxt.user_id}> it's your turn!")
        self._start_turn_timer(channel)

    @ui.button(label="🃏 Fold", style=discord.ButtonStyle.danger, row=0)
    async def fold_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        table   = self.table
        current = table.current_player

        if current is None or interaction.user.id != current.user_id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return
        if table.phase not in ("preflop", "flop", "turn", "river"):
            await interaction.response.send_message("Game is not in a betting round.", ephemeral=True)
            return

        self._cancel_turn_timer()
        player = table.get_player(interaction.user.id)
        name   = interaction.user.display_name
        result = table.apply_fold(interaction.user.id)
        await interaction.channel.send(f"🃏 **{name}** folded.")
        await self._handle_result(result, interaction, "fold")
        if player:
            await self._show_cards_followup(interaction, player)

    @ui.button(label="✅ Check", style=discord.ButtonStyle.primary, row=0)
    async def check_call_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        table   = self.table
        uid     = interaction.user.id
        current = table.current_player

        if current is None or uid != current.user_id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return
        if table.phase not in ("preflop", "flop", "turn", "river"):
            await interaction.response.send_message("Game is not in a betting round.", ephemeral=True)
            return

        to_call = table.current_bet - current.bet_this_round

        self._cancel_turn_timer()

        player = table.get_player(uid)
        if to_call <= 0:
            result = table.apply_check(uid)
            await interaction.channel.send(f"✅ **{interaction.user.display_name}** checked.")
            await self._handle_result(result, interaction, "check")
            if player:
                await self._show_cards_followup(interaction, player)
        else:
            gold = get_gold(table.guild_id, uid)
            if gold <= 0:
                await interaction.response.send_message(
                    f"❌ Not enough gold to call **{to_call:,}** 💰. Use ⚡ All In instead.",
                    ephemeral=True,
                )
                self._start_turn_timer(interaction.channel)
                return
            if gold < to_call:
                add_gold(table.guild_id, uid, -gold)
                amount, result = table.apply_allin(uid, gold)
                await interaction.channel.send(
                    f"⚡ **{interaction.user.display_name}** can't afford the full call — goes **All-In** for **{gold:,}** 💰!"
                )
                await self._handle_result(result, interaction, f"all-in {gold:,}")
                if player:
                    await self._show_cards_followup(interaction, player)
                return

            add_gold(table.guild_id, uid, -to_call)
            amount, result = table.apply_call(uid)
            await interaction.channel.send(
                f"📞 **{interaction.user.display_name}** called **{to_call:,}** 💰."
            )
            await self._handle_result(result, interaction, f"call {to_call:,}")
            if player:
                await self._show_cards_followup(interaction, player)

    @ui.button(label="💰 Raise", style=discord.ButtonStyle.success, row=0)
    async def raise_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        table   = self.table
        current = table.current_player

        if current is None or interaction.user.id != current.user_id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return
        if table.phase not in ("preflop", "flop", "turn", "river"):
            await interaction.response.send_message("Game is not in a betting round.", ephemeral=True)
            return

        gold = get_gold(table.guild_id, interaction.user.id)
        min_raise = table.current_bet + table.blind
        min_pay   = min_raise - current.bet_this_round
        if gold < min_pay:
            await interaction.response.send_message(
                f"❌ Not enough gold to raise. Min raise costs **{min_pay:,}** 💰 but you have **{gold:,}**.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(PokerRaiseModal(self))

    @ui.button(label="⚡ All In", style=discord.ButtonStyle.danger, row=0)
    async def allin_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        table   = self.table
        uid     = interaction.user.id
        current = table.current_player

        if current is None or uid != current.user_id:
            await interaction.response.send_message("It's not your turn!", ephemeral=True)
            return
        if table.phase not in ("preflop", "flop", "turn", "river"):
            await interaction.response.send_message("Game is not in a betting round.", ephemeral=True)
            return

        gold = get_gold(table.guild_id, uid)
        if gold <= 0:
            await interaction.response.send_message("❌ You have no gold left to go all-in with.", ephemeral=True)
            return

        player = table.get_player(uid)
        self._cancel_turn_timer()
        add_gold(table.guild_id, uid, -gold)
        amount, result = table.apply_allin(uid, gold)

        await interaction.channel.send(
            f"⚡ **{interaction.user.display_name}** goes **All-In** for **{gold:,}** 💰!"
        )
        await self._handle_result(result, interaction, f"all-in {gold:,}")
        if player:
            await self._show_cards_followup(interaction, player)

    @ui.button(label="🂠 My Cards", style=discord.ButtonStyle.grey, row=1)
    async def my_cards_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        table = self.table
        uid   = interaction.user.id
        p     = table.get_player(uid)
        if p is None:
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return
        if not p.hole_cards:
            await interaction.response.send_message("No cards dealt yet.", ephemeral=True)
            return

        cards_str = " ".join(str(c) for c in p.hole_cards)
        lines     = [f"🂠 **Your hole cards:** {cards_str}"]

        if table.community:
            score, hand_nm = best_hand(p.hole_cards + table.community)
            lines.append(f"🃏 **Best hand so far:** {hand_nm}")

        content = "\n".join(lines)
        buf     = await render_cards_image(p.hole_cards)
        if buf:
            await interaction.response.send_message(
                content, file=discord.File(buf, "hole_cards.png"), ephemeral=True
            )
        else:
            await interaction.response.send_message(content, ephemeral=True)

    @ui.button(label="🔄 Refresh", style=discord.ButtonStyle.grey, row=1)
    async def refresh_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        table = self.table
        uid   = interaction.user.id
        if table.get_player(uid) is None:
            await interaction.response.send_message("You're not in this game.", ephemeral=True)
            return
        if table.phase == "showdown":
            await interaction.response.send_message("Game has ended.", ephemeral=True)
            return

        new_view         = PokerGameView(table)
        new_view.message = None
        new_view._sync_check_call_label()
        self.stop()

        await interaction.response.defer()
        if self.message:
            try:
                await self.message.delete()
            except Exception:
                pass
        new_view.message = await interaction.channel.send(
            embed=build_poker_board_embed(table), view=new_view
        )

    async def on_timeout(self) -> None:
        self._cancel_turn_timer()
        table = self.table
        if table.phase in ("lobby", "showdown"):
            return

        table.phase = "showdown"
        for p in table.players:
            if p.total_in_pot > 0:
                add_gold(table.guild_id, p.user_id, p.total_in_pot)

        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]

        _poker_cleanup(table)
        if self.message:
            try:
                await self.message.edit(
                    content="⏰ Poker table timed out — all bets refunded.", view=self
                )
            except Exception:
                pass


class PokerLobbyView(ui.View):
    """Waiting room for Texas Hold'em. Host can start early; auto-starts when full."""

    def __init__(self, table: PokerTable) -> None:
        super().__init__(timeout=300)
        self.table    = table
        self.resolved = False
        self.message: discord.Message | None = None

    async def _start_game(self, interaction: discord.Interaction) -> None:
        table = self.table
        if len(table.players) < 2:
            await interaction.response.send_message(
                "❌ Need at least **2 players** to start.", ephemeral=True
            )
            return

        self.resolved = True
        self.stop()

        for p in table.players:
            if get_gold(table.guild_id, p.user_id) <= 0:
                p.in_debt = True

        sb_uid, sb_amt, bb_uid, bb_amt = table.start()
        add_gold(table.guild_id, sb_uid, -sb_amt)
        add_gold(table.guild_id, bb_uid, -bb_amt)

        game_view = PokerGameView(table)
        game_view._sync_check_call_label()

        board_embed = build_poker_board_embed(table)
        await interaction.response.edit_message(
            content="♠️ **Texas Hold'em started!**",
            embed=board_embed,
            view=game_view,
        )
        game_view.message = interaction.message

        for p in table.players:
            if p.in_debt:
                try:
                    await interaction.followup.send(f"<@{p.user_id}> {DEBT_WARNING}", ephemeral=False)
                except Exception:
                    pass

        sb_player = table.get_player(sb_uid)
        bb_player = table.get_player(bb_uid)
        dealer    = table.players[table.dealer_idx]
        await interaction.followup.send(
            f"🎴 **Dealer:** {dealer.name}  |  "
            f"SB: {sb_player.name} ({sb_amt:,} 💰)  |  "
            f"BB: {bb_player.name} ({bb_amt:,} 💰)"
        )

        for p in table.players:
            member = interaction.guild.get_member(p.user_id)
            if not member:
                continue
            cards_str = " ".join(_fmt_card(c) for c in p.hole_cards)
            content   = f"🂠 **Your hole cards:** {cards_str}"
            buf = await render_cards_image(p.hole_cards)
            try:
                if buf:
                    await member.send(content, file=discord.File(buf, "hole_cards.png"))
                else:
                    await member.send(content)
            except Exception:
                pass

        first = table.current_player
        if first:
            await interaction.followup.send(f"▶️ <@{first.user_id}> your turn first!")

        game_view._start_turn_timer(interaction.channel)

    @ui.button(label="🪑 Join", style=discord.ButtonStyle.primary)
    async def join_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid   = interaction.user.id
        table = self.table

        if self.resolved:
            await interaction.response.send_message("This lobby has already started.", ephemeral=True)
            return
        if _check_active(uid, table.guild_id):
            await interaction.response.send_message(
                "You're already in a game or lobby.", ephemeral=True
            )
            return
        if not table.add_player(uid, interaction.user.display_name):
            await interaction.response.send_message("Seat unavailable (full or already joined).", ephemeral=True)
            return

        active_player_ids.add(uid)

        if table.is_full:
            await self._start_game(interaction)
            return

        await interaction.response.edit_message(embed=build_poker_lobby_embed(table), view=self)

    @ui.button(label="🚪 Leave", style=discord.ButtonStyle.secondary)
    async def leave_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid   = interaction.user.id
        table = self.table

        if self.resolved:
            await interaction.response.send_message("Game has already started.", ephemeral=True)
            return
        if uid == table.host_id:
            await interaction.response.send_message(
                "Host cannot leave — close the lobby with /pokerstop or start the game.", ephemeral=True
            )
            return

        p = table.get_player(uid)
        if p is None:
            await interaction.response.send_message("You're not in this lobby.", ephemeral=True)
            return

        table.players = [x for x in table.players if x.user_id != uid]
        active_player_ids.discard(uid)
        await interaction.response.edit_message(embed=build_poker_lobby_embed(table), view=self)

    @ui.button(label="▶️ Start Game", style=discord.ButtonStyle.success)
    async def start_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        if interaction.user.id != self.table.host_id:
            await interaction.response.send_message("Only the host can start the game.", ephemeral=True)
            return
        if self.resolved:
            await interaction.response.send_message("Game already started.", ephemeral=True)
            return
        await self._start_game(interaction)

    async def on_timeout(self) -> None:
        if self.resolved:
            return
        self.resolved = True
        table = self.table
        _poker_cleanup(table)
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(content="⏰ Poker lobby timed out.", embed=None, view=self)
            except Exception:
                pass


class PokerRematchView(ui.View):
    """Unanimous vote required — same rules as Ban-Luck RematchView."""

    def __init__(self, blind: int, guild_id: int, participants: list[tuple[int, str]]) -> None:
        super().__init__(timeout=120)
        self.blind        = blind
        self.guild_id     = guild_id
        self.participants = participants
        self.participant_set = {uid for uid, _ in participants}
        self.votes:       set[int]                       = set()
        self.started      = False

    @ui.button(label="🔄 Rematch", style=discord.ButtonStyle.green)
    async def rematch_btn(self, interaction: discord.Interaction, button: ui.Button) -> None:
        uid   = interaction.user.id
        total = len(self.participants)

        if uid not in self.participant_set:
            await interaction.response.send_message(
                "Only players from the previous game can vote.", ephemeral=True
            )
            return
        if self.started:
            await interaction.response.send_message("Rematch already started!", ephemeral=True)
            return
        if _check_active(uid, self.guild_id):
            await interaction.response.send_message(
                "You're already in another game.", ephemeral=True
            )
            return
        if uid in self.votes:
            await interaction.response.send_message(
                "You already voted ✅  Waiting for others…", ephemeral=True
            )
            return

        self.votes.add(uid)
        count = len(self.votes)
        button.label = f"🔄 Rematch ({count}/{total})"

        if count < total:
            waiting = [name for uid2, name in self.participants if uid2 not in self.votes]
            await interaction.response.edit_message(
                content=f"⏳ Waiting for **{', '.join(waiting)}** to vote…", view=self
            )
            return

        self.started    = True
        button.disabled = True
        self.stop()

        eligible = [
            (uid2, name)
            for uid2, name in self.participants
            if uid2 not in active_player_ids
        ]
        if not eligible:
            await interaction.response.edit_message(
                content="❌ Rematch cancelled — all players are busy.", embed=None, view=None
            )
            return

        host_id, host_name = random.choice(eligible)
        table = PokerTable(guild_id=self.guild_id, host_id=host_id, blind=self.blind)
        for uid2, name in self.participants:
            table.add_player(uid2, name)

        active_poker_tables[host_id] = table
        for p in table.players:
            active_player_ids.add(p.user_id)

        for p in table.players:
            if get_gold(self.guild_id, p.user_id) <= 0:
                p.in_debt = True

        sb_uid, sb_amt, bb_uid, bb_amt = table.start()
        add_gold(self.guild_id, sb_uid, -sb_amt)
        add_gold(self.guild_id, bb_uid, -bb_amt)

        game_view = PokerGameView(table)
        game_view._sync_check_call_label()

        board_embed = build_poker_board_embed(table)

        await interaction.response.edit_message(
            content=f"🔄 **Rematch!** New host: **{host_name}**",
            embed=board_embed,
            view=game_view,
        )
        game_view.message = interaction.message

        sb_player = table.get_player(sb_uid)
        bb_player = table.get_player(bb_uid)
        dealer    = table.players[table.dealer_idx]
        await interaction.followup.send(
            f"🎴 Dealer: {dealer.name}  |  SB: {sb_player.name} ({sb_amt:,} 💰)  |  "
            f"BB: {bb_player.name} ({bb_amt:,} 💰)\n"
            f"Click **🂠 My Cards** to see your hole cards!"
        )

        first = table.current_player
        if first:
            await interaction.followup.send(f"▶️ <@{first.user_id}> your turn first!")

        game_view._start_turn_timer(interaction.channel)


class PokerCog(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="poker", description="Start a Texas Hold'em poker table")
    @app_commands.describe(blind="Small blind in Gold (big blind = 2×). Default 50.")
    async def cmd_poker(self, interaction: discord.Interaction, blind: int = 50) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        uid      = interaction.user.id
        guild_id = interaction.guild.id
        name     = interaction.user.display_name

        if blind < 10:
            await interaction.response.send_message(
                "❌ Minimum small blind is **10** 💰 (big blind = 20).", ephemeral=True
            )
            return
        if blind * 2 > MAX_BET:
            await interaction.response.send_message(
                f"❌ Big blind cannot exceed the bet cap of **{MAX_BET:,}** 💰.", ephemeral=True
            )
            return
        if _check_active(uid, guild_id):
            await interaction.response.send_message(
                "You already have an open lobby or are in a game.", ephemeral=True
            )
            return

        table = PokerTable(guild_id=guild_id, host_id=uid, blind=blind)
        table.add_player(uid, name)

        active_poker_tables[uid] = table
        active_player_ids.add(uid)

        lobby_view = PokerLobbyView(table)
        await interaction.response.send_message(embed=build_poker_lobby_embed(table), view=lobby_view)
        lobby_view.message = await interaction.original_response()

    @app_commands.command(name="pokerstop", description="Close your open Texas Hold'em lobby (host only)")
    async def cmd_pokerstop(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        uid   = interaction.user.id
        table = active_poker_tables.get(uid)
        if table is None or table.guild_id != interaction.guild.id:
            await interaction.response.send_message("You don't have an open poker lobby.", ephemeral=True)
            return
        if table.phase != "lobby":
            await interaction.response.send_message(
                "Game is already running — it can't be stopped from here.", ephemeral=True
            )
            return
        _poker_cleanup(table)
        await interaction.response.send_message("♠️ Poker lobby closed.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PokerCog(bot))
