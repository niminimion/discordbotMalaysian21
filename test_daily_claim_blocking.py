"""
Property-based tests for daily claim blocking (task 4.2)

Tests the /daily command to ensure users cannot claim the daily reward
twice on the same MYT date.

**Validates: Requirements 1.3, 1.5, 3.2**
"""

import pytest
import sqlite3
from hypothesis import given, settings
import hypothesis.strategies as st


# ---------------------------------------------------------------------------
# Constants (copied from main.py)
# ---------------------------------------------------------------------------

DAILY_REWARD = 500


# ---------------------------------------------------------------------------
# Helper Functions (copied from main.py for testing)
# ---------------------------------------------------------------------------

def get_today_myt() -> str:
    """
    Return the current date in Malaysia Time (UTC+8) as 'YYYY-MM-DD' string.
    
    Uses Python standard library: datetime, timezone, timedelta.
    """
    from datetime import datetime, timezone, timedelta
    myt = timezone(timedelta(hours=8))
    return datetime.now(myt).strftime('%Y-%m-%d')


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


def get_last_daily(db, guild_id: int, user_id: int) -> str | None:
    row = db.execute(
        "SELECT last_daily FROM user_gold WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    return row[0] if row else None


def set_last_daily(db, guild_id: int, user_id: int, ts: str) -> None:
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold, last_daily) VALUES (?, ?, 0, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET last_daily = excluded.last_daily
        """,
        (guild_id, user_id, ts),
    )
    db.commit()


def claim_daily(db, guild_id: int, user_id: int, today: str) -> tuple[bool, str, int]:
    """
    Simulate the /daily command logic.
    
    Returns (success, message, new_balance).
    """
    last_str = get_last_daily(db, guild_id, user_id)
    
    # Check if already claimed today (MYT)
    if last_str == today:
        return (
            False,
            "❌ You already claimed your daily relief! Wait until midnight (MYT) for the reset.",
            get_gold(db, guild_id, user_id)
        )
    
    # Award 500 Gold and update last_daily
    new_bal = add_gold(db, guild_id, user_id, DAILY_REWARD)
    set_last_daily(db, guild_id, user_id, today)
    
    return (
        True,
        "✅ Here is your daily 500 gold. Don't lose it all at once!",
        new_bal
    )


# ---------------------------------------------------------------------------
# Test Database Setup
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


# ---------------------------------------------------------------------------
# Property Test: Daily Claim Once Per Day
# ---------------------------------------------------------------------------

@settings(max_examples=10, deadline=None)
@given(
    guild_id=st.integers(min_value=1, max_value=999999999999),
    user_id=st.integers(min_value=1, max_value=999999999999),
    initial_gold=st.integers(min_value=-10000, max_value=100000),
    year=st.integers(min_value=2020, max_value=2030),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=28)  # Use 28 to avoid invalid dates
)
def test_daily_claim_blocking_property(guild_id, user_id, initial_gold, year, month, day):
    """
    Property 1: Daily Claim Once Per Day
    
    For any user and guild, if a user successfully claims their daily reward,
    then attempting to claim again on the same MYT date should be blocked
    with an error message.
    
    **Validates: Requirements 1.3, 1.5, 3.2**
    """
    # Create test database for this test run
    test_db = create_test_db()
    
    try:
        # Format date string as YYYY-MM-DD
        date_str = f"{year:04d}-{month:02d}-{day:02d}"
        
        # Set up user with initial Gold balance
        if initial_gold != 0:
            add_gold(test_db, guild_id, user_id, initial_gold)
        
        # Get initial balance
        initial_balance = get_gold(test_db, guild_id, user_id)
        
        # First claim attempt - should succeed
        success1, message1, balance1 = claim_daily(test_db, guild_id, user_id, date_str)
        
        # Assertion 1: First claim should succeed
        assert success1, f"First daily claim should succeed, but got: {message1}"
        
        # Assertion 2: Balance should increase by DAILY_REWARD
        assert balance1 == initial_balance + DAILY_REWARD, \
            f"Balance should increase by {DAILY_REWARD}. Initial: {initial_balance}, After: {balance1}"
        
        # Assertion 3: last_daily should be set to the date
        last_daily = get_last_daily(test_db, guild_id, user_id)
        assert last_daily == date_str, \
            f"last_daily should be set to '{date_str}', but got: {last_daily}"
        
        # Second claim attempt on the same date - should be blocked
        success2, message2, balance2 = claim_daily(test_db, guild_id, user_id, date_str)
        
        # Assertion 4: Second claim should be blocked
        assert not success2, \
            f"Second daily claim on same date should be blocked, but succeeded with: {message2}"
        
        # Assertion 5: Error message should indicate already claimed
        assert "already claimed" in message2.lower(), \
            f"Error message should indicate already claimed: '{message2}'"
        
        # Assertion 6: Balance should remain unchanged after blocked claim
        assert balance2 == balance1, \
            f"Balance should not change when claim is blocked. After first: {balance1}, After second: {balance2}"
        
        # Assertion 7: last_daily should remain unchanged
        last_daily_after = get_last_daily(test_db, guild_id, user_id)
        assert last_daily_after == date_str, \
            f"last_daily should remain '{date_str}', but got: {last_daily_after}"
        
    finally:
        # Clean up test database
        cleanup_test_db(test_db)


# ---------------------------------------------------------------------------
# Additional Edge Case Tests
# ---------------------------------------------------------------------------

def test_daily_claim_blocking_with_real_date():
    """
    Test daily claim blocking with the actual current MYT date.
    
    This ensures the property works with real dates from get_today_myt().
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = get_today_myt()
        
        # First claim - should succeed
        success1, message1, balance1 = claim_daily(test_db, guild_id, user_id, today)
        assert success1, f"First claim should succeed: {message1}"
        assert balance1 == DAILY_REWARD, f"Balance should be {DAILY_REWARD}, got {balance1}"
        
        # Second claim on same date - should be blocked
        success2, message2, balance2 = claim_daily(test_db, guild_id, user_id, today)
        assert not success2, f"Second claim should be blocked: {message2}"
        assert balance2 == DAILY_REWARD, f"Balance should remain {DAILY_REWARD}, got {balance2}"
        
    finally:
        cleanup_test_db(test_db)


def test_daily_claim_allows_different_dates():
    """
    Test that daily claim is allowed when the date changes.
    
    This verifies that the blocking only applies to the same date.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        date1 = "2024-01-15"
        date2 = "2024-01-16"
        
        # First claim on date1 - should succeed
        success1, message1, balance1 = claim_daily(test_db, guild_id, user_id, date1)
        assert success1, f"First claim should succeed: {message1}"
        assert balance1 == DAILY_REWARD
        
        # Second claim on date2 (different date) - should also succeed
        success2, message2, balance2 = claim_daily(test_db, guild_id, user_id, date2)
        assert success2, f"Claim on different date should succeed: {message2}"
        assert balance2 == DAILY_REWARD * 2, \
            f"Balance should be {DAILY_REWARD * 2} after two claims on different dates, got {balance2}"
        
    finally:
        cleanup_test_db(test_db)


def test_daily_claim_first_time_user():
    """
    Test that first-time users (last_daily = NULL) can claim daily.
    
    This verifies the edge case where a user has never claimed before.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = get_today_myt()
        
        # Verify user doesn't exist yet
        last_daily = get_last_daily(test_db, guild_id, user_id)
        assert last_daily is None, "First-time user should have NULL last_daily"
        
        # First claim - should succeed
        success, message, balance = claim_daily(test_db, guild_id, user_id, today)
        assert success, f"First-time user should be able to claim: {message}"
        assert balance == DAILY_REWARD, f"Balance should be {DAILY_REWARD}, got {balance}"
        
        # Verify last_daily is now set
        last_daily_after = get_last_daily(test_db, guild_id, user_id)
        assert last_daily_after == today, f"last_daily should be set to {today}, got {last_daily_after}"
        
    finally:
        cleanup_test_db(test_db)


def test_daily_claim_blocking_preserves_existing_gold():
    """
    Test that blocked claims don't affect existing Gold balances.
    
    This ensures the blocking happens before any Gold operations.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = get_today_myt()
        initial_gold = 5000
        
        # Set up user with existing Gold
        add_gold(test_db, guild_id, user_id, initial_gold)
        
        # First claim - should succeed
        success1, _, balance1 = claim_daily(test_db, guild_id, user_id, today)
        assert success1
        assert balance1 == initial_gold + DAILY_REWARD
        
        # Second claim - should be blocked
        success2, _, balance2 = claim_daily(test_db, guild_id, user_id, today)
        assert not success2
        assert balance2 == balance1, "Blocked claim should not change balance"
        
    finally:
        cleanup_test_db(test_db)
