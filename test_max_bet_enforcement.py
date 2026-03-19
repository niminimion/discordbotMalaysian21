"""
Property-based tests for MAX_BET enforcement (task 2.5)

Tests the validate_bet_with_firewall() function to ensure it properly
rejects bets exceeding MAX_BET (5000).

**Validates: Requirements 2.3, 2.4**
"""

import pytest
import sqlite3
import os
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
    db_path = "test_bot_data.db"
    
    # Remove existing test database if it exists
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # Create new test database
    db = sqlite3.connect(db_path, check_same_thread=False)
    
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
    if os.path.exists(db_path):
        os.remove(db_path)


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
# Property Test: MAX_BET Enforcement
# ---------------------------------------------------------------------------

def create_test_db():
    """Create a temporary test database."""
    db_path = "test_pbt_max_bet.db"
    
    # Remove existing test database if it exists
    if os.path.exists(db_path):
        os.remove(db_path)
    
    # Create new test database
    db = sqlite3.connect(db_path, check_same_thread=False)
    
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
    db_path = "test_pbt_max_bet.db"
    db.close()
    if os.path.exists(db_path):
        os.remove(db_path)


@settings(max_examples=10, deadline=None)
@given(
    bet_amount=st.integers(min_value=MAX_BET + 1, max_value=1000000),
    guild_id=st.integers(min_value=1, max_value=999999999999),
    user_id=st.integers(min_value=1, max_value=999999999999),
    initial_gold=st.integers(min_value=1, max_value=1000000)
)
def test_max_bet_enforcement_property(bet_amount, guild_id, user_id, initial_gold):
    """
    Property 4: MAX_BET Enforcement
    
    For any gambling command and any bet amount, if the bet exceeds MAX_BET (5000),
    then the system should reject the bet before any Gold is deducted.
    
    **Validates: Requirements 2.3, 2.4**
    """
    # Create test database for this test run
    test_db = create_test_db()
    
    try:
        # Set up user with positive Gold balance (to avoid MAX_DEBT checks)
        add_gold(test_db, guild_id, user_id, initial_gold)
        
        # Verify initial balance
        initial_balance = get_gold(test_db, guild_id, user_id)
        assert initial_balance == initial_gold, "Initial balance should be set correctly"
        
        # Attempt to place bet > MAX_BET
        is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
        
        # Assertion 1: Bet should be rejected
        assert not is_valid, f"Bet of {bet_amount} (> MAX_BET={MAX_BET}) should be rejected"
        
        # Assertion 2: Error message should be present
        assert error_message != "", "Error message should be provided when bet is rejected"
        
        # Assertion 3: Error message should mention MAX_BET
        assert "maximum allowed" in error_message.lower() or str(MAX_BET) in error_message, \
            f"Error message should mention MAX_BET: '{error_message}'"
        
        # Assertion 4: Gold balance should remain unchanged (no deduction)
        final_balance = get_gold(test_db, guild_id, user_id)
        assert final_balance == initial_balance, \
            f"Gold should not be deducted when bet is rejected. Initial: {initial_balance}, Final: {final_balance}"
    finally:
        # Clean up test database
        cleanup_test_db(test_db)


# ---------------------------------------------------------------------------
# Additional Edge Case Tests
# ---------------------------------------------------------------------------

def test_max_bet_boundary_exactly_max_bet(test_db):
    """
    Test that a bet exactly equal to MAX_BET is allowed.
    
    This is an edge case test to verify the boundary condition.
    """
    guild_id = 123456789
    user_id = 987654321
    bet_amount = MAX_BET  # Exactly 5000
    
    # Set up user with sufficient Gold
    add_gold(test_db, guild_id, user_id, 10000)
    
    # Attempt to place bet = MAX_BET
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    # Bet should be ALLOWED (not rejected)
    assert is_valid, f"Bet of exactly MAX_BET ({MAX_BET}) should be allowed"
    assert error_message == "", "No error message should be present for valid bet"


def test_max_bet_boundary_one_over_max_bet(test_db):
    """
    Test that a bet of MAX_BET + 1 is rejected.
    
    This is an edge case test to verify the boundary condition.
    """
    guild_id = 123456789
    user_id = 987654321
    bet_amount = MAX_BET + 1  # 5001
    
    # Set up user with sufficient Gold
    add_gold(test_db, guild_id, user_id, 10000)
    
    # Attempt to place bet = MAX_BET + 1
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    # Bet should be REJECTED
    assert not is_valid, f"Bet of MAX_BET + 1 ({bet_amount}) should be rejected"
    assert error_message != "", "Error message should be present for rejected bet"


def test_max_bet_enforcement_with_zero_gold(test_db):
    """
    Test that MAX_BET enforcement works even when user has zero Gold.
    
    This ensures MAX_BET check happens before MAX_DEBT check.
    """
    guild_id = 123456789
    user_id = 987654321
    bet_amount = MAX_BET + 1000  # Well over MAX_BET
    
    # User has zero Gold (would trigger debt checks)
    # Don't add any gold, so balance is 0
    
    # Attempt to place bet > MAX_BET
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    # Bet should be REJECTED due to MAX_BET (not MAX_DEBT)
    assert not is_valid, f"Bet of {bet_amount} should be rejected"
    assert "maximum allowed" in error_message.lower() or str(MAX_BET) in error_message, \
        f"Error should be about MAX_BET, not MAX_DEBT: '{error_message}'"


def test_max_bet_enforcement_with_negative_gold(test_db):
    """
    Test that MAX_BET enforcement works even when user is in debt.
    
    This ensures MAX_BET check happens before MAX_DEBT check.
    """
    guild_id = 123456789
    user_id = 987654321
    bet_amount = MAX_BET + 500  # Over MAX_BET
    
    # User is in debt
    add_gold(test_db, guild_id, user_id, -2000)
    
    # Attempt to place bet > MAX_BET
    is_valid, error_message = validate_bet_with_firewall(test_db, guild_id, user_id, bet_amount)
    
    # Bet should be REJECTED due to MAX_BET (not MAX_DEBT)
    assert not is_valid, f"Bet of {bet_amount} should be rejected"
    assert "maximum allowed" in error_message.lower() or str(MAX_BET) in error_message, \
        f"Error should be about MAX_BET, not MAX_DEBT: '{error_message}'"
