"""
texas_poker.py — Pure game logic for Texas Hold'em Poker
---------------------------------------------------------
Classes:
  PokerPlayer — one player's in-hand state (hole cards, bets, status)
  PokerTable  — full table state machine (lobby → preflop → flop → turn → river → showdown)

Hand evaluation:
  best_hand(cards) → (score_tuple, hand_name_str)
  Higher score_tuple = better hand (Python tuple comparison).

Gold accounting is handled by main.py; this module only tracks running totals
(pot, bet_this_round, total_in_pot) and returns how much to deduct at each action.
"""

import random
from itertools import combinations
from collections import Counter
from dataclasses import dataclass, field

from blackjack import Card, Deck  # reuse identical Card/Deck


# ---------------------------------------------------------------------------
# Hand evaluation
# ---------------------------------------------------------------------------

RANK_VALUES: dict[str, int] = {
    '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7,
    '8': 8, '9': 9, '10': 10, 'J': 11, 'Q': 12, 'K': 13, 'A': 14,
}

HAND_NAMES: dict[int, str] = {
    9: "Royal Flush 👑",
    8: "Straight Flush 🌊",
    7: "Four of a Kind 🎰",
    6: "Full House 🏠",
    5: "Flush 🎨",
    4: "Straight ➡️",
    3: "Three of a Kind 🎲",
    2: "Two Pair 👯",
    1: "One Pair",
    0: "High Card",
}


def _score_five(cards: tuple) -> tuple:
    """Score exactly 5 cards. Returns (hand_rank_int, tiebreaker_list)."""
    vals = sorted([RANK_VALUES[c.rank] for c in cards], reverse=True)
    suits = [c.suit for c in cards]
    is_flush = len(set(suits)) == 1

    is_straight = False
    straight_high = 0
    if len(set(vals)) == 5 and vals[0] - vals[4] == 4:
        is_straight, straight_high = True, vals[0]
    elif set(vals) == {14, 2, 3, 4, 5}:  # A-2-3-4-5 wheel
        is_straight, straight_high = True, 5
        vals = [5, 4, 3, 2, 1]

    cnt = Counter(vals)
    # Sort by (frequency DESC, value DESC) for tiebreaking
    groups = sorted(cnt.keys(), key=lambda v: (cnt[v], v), reverse=True)
    freq = [cnt[v] for v in groups]

    if is_straight and is_flush:
        return (9 if vals[0] == 14 and vals[1] == 13 else 8, [straight_high])
    if freq[0] == 4:
        return (7, groups)
    if freq[0] == 3 and len(freq) > 1 and freq[1] == 2:
        return (6, groups)
    if is_flush:
        return (5, vals)
    if is_straight:
        return (4, [straight_high])
    if freq[0] == 3:
        return (3, groups)
    if freq[0] == 2 and len(freq) > 1 and freq[1] == 2:
        return (2, groups)
    if freq[0] == 2:
        return (1, groups)
    return (0, vals)


def best_hand(all_cards: list) -> tuple:
    """
    Best 5-card hand from up to 7 cards.
    Returns (score_tuple, hand_name_str).
    """
    if len(all_cards) < 5:
        vals = sorted([RANK_VALUES[c.rank] for c in all_cards], reverse=True)
        return (0, vals), HAND_NAMES[0]
    score = max(_score_five(combo) for combo in combinations(all_cards, 5))
    return score, HAND_NAMES[score[0]]


# ---------------------------------------------------------------------------
# PokerPlayer
# ---------------------------------------------------------------------------

@dataclass
class PokerPlayer:
    """One player's state within a PokerTable hand."""
    user_id: int
    name: str
    hole_cards: list = field(default_factory=list)
    bet_this_round: int = 0   # amount committed in the current betting round
    total_in_pot: int = 0     # cumulative gold put into pot across all rounds
    status: str = "active"    # "active" | "folded" | "all_in"
    in_debt: bool = False


# ---------------------------------------------------------------------------
# PokerTable
# ---------------------------------------------------------------------------

