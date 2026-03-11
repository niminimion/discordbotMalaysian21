"""
blackjack.py — Pure game logic for Malaysian Ban-Luck (21)
----------------------------------------------------------
IMPORTANT: This is NOT standard Blackjack. Key differences:
  - Ace value is dynamic based on how many cards are in the hand
  - Players MUST Hit if score < 16 (Minimum 16 Rule)
  - 15/16 Escape: surrender on exact 15 or 16 with 2 initial cards
  - Unique special hands: Ban-Ban, Ban-Luck, Double, 五龙, 7-7-7
  - Max payout is 5× (economy cap)

Classes:
  Card        — immutable playing card (suit + rank + image_code)
  Deck        — 52-card deck, shuffled, no replacement
  Hand        — Ban-Luck hand with dynamic Ace scoring + special detection
  PlayerState — one participant's in-game state (hand + status + escaped)
  GameTable   — full multi-player table state machine (lobby → playing → finished)
"""

import random
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Card
# ---------------------------------------------------------------------------

SUITS: tuple[str, ...] = ("♠", "♥", "♦", "♣")
RANKS: tuple[str, ...] = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")

FACE_RANKS: frozenset[str] = frozenset({"10", "J", "Q", "K"})


@dataclass(frozen=True)
class Card:
    """Immutable value type — frozen=True means hashable and mutation-proof."""
    suit: str
    rank: str

    def __str__(self) -> str:
        return f"{self.suit}{self.rank}"

    @property
    def base_value(self) -> int:
        """
        Non-Ace base value. Face cards = 10, Ace = 0 (handled separately
        in Hand.score based on card count), numerics = face value.
        """
        if self.rank == "A":
            return 0   # placeholder; Hand.score resolves Ace dynamically
        if self.rank in FACE_RANKS:
            return 10
        return int(self.rank)

    @property
    def image_code(self) -> str:
        """
        Code for the Deck of Cards API image URL:
          https://deckofcardsapi.com/static/img/{code}.png
        rank: "10" → "0"  |  suit: ♠→S  ♥→H  ♦→D  ♣→C
        """
        rank_code = "0" if self.rank == "10" else self.rank
        suit_map  = {"♠": "S", "♥": "H", "♦": "D", "♣": "C"}
        return f"{rank_code}{suit_map[self.suit]}"


# ---------------------------------------------------------------------------
# Deck
# ---------------------------------------------------------------------------

class Deck:
    """
    Standard 52-card deck. Each card exists exactly once — no replacement.
    deal() pops from the top; cards cannot repeat within the same game.
    """

    def __init__(self) -> None:
        self._cards: list[Card] = [
            Card(suit, rank)
            for suit in SUITS
            for rank in RANKS
        ]
        self.shuffle()

    def shuffle(self) -> None:
        random.shuffle(self._cards)

    def deal(self) -> Card:
        if not self._cards:
            raise IndexError("Deck is empty")
        return self._cards.pop()

    def __len__(self) -> int:
        return len(self._cards)


# ---------------------------------------------------------------------------
# Hand  (Ban-Luck rules)
# ---------------------------------------------------------------------------

