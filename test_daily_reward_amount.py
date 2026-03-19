"""
Property-based tests for daily reward amount (task 4.3)

Tests the /daily command to ensure it awards exactly 500 Gold and updates
the last_daily timestamp correctly.

**Validates: Requirements 3.3, 3.4**
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
# Property Test: Daily Reward Amount and Persistence
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
def test_daily_reward_amount_property(guild_id, user_id, initial_gold, year, month, day):
    """
    Property 2: Daily Reward Amount and Persistence
    
    For any user and guild, if a user has not claimed their daily reward today (MYT),
    then executing the daily command should increase their Gold balance by exactly 500
    and update their last_daily timestamp to the current MYT date.
    
    **Validates: Requirements 3.3, 3.4**
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
        
        # Verify user has not claimed today (last_daily should be None or different date)
        last_daily_before = get_last_daily(test_db, guild_id, user_id)
        
        # If last_daily is already set to today, set it to a different date
        if last_daily_before == date_str:
            # Set to yesterday to ensure we can claim today
            yesterday = f"{year:04d}-{month:02d}-{max(1, day-1):02d}"
            set_last_daily(test_db, guild_id, user_id, yesterday)
            last_daily_before = yesterday
        
        # Claim daily reward
        success, message, new_balance = claim_daily(test_db, guild_id, user_id, date_str)
        
        # Assertion 1: Claim should succeed
        assert success, f"Daily claim should succeed when not claimed today, but got: {message}"
        
        # Assertion 2: Gold balance should increase by exactly DAILY_REWARD (500)
        expected_balance = initial_balance + DAILY_REWARD
        assert new_balance == expected_balance, \
            f"Gold should increase by exactly {DAILY_REWARD}. Initial: {initial_balance}, Expected: {expected_balance}, Got: {new_balance}"
        
        # Assertion 3: Database balance should match returned balance
        db_balance = get_gold(test_db, guild_id, user_id)
        assert db_balance == new_balance, \
            f"Database balance should match returned balance. Returned: {new_balance}, DB: {db_balance}"
        
        # Assertion 4: last_daily should be updated to the current date
        last_daily_after = get_last_daily(test_db, guild_id, user_id)
        assert last_daily_after == date_str, \
            f"last_daily should be updated to '{date_str}', but got: {last_daily_after}"
        
        # Assertion 5: Success message should be present
        assert "500 gold" in message.lower() or "500" in message, \
            f"Success message should mention 500 gold: '{message}'"
        
    finally:
        # Clean up test database
        cleanup_test_db(test_db)


# ---------------------------------------------------------------------------
# Additional Edge Case Tests
# ---------------------------------------------------------------------------

def test_daily_reward_amount_with_real_date():
    """
    Test daily reward amount with the actual current MYT date.
    
    This ensures the property works with real dates from get_today_myt().
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        initial_gold = 1000
        today = get_today_myt()
        
        # Set up user with initial Gold
        add_gold(test_db, guild_id, user_id, initial_gold)
        
        # Claim daily
        success, message, new_balance = claim_daily(test_db, guild_id, user_id, today)
        
        # Verify success
        assert success, f"Claim should succeed: {message}"
        
        # Verify Gold increased by exactly 500
        assert new_balance == initial_gold + DAILY_REWARD, \
            f"Gold should increase by {DAILY_REWARD}. Initial: {initial_gold}, New: {new_balance}"
        
        # Verify last_daily updated
        last_daily = get_last_daily(test_db, guild_id, user_id)
        assert last_daily == today, f"last_daily should be {today}, got {last_daily}"
        
    finally:
        cleanup_test_db(test_db)


def test_daily_reward_amount_with_negative_gold():
    """
    Test that daily reward works correctly when user is in debt (negative Gold).
    
    This verifies that the reward is added correctly even for negative balances.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        initial_gold = -2000  # User is in debt
        today = get_today_myt()
        
        # Set up user with negative Gold
        add_gold(test_db, guild_id, user_id, initial_gold)
        
        # Claim daily
        success, message, new_balance = claim_daily(test_db, guild_id, user_id, today)
        
        # Verify success
        assert success, f"Claim should succeed even with negative Gold: {message}"
        
        # Verify Gold increased by exactly 500
        expected_balance = initial_gold + DAILY_REWARD  # -2000 + 500 = -1500
        assert new_balance == expected_balance, \
            f"Gold should increase by {DAILY_REWARD}. Initial: {initial_gold}, Expected: {expected_balance}, Got: {new_balance}"
        
        # Verify last_daily updated
        last_daily = get_last_daily(test_db, guild_id, user_id)
        assert last_daily == today, f"last_daily should be {today}, got {last_daily}"
        
    finally:
        cleanup_test_db(test_db)