@dataclass
class PokerTable:
    """
    Full state for one Texas Hold'em table.

    State machine: lobby → preflop → flop → turn → river → showdown

    Blinds: small blind = blind, big blind = blind * 2.
    main.py deducts/adds gold from the DB at each action;
    PokerTable only tracks pot and per-player contribution totals.
    """
    guild_id: int
    host_id:  int
    blind:    int   # small blind; big blind = blind * 2

    players:         list = field(default_factory=list)   # list[PokerPlayer], in seat order
    community:       list = field(default_factory=list)   # list[Card], 0-5 public cards
    deck:            Deck = field(default_factory=Deck)
    pot:             int  = 0
    phase:           str  = "lobby"
    current_bet:     int  = 0     # highest bet in the current betting round
    dealer_idx:      int  = 0     # index into self.players
    players_to_act:  list = field(default_factory=list)  # list[int] user_ids still to act

    MAX_PLAYERS: int = 8

    # ------------------------------------------------------------------ Lobby

    def add_player(self, uid: int, name: str) -> bool:
        if self.phase != "lobby" or len(self.players) >= self.MAX_PLAYERS:
            return False
        if any(p.user_id == uid for p in self.players):
            return False
        self.players.append(PokerPlayer(uid, name))
        return True

    @property
    def is_full(self) -> bool:
        return len(self.players) >= self.MAX_PLAYERS

    # ----------------------------------------------------------------- Helpers

    @property
    def all_player_ids(self) -> set:
        return {p.user_id for p in self.players}

    @property
    def active_players(self) -> list:
        """Players still in the hand (not folded)."""
        return [p for p in self.players if p.status != "folded"]

    @property
    def can_act_players(self) -> list:
        """Players who can still make decisions (active, not all-in)."""
        return [p for p in self.players if p.status == "active"]

    def get_player(self, uid: int):
        return next((p for p in self.players if p.user_id == uid), None)

    @property
    def current_player(self):
        if not self.players_to_act:
            return None
        return self.get_player(self.players_to_act[0])

    def _build_action_order(self, start_idx: int) -> list:
        """Build list of active (not folded, not all-in) player IDs clockwise from start_idx."""
        n = len(self.players)
        result = []
        for i in range(n):
            idx = (start_idx + i) % n
            p = self.players[idx]
            if p.status == "active":
                result.append(p.user_id)
        return result

    # ----------------------------------------------------------- Game start

    def start(self) -> tuple:
        """
        Deal hole cards, post blinds, set up preflop action order.
        Returns (sb_uid, sb_amount, bb_uid, bb_amount) — main.py deducts these from Gold.
        """
        self.deck = Deck()
        self.phase = "preflop"
        self.dealer_idx = random.randint(0, len(self.players) - 1)

        # Deal 2 hole cards to each player
        for _ in range(2):
            for p in self.players:
                p.hole_cards.append(self.deck.deal())

        n = len(self.players)
        if n == 2:
            # Heads-up: dealer is SB, other is BB
            sb_idx, bb_idx = self.dealer_idx, (self.dealer_idx + 1) % n
        else:
            sb_idx = (self.dealer_idx + 1) % n
            bb_idx = (self.dealer_idx + 2) % n

        sb, bb = self.players[sb_idx], self.players[bb_idx]
        sb_amount, bb_amount = self.blind, self.blind * 2

        sb.bet_this_round = sb_amount
        sb.total_in_pot   = sb_amount
        bb.bet_this_round = bb_amount
        bb.total_in_pot   = bb_amount
        self.pot         = sb_amount + bb_amount
        self.current_bet = bb_amount

        # Preflop: UTG (left of BB) acts first; BB gets to act last (option to raise)
        utg_idx = sb_idx if n == 2 else (bb_idx + 1) % n
        self.players_to_act = self._build_action_order(utg_idx)
        if bb.user_id not in self.players_to_act:
            self.players_to_act.append(bb.user_id)

        return sb.user_id, sb_amount, bb.user_id, bb_amount

    # ----------------------------------------------------------- Actions

    def apply_fold(self, uid: int) -> str:
        """
        Returns:
          'win'        — only 1 active player left (last player wins pot)
          'round_over' — betting round ended
          'continue'   — hand continues, next player acts
        """
        player = self.get_player(uid)
        if player:
            player.status = "folded"
        if uid in self.players_to_act:
            self.players_to_act.remove(uid)
        if len(self.active_players) == 1:
            return "win"
        if not self.players_to_act:
            return "round_over"
        return "continue"

    def apply_check(self, uid: int) -> str:
        """Returns 'round_over' or 'continue'."""
        if uid in self.players_to_act:
            self.players_to_act.remove(uid)
        return "round_over" if not self.players_to_act else "continue"

    def apply_call(self, uid: int) -> tuple:
        """
        Returns (amount_paid, result_str).
        amount_paid is how much gold to deduct from the player.
        """
        player = self.get_player(uid)
        amount = self.current_bet - player.bet_this_round
        player.bet_this_round = self.current_bet
        player.total_in_pot  += amount
        self.pot             += amount
        if uid in self.players_to_act:
            self.players_to_act.remove(uid)
        return amount, ("round_over" if not self.players_to_act else "continue")

    def apply_raise(self, uid: int, raise_to: int) -> tuple:
        """
        raise_to = the new total bet level this player commits to.
        Returns (amount_paid, 'continue').
        Everyone else who is active must act again.
        """
        player = self.get_player(uid)
        amount = raise_to - player.bet_this_round
        player.bet_this_round = raise_to
        player.total_in_pot  += amount
        self.pot             += amount
        self.current_bet      = raise_to
        if uid in self.players_to_act:
            self.players_to_act.remove(uid)
        queued = set(self.players_to_act)
        for p in self.players:
            if p.status == "active" and p.user_id != uid and p.user_id not in queued:
                self.players_to_act.append(p.user_id)
        return amount, "continue"

    def apply_allin(self, uid: int, player_gold: int) -> tuple:
        """
        Player goes all-in with their remaining gold.
        Returns (amount_paid, result_str).
        """
        player = self.get_player(uid)
        amount         = player_gold
        new_total_bet  = player.bet_this_round + amount
        was_raise      = new_total_bet > self.current_bet

        player.bet_this_round = new_total_bet
        player.total_in_pot  += amount
        self.pot             += amount
        player.status         = "all_in"

        if uid in self.players_to_act:
            self.players_to_act.remove(uid)

        if was_raise:
            self.current_bet = new_total_bet
            queued = set(self.players_to_act)
            for p in self.players:
                if p.status == "active" and p.user_id != uid and p.user_id not in queued:
                    self.players_to_act.append(p.user_id)

        if not self.players_to_act or not self.can_act_players:
            return amount, "round_over"
        return amount, "continue"

    # ------------------------------------------------- Phase advancement

    def advance_phase(self) -> str:
        """
        End current betting round, deal community cards for next phase.
        Returns 'showdown' if we should settle now, 'continue' otherwise.
        """
        phase_order = ["preflop", "flop", "turn", "river"]
        if self.phase not in phase_order:
            self.phase = "showdown"
            return "showdown"

        idx = phase_order.index(self.phase)
        if idx >= len(phase_order) - 1:
            self.phase = "showdown"
            return "showdown"

        self.phase = phase_order[idx + 1]

        if self.phase == "flop":
            self.community = [self.deck.deal() for _ in range(3)]
        else:
            self.community.append(self.deck.deal())

        # Reset per-round bets
        for p in self.players:
            p.bet_this_round = 0
        self.current_bet = 0

        # Action starts from first active player left of dealer
        n = len(self.players)
        for i in range(1, n + 1):
            idx2 = (self.dealer_idx + i) % n
            if self.players[idx2].status == "active":
                self.players_to_act = self._build_action_order(idx2)
                return "continue"

        # No one can act — go straight to showdown
        self.phase = "showdown"
        return "showdown"

    def fast_forward_to_showdown(self) -> None:
        """Deal all remaining community cards with no betting (all-in scenario)."""
        while self.phase not in ("showdown", "river"):
            phases = ["preflop", "flop", "turn"]
            if self.phase in phases:
                idx = phases.index(self.phase)
                self.phase = ["flop", "turn", "river"][idx]
                if self.phase == "flop":
                    self.community = [self.deck.deal() for _ in range(3)]
                else:
                    self.community.append(self.deck.deal())
            else:
                break
        if self.phase == "river":
            if len(self.community) < 5:
                self.community.append(self.deck.deal())
            self.phase = "showdown"

    # --------------------------------------------------------- Showdown

    def determine_winners(self) -> tuple:
        """
        Evaluate all non-folded players.
        Returns (winners_list, all_results_list) where each entry is
        (PokerPlayer, score_tuple, hand_name_str).
        winners_list contains all players tied for the best hand.
        """
        results = []
        for p in self.active_players:
            score, hand_name = best_hand(p.hole_cards + self.community)
            results.append((p, score, hand_name))
        results.sort(key=lambda x: x[1], reverse=True)

        if not results:
            return [], []

        best_score = results[0][1]
        winners = [(p, s, h) for p, s, h in results if s == best_score]
        return winners, results