class Hand:
    """
    A player's hand under Malaysian Ban-Luck (21) rules.

    KEY DIFFERENCE from standard Blackjack — Dynamic Ace value:
      2 cards: Ace = 1, 10, or 11 (best value that keeps score ≤ 21)
      3 cards: Ace = 1 or 10
      4-5 cards: Ace = 1 only

    This is NOT the standard "reduce from 11 to 1" clamp.
    The maximum Ace value decreases as the hand grows.
    """

    def __init__(self) -> None:
        self.cards: list[Card] = []

    def add(self, card: Card) -> None:
        self.cards.append(card)

    @property
    def score(self) -> int:
        """
        Calculate Ban-Luck score with dynamic Ace values.

        Algorithm:
          1. Sum all non-Ace cards.
          2. For each Ace, determine the best value based on card count
             (tries highest allowed value first, reduces if bust).
        """
        n     = len(self.cards)
        aces  = [c for c in self.cards if c.rank == "A"]
        others = sum(c.base_value for c in self.cards if c.rank != "A")

        # Max Ace value allowed based on total cards in hand
        if n <= 2:
            ace_values = [11, 10, 1]   # try 11, then 10, then 1
        elif n == 3:
            ace_values = [10, 1]       # try 10, then 1
        else:
            ace_values = [1]           # 4+ cards: always 1

        total = others
        for _ in aces:
            # Pick the highest allowed value that doesn't bust the total
            for val in ace_values:
                if total + val <= 21:
                    total += val
                    break
            else:
                # All options bust — use the minimum (last in the list)
                total += ace_values[-1]

        return total

    @property
    def is_bust(self) -> bool:
        return self.score > 21

    @property
    def must_hit(self) -> bool:
        """
        Minimum 16 Rule: player MUST Hit if score < 16.
        Stand is forbidden until this condition is cleared.
        """
        return self.score < 16

    @property
    def can_escape(self) -> bool:
        """
        15/16 Escape window: only available on the initial 2-card deal
        when the hand is exactly 15 or 16. Allows the player to surrender
        and reclaim their bet before play begins.
        """
        return len(self.cards) == 2 and self.score in (15, 16)

    @property
    def special(self) -> tuple[str, float] | None:
        """
        Detect Ban-Luck special hands.
        Returns (display_name, payout_multiplier) or None.
        Payout multiplier: player receives  bet × multiplier  back.

        Priority (checked highest first):
          7-7-7 三条七 🎰   — three 7s totalling 21        → 5×
          五龙 Five Dragon 🐲 — 5 cards without busting     → 5×
          Ban-Ban 双A ✨    — two Aces on initial 2-card deal → 3×
          Ban-Luck 过海 🌊  — Ace + face on initial deal    → 2×
          Double 双对子 👯  — two identical ranks on deal   → 2×
        """
        if self.is_bust:
            return None

        n = len(self.cards)

        # 7-7-7: three 7s (score is exactly 21)
        if sum(1 for c in self.cards if c.rank == "7") >= 3:
            return ("7-7-7 三条七 🎰", 5.0)

        # 五龙: 5 cards without busting
        if n >= 5:
            return ("五龙 Five Dragon 🐲", 5.0)

        # Initial 2-card specials
        if n == 2:
            ranks = {c.rank for c in self.cards}
            # Ban-Ban: both cards are Aces
            if all(c.rank == "A" for c in self.cards):
                return ("Ban-Ban 双A ✨", 3.0)
            # Ban-Luck: Ace + face card (10/J/Q/K)
            if "A" in ranks and (ranks - {"A"}) <= FACE_RANKS:
                return ("Ban-Luck 过海 🌊", 2.0)
            # Double: two cards with identical rank (not Ace, handled above)
            if self.cards[0].rank == self.cards[1].rank:
                return ("Double 双对子 👯", 2.0)

        return None

    def show(self, hide_first: bool = False, hide_all: bool = False) -> str:
        """
        Render the hand as a Discord-friendly string.
        hide_all=True   → all cards masked as [?] (public board during play).
        hide_first=True → first card masked, rest visible (legacy / not used on public board).
        Neither         → full hand + score + tags (ephemeral / reveal at showdown).
        """
        if hide_all:
            return "  ".join("[?]" for _ in self.cards)

        if hide_first and len(self.cards) >= 2:
            rest = "  ".join(str(c) for c in self.cards[1:])
            return f"[?]  {rest}"

        cards_str = "  ".join(str(c) for c in self.cards)
        if self.is_bust:
            tag = "  💥 **BUST**"
        elif self.special:
            tag = f"  {self.special[0]}"
        elif self.must_hit:
            tag = f"  *(must hit — {self.score} < 16)*"
        else:
            tag = ""
        return f"{cards_str}  — **{self.score}**{tag}"

    def __len__(self) -> int:
        return len(self.cards)


# ---------------------------------------------------------------------------
# PlayerState
# ---------------------------------------------------------------------------

