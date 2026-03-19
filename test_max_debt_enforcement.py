"""
Property-based tests for MAX_DEBT enforcement (task 2.6)

Tests the validate_bet_with_firewall() function to ensure it properly
rejects bets that would cause a user's debt to exceed MAX_DEBT (-10000).

**Validates: Requirements 2.5, 2.6**
"""

import pytest
import sqlite3
from hypothesis import given, settings
import hypothesis.strategies as st


# ---------------------------------------------------------------------------
# Constants (copied from main.py)
# ---------------------------------------------------------------------------

MAX_BET = 5000
MAX_DEBT = -10000


# ---------------------------------------------------------------------------
# Test Database Setup
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db():
    """Create a temporary test database for each test."""
    # Use an in-memory DB to avoid Windows file locking.
    db = sqlite3.connect(":memory:", check_same_thread=False)
    
    # Create user_gold table with all columns
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_gold (
            guild_id   BIGINT  NOT NULL,
            user_id    BIGINT  NOT NULL,
            gold       INTEGER NOT NULL DEFAULT 0,
            last_daily TEXT,
            last_rob_date TEXT,
            rob_count INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    db.commit()
    
    yield db
    
    # Cleanup
    db.close()


# ---------------------------------------------------------------------------
# Helper Functions (copied from main.py for testing)
# ---------------------------------------------------------------------------

def get_gold(db, guild_id: int, user_id: int) -> int:
    """Return the player's current Gold balance in this guild (0 for first-timers)."""
    row = db.execute(
        "SELECT gold FROM user_gold WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return row[0] if row else 0


def add_gold(db, guild_id: int, user_id: int, amount: int) -> int:
    """
    Add (or subtract) Gold for a player in this guild.
    No floor — balance may go negative (debt).
    Returns the new balance.
    """
    new_val = get_gold(db, guild_id, user_id) + amount
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold) VALUES (?, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET gold = excluded.gold
        """,
        (guild_id, user_id, new_val),
    )
    db.commit()
    return new_val


def validate_bet_with_firewall(db, guild_id: int, user_id: int, bet: int) -> tuple[bool, str]:
    """
    Validate bet against economic firewall rules.
    
    Returns (is_valid, error_message).
    If is_valid is True, error_message is empty.
    """
    # Check MAX_BET
    if bet > MAX_BET:
        return False, f"❌ Bet exceeds maximum allowed ({MAX_BET:,} 💰)"
    
    # Check MAX_DEBT for in-debt players
    current_gold = get_gold(db, guild_id, user_id)
    if current_gold <= 0:
        projected_debt = current_gold - int(bet * 1.3)
        if projected_debt < MAX_DEBT:
            return False, f"🚫 Ah Long credit limit reached! Maximum debt is {MAX_DEBT:,} 💰. Your projected debt would be {projected_debt:,} 💰."
    
    return True, ""


# ---------------------------------------------------------------------------
# Property Test: MAX_DEBT Enforcement
# ---------------------------------------------------------------------------

def create_test_db():
    """Create a temporary test database."""
    # Use an in-memory DB to avoid Windows file locking,
    # especially under Hypothesis running many examples.
    db = sqlite3.connect(":memory:", check_same_thread=False)
    
    # Create user_gold table with all columns
    db.execute("""
        CREATE TABLE IF NOT EXISTS user_gold (
            guild_id   BIGINT  NOT NULL,
            user_id    BIGINT  NOT NULL,
            gold       INTEGER NOT NULL DEFAULT 0,
            last_daily TEXT,
            last_rob_date TEXT,
            rob_count INTEGER DEFAULT 0,
            PRIMARY KEY (guild_id, user_id)
        )
    """)
    db.commit()
    
    return db


def cleanup_test_db(db):
    """Clean up test database."""
    db.close()


@settings(max_examples=10, deadline=None)
@given(
    current_gold=st.integers(min_value=MAX_DEBT + 1, max_value=0),  # Negative Gold (in debt)
    bet_amount=st.integers(min_value=1, max_value=MAX_BET),  # Valid bet amount
    guild_id=st.integers(min_value=1, max_value=999999999999),
    user_id=st.integers(min_value=1, max_value=999999999999)
)
def test_max_debt_enforcement_property(current_gold, bet_amount, guild_id, user_id):
    """
    Property 5: MAX_DEBT Enforcement
    
    For any user in debt (Gold <= 0) attempting to place a bet, if the projected debt
    (current_gold - int(bet * 1.3)) would be less than MAX_DEBT (-10000), then the
    system should reject the bet before any Gold is deducted.
    
    **Validates: Requirements 2.5, 2.6**
    """
    # Create test database for this test run
    test_db = create_test_db()
    
    try:
        # Set up user with negative Gold balance (in debt)
        add_gold(test_db, guild_id, user_id, current_gold)
        
        # Verify initial balance
        initial_balance = get_gold(test_db, guild_id, user_id)
        assert initial_balance == current_gold, "Initial balance should be set correctly"
        
        # Calculate projected debt
        projected_debt = current_gold - int(bet_amount * 1.3)
        
        # Attempt to place bet
        is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
        
        if projected_debt < MAX_DEBT:
            # Bet should be REJECTED because projected debt exceeds MAX_DEBT
            assert not is_valid, \
                f"Bet should be rejected when projected_debt ({projected_debt}) < MAX_DEBT ({MAX_DEBT})"
            
            # Error message should be present
            assert error_message != "", "Error message should be provided when bet is rejected"
            
            # Error message should mention MAX_DEBT or credit limit
            assert "credit limit" in error_message.lower() or str(MAX_DEBT) in error_message, \
                f"Error message should mention MAX_DEBT or credit limit: '{error_message}'"
            
            # Gold balance should remain unchanged (no deduction)
            final_balance = get_gold(test_db, guild_id, user_id)
            assert final_balance == initial_balance, \
                f"Gold should not be deducted when bet is rejected. Initial: {initial_balance}, Final: {final_balance}"
        else:
            # Bet should be ALLOWED because projected debt is within limits
            assert is_valid, \
                f"Bet should be allowed when projected_debt ({projected_debt}) >= MAX_DEBT ({MAX_DEBT})"
            
            # No error message should be present
            assert error_message == "", f"No error message should be present for valid bet, got: '{error_message}'"
    finally:
        # Clean up test database
        cleanup_test_db(test_db)


# ---------------------------------------------------------------------------
# Additional Edge Case Tests
# ---------------------------------------------------------------------------

def test_max_debt_boundary_exactly_max_debt(test_db):
    """
    Test that a bet resulting in exactly MAX_DEBT is allowed.
    
    This is an edge case test to verify the boundary condition.
    """
    guild_id = 123456789
    user_id = 987654321
    
    # Set up user in debt such that projected debt = MAX_DEBT
    # projected_debt = current_gold - int(bet * 1.3)
    # -10000 = current_gold - int(1000 * 1.3)
    # -10000 = current_gold - 1300
    # current_gold = -8700
    current_gold = -8700
    bet_amount = 1000
    
    add_gold(test_db, guild_id, user_id, current_gold)
    
    # Verify projected debt calculation
    projected_debt = current_gold - int(bet_amount * 1.3)
    assert projected_debt == MAX_DEBT, f"Test setup error: projected_debt should be {MAX_DEBT}, got {projected_debt}"
    
    # Attempt to place bet
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    # Bet should be ALLOWED (projected debt equals MAX_DEBT, not less than)
    assert is_valid, f"Bet resulting in exactly MAX_DEBT ({MAX_DEBT}) should be allowed"
    assert error_message == "", "No error message should be present for valid bet"


def test_max_debt_boundary_one_below_max_debt(test_db):
    """
    Test that a bet resulting in MAX_DEBT - 1 is rejected.
    
    This is an edge case test to verify the boundary condition.
    """
    guild_id = 123456789
    user_id = 987654321
    
    # Set up user in debt such that projected debt = MAX_DEBT - 1
    # projected_debt = current_gold - int(bet * 1.3)
    # -10001 = current_gold - int(1000 * 1.3)
    # -10001 = current_gold - 1300
    # current_gold = -8701
    current_gold = -8701
    bet_amount = 1000
    
    add_gold(test_db, guild_id, user_id, current_gold)
    
    # Verify projected debt calculation
    projected_debt = current_gold - int(bet_amount * 1.3)
    assert projected_debt == MAX_DEBT - 1, f"Test setup error: projected_debt should be {MAX_DEBT - 1}, got {projected_debt}"
    
    # Attempt to place bet
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    # Bet should be REJECTED
    assert not is_valid, f"Bet resulting in MAX_DEBT - 1 ({MAX_DEBT - 1}) should be rejected"
    assert error_message != "", "Error message should be present for rejected bet"


def test_max_debt_not_checked_for_positive_gold(test_db):
    """
    Test that MAX_DEBT check is NOT applied when user has positive Gold.
    
    Users with positive Gold should not be subject to MAX_DEBT checks.
    """
    guild_id = 123456789
    user_id = 987654321
    bet_amount = 1000
    
    # User has positive Gold
    add_gold(test_db, guild_id, user_id, 5000)
    
    # Attempt to place bet
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    # Bet should be ALLOWED (MAX_DEBT check doesn't apply to positive Gold)
    assert is_valid, "Bet should be allowed for users with positive Gold"
    assert error_message == "", "No error message should be present"


def test_max_debt_checked_for_zero_gold(test_db):
    """
    Test that MAX_DEBT check IS applied when user has exactly zero Gold.
    
    Users with zero Gold are considered "in debt" for the purpose of MAX_DEBT checks.
    """
    guild_id = 123456789
    user_id = 987654321
    
    # User has exactly zero Gold
    # Don't add any gold, so balance is 0
    
    # Large bet that would cause projected debt < MAX_DEBT
    # projected_debt = 0 - int(10000 * 1.3) = -13000
    bet_amount = 10000
    
    # Attempt to place bet
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    # Calculate projected debt
    projected_debt = 0 - int(bet_amount * 1.3)
    
    # Bet should be REJECTED because projected debt < MAX_DEBT
    assert not is_valid, f"Bet should be rejected when projected_debt ({projected_debt}) < MAX_DEBT ({MAX_DEBT})"
    assert error_message != "", "Error message should be present"


def test_max_debt_with_small_negative_gold(test_db):
    """
    Test MAX_DEBT enforcement with small negative Gold balance.
    
    Even users with small debt should be subject to MAX_DEBT checks.
    """
    guild_id = 123456789
    user_id = 987654321
    
    # User has small debt
    add_gold(test_db, guild_id, user_id, -100)
    
    # Large bet that would cause projected debt < MAX_DEBT
    # projected_debt = -100 - int(8000 * 1.3) = -100 - 10400 = -10500
    bet_amount = 8000
    
    # Attempt to place bet
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    # Calculate projected debt
    projected_debt = -100 - int(bet_amount * 1.3)
    
    # Bet should be REJECTED
    assert not is_valid, f"Bet should be rejected when projected_debt ({projected_debt}) < MAX_DEBT ({MAX_DEBT})"
    assert error_message != "", "Error message should be present"


def test_max_debt_with_large_negative_gold(test_db):
    """
    Test MAX_DEBT enforcement when user is already near MAX_DEBT.
    
    Users already near MAX_DEBT should have very limited betting capacity.
    """
    guild_id = 123456789
    user_id = 987654321
    
    # User is already near MAX_DEBT
    add_gold(test_db, guild_id, user_id, -9500)
    
    # Small bet that would still exceed MAX_DEBT
    # projected_debt = -9500 - int(500 * 1.3) = -9500 - 650 = -10150
    bet_amount = 500
    
    # Attempt to place bet
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    # Calculate projected debt
    projected_debt = -9500 - int(bet_amount * 1.3)
    
    # Bet should be REJECTED
    assert not is_valid, f"Bet should be rejected when projected_debt ({projected_debt}) < MAX_DEBT ({MAX_DEBT})"
    assert error_message != "", "Error message should be present"


def test_max_debt_debt_tax_calculation(test_db):
    """
    Test that projected debt calculation uses the correct debt tax rate (30%).
    
    The projected debt formula is: current_gold - int(bet * 1.3)
    """
    guild_id = 123456789
    user_id = 987654321
    
    # User in debt
    current_gold = -5000
    add_gold(test_db, guild_id, user_id, current_gold)
    
    # Bet amount
    bet_amount = 4000
    
    # Expected projected debt = -5000 - int(4000 * 1.3) = -5000 - 5200 = -10200
    expected_projected_debt = current_gold - int(bet_amount * 1.3)
    assert expected_projected_debt == -10200, "Test setup verification"
    
    # This should be rejected because -10200 < -10000
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    assert not is_valid, "Bet should be rejected"
    assert error_message != "", "Error message should be present"
    
    # Verify the error message contains the correct projected debt
    assert "-10,200" in error_message, f"Error message should show projected debt of -10,200: '{error_message}'"
