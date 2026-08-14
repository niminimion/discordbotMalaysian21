import math
from blackjack import GameTable
from texas_poker import PokerTable
from config import DEBT_TAX_RATE

active_tables:        dict[int, GameTable]  = {}
active_poker_tables:  dict[int, PokerTable] = {}
active_player_ids:    set[int]              = set()


def _status_tag(status: str, escaped: bool = False) -> str:
    """Small emoji tag appended to a player's name on the board."""
    if escaped:
        return " 🏃"
    return {"stood": " 🛑", "bust": " 💥", "special": " 🏅"}.get(status, "")


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


def _poker_cleanup(table: PokerTable) -> None:
    active_poker_tables.pop(table.host_id, None)
    active_player_ids.difference_update(table.all_player_ids)


def _check_active(uid: int, guild_id: int) -> bool:
    """Return True only if uid is genuinely in a live table/lobby in this guild.
    Removes stale active_player_ids entries caused by restarts or missed cleanups."""
    if uid not in active_player_ids:
        return False
    for table in active_tables.values():
        if table.guild_id == guild_id and any(p.user_id == uid for p in table.all_participants):
            return True
    for table in active_poker_tables.values():
        if table.guild_id == guild_id and uid in table.all_player_ids:
            return True
    active_player_ids.discard(uid)
    return False