@dataclass
class PlayerState:
    """One participant's state within a GameTable."""
    user_id: int
    name:    str
    hand:    Hand = field(default_factory=Hand)
    status:  str  = "active"
    escaped: bool = False
    # status values:
    #   "active"  — still playing, can Hit/Stand/Escape
    #   "stood"   — chose to Stand (score ≥ 16)
    #   "bust"    — went over 21
    #   "special" — triggered 五龙 or 7-7-7 during play (auto-stand)
    # escaped = True means player used the 15/16 Escape — bet refunded,
    #   skipped in payout loop


# ---------------------------------------------------------------------------
# GameTable
# ---------------------------------------------------------------------------

@dataclass
class GameTable:
    """
    Full state for one multi-player Ban-Luck table.

    State machine:
      LOBBY    → players join via Discord buttons
      PLAYING  → cards dealt; participants act in turn order
      FINISHED → all done; payout calculated

    Turn order: players in join order first, banker always last.
    Banker escrow = bet × num_players × 5 (covers worst-case 5× for all).
    """
    banker_id:   int
    banker_name: str
    bet:         int

    deck:    Deck              = field(default_factory=Deck)
    players: list[PlayerState] = field(default_factory=list)  # non-banker

    # init=False fields set in __post_init__
    banker:      PlayerState = field(init=False)
    phase:       str         = field(default="lobby", init=False)
    current_idx: int         = field(default=0,       init=False)

    MAX_PLAYERS: int = 4   # non-banker slots; total table = 5

    def __post_init__(self) -> None:
        self.banker = PlayerState(self.banker_id, self.banker_name)

    # ------------------------------------------------------------------ Lobby

    def add_player(self, user_id: int, name: str) -> bool:
        if self.phase != "lobby":
            return False
        if len(self.players) >= self.MAX_PLAYERS:
            return False
        if user_id == self.banker_id:
            return False
        if any(p.user_id == user_id for p in self.players):
            return False
        self.players.append(PlayerState(user_id, name))
        return True

    @property
    def is_full(self) -> bool:
        return len(self.players) >= self.MAX_PLAYERS

    @property
    def banker_escrow(self) -> int:
        """
        XP the banker must lock in.
        Max total return per player = bet + bet×5 = bet×6 (for a 5x special hand),
        because the player's stake is returned on top of the 5× profit.
        """
        return self.bet * len(self.players) * 6

    # --------------------------------------------------------------- Playing

    def start(self) -> None:
        """
        LOBBY → PLAYING.
        Deals 2 cards alternately (players then banker, twice).
        No auto-detection of naturals on deal (unlike standard BJ) — all
        special hands are checked live when the player acts.
        Positions current_idx at the first active player.
        """
        self.phase       = "playing"
        self.current_idx = 0
        for _ in range(2):
            for p in self.all_participants:
                p.hand.add(self.deck.deal())
        self._skip_to_active()

    def _skip_to_active(self) -> bool:
        """
        Advance current_idx to the next participant with status "active"
        who has not escaped.
        Returns True if found. Returns False (sets phase="finished") if done.
        """
        total = len(self.players)   # banker sits at index == total

        while self.current_idx <= total:
            p = (
                self.players[self.current_idx]
                if self.current_idx < total
                else self.banker
            )
            if p.status == "active" and not p.escaped:
                return True
            self.current_idx += 1

        self.phase = "finished"
        return False

    def advance(self) -> bool:
        """
        End current participant's turn, move to next active one.
        Returns True if the game should be resolved.
        """
        self.current_idx += 1
        return not self._skip_to_active()

    @property
    def current_participant(self) -> PlayerState | None:
        if self.phase != "playing":
            return None
        total = len(self.players)
        if self.current_idx < total:
            return self.players[self.current_idx]
        if self.current_idx == total:
            return self.banker
        return None

    # ---------------------------------------------------------------- Helpers

    @property
    def all_participants(self) -> list[PlayerState]:
        """Players in join order, banker last."""
        return self.players + [self.banker]

    @property
    def all_player_ids(self) -> set[int]:
        ids = {self.banker_id}
        ids.update(p.user_id for p in self.players)
        return ids