def test_daily_reward_amount_with_zero_gold():
    """
    Test that daily reward works correctly when user has exactly zero Gold.
    
    This is an edge case for users who have spent all their Gold.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = get_today_myt()
        
        # User has zero Gold (don't add any)
        initial_gold = get_gold(test_db, guild_id, user_id)
        assert initial_gold == 0, "Initial Gold should be 0"
        
        # Claim daily
        success, message, new_balance = claim_daily(test_db, guild_id, user_id, today)
        
        # Verify success
        assert success, f"Claim should succeed with zero Gold: {message}"
        
        # Verify Gold is exactly 500
        assert new_balance == DAILY_REWARD, \
            f"Gold should be exactly {DAILY_REWARD}, got {new_balance}"
        
        # Verify last_daily updated
        last_daily = get_last_daily(test_db, guild_id, user_id)
        assert last_daily == today, f"last_daily should be {today}, got {last_daily}"
        
    finally:
        cleanup_test_db(test_db)


def test_daily_reward_amount_first_time_user():
    """
    Test that first-time users (last_daily = NULL) receive exactly 500 Gold.
    
    This verifies the edge case where a user has never claimed before.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = get_today_myt()
        
        # Verify user doesn't exist yet (last_daily = NULL)
        last_daily = get_last_daily(test_db, guild_id, user_id)
        assert last_daily is None, "First-time user should have NULL last_daily"
        
        # Claim daily
        success, message, new_balance = claim_daily(test_db, guild_id, user_id, today)
        
        # Verify success
        assert success, f"First-time user should be able to claim: {message}"
        
        # Verify Gold is exactly 500
        assert new_balance == DAILY_REWARD, \
            f"First-time user should receive exactly {DAILY_REWARD}, got {new_balance}"
        
        # Verify last_daily is now set
        last_daily_after = get_last_daily(test_db, guild_id, user_id)
        assert last_daily_after == today, f"last_daily should be set to {today}, got {last_daily_after}"
        
    finally:
        cleanup_test_db(test_db)


def test_daily_reward_amount_with_large_positive_gold():
    """
    Test that daily reward works correctly when user has large positive Gold.
    
    This verifies that the reward is added correctly to large balances.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        initial_gold = 50000  # Large positive balance
        today = get_today_myt()
        
        # Set up user with large Gold
        add_gold(test_db, guild_id, user_id, initial_gold)
        
        # Claim daily
        success, message, new_balance = claim_daily(test_db, guild_id, user_id, today)
        
        # Verify success
        assert success, f"Claim should succeed with large Gold: {message}"
        
        # Verify Gold increased by exactly 500
        expected_balance = initial_gold + DAILY_REWARD
        assert new_balance == expected_balance, \
            f"Gold should increase by {DAILY_REWARD}. Initial: {initial_gold}, Expected: {expected_balance}, Got: {new_balance}"
        
        # Verify last_daily updated
        last_daily = get_last_daily(test_db, guild_id, user_id)
        assert last_daily == today, f"last_daily should be {today}, got {last_daily}"
        
    finally:
        cleanup_test_db(test_db)


def test_daily_reward_amount_persistence_across_multiple_days():
    """
    Test that daily reward can be claimed on multiple different days.
    
    This verifies that the reward amount is consistent across multiple claims.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        
        # Claim on day 1
        date1 = "2024-01-15"
        success1, _, balance1 = claim_daily(test_db, guild_id, user_id, date1)
        assert success1, "First claim should succeed"
        assert balance1 == DAILY_REWARD, f"Balance after day 1 should be {DAILY_REWARD}, got {balance1}"
        
        # Claim on day 2
        date2 = "2024-01-16"
        success2, _, balance2 = claim_daily(test_db, guild_id, user_id, date2)
        assert success2, "Second claim should succeed"
        assert balance2 == DAILY_REWARD * 2, f"Balance after day 2 should be {DAILY_REWARD * 2}, got {balance2}"
        
        # Claim on day 3
        date3 = "2024-01-17"
        success3, _, balance3 = claim_daily(test_db, guild_id, user_id, date3)
        assert success3, "Third claim should succeed"
        assert balance3 == DAILY_REWARD * 3, f"Balance after day 3 should be {DAILY_REWARD * 3}, got {balance3}"
        
        # Verify last_daily is updated to the most recent date
        last_daily = get_last_daily(test_db, guild_id, user_id)
        assert last_daily == date3, f"last_daily should be {date3}, got {last_daily}"
        
    finally:
        cleanup_test_db(test_db)
