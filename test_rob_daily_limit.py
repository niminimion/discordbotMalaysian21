"""
Property-based tests for rob daily limit enforcement (task 5.4)

Tests the rob daily limit logic to ensure users cannot rob more than 3 times
per day. When rob_count >= 3, all rob attempts should be blocked.

**Validates: Requirements 4.3**
"""

import pytest
import sqlite3
import os
from hypothesis import given, settings
import hypothesis.strategies as st


# ---------------------------------------------------------------------------
# Helper Functions (copied from main.py for testing)
# ---------------------------------------------------------------------------

def get_rob_data(db, guild_id: int, user_id: int) -> tuple[str | None, int]:
    """
    Fetch (last_rob_date, rob_count) for a user.
    Returns (None, 0) for first-time users.
    """
    row = db.execute(
        "SELECT last_rob_date, rob_count FROM user_gold WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    ).fetchone()
    if row is None:
        return None, 0
    return row[0], row[1] if row[1] is not None else 0


def update_rob_data(db, guild_id: int, user_id: int, date: str, count: int) -> None:
    """
    Update last_rob_date and rob_count for a user.
    """
    db.execute(
        """
        INSERT INTO user_gold (guild_id, user_id, gold, last_rob_date, rob_count) 
        VALUES (?, ?, 0, ?, ?)
        ON CONFLICT(guild_id, user_id) DO UPDATE SET 
            last_rob_date = excluded.last_rob_date,
            rob_count = excluded.rob_count
        """,
        (guild_id, user_id, date, count),
    )
    db.commit()


def reset_rob_count_if_new_day(db, guild_id: int, user_id: int, today: str) -> int:
    """
    Check if last_rob_date differs from today (MYT).
    If different, reset rob_count to 0 and update date.
    Returns current rob_count.
    """
    last_rob_date, rob_count = get_rob_data(db, guild_id, user_id)
    
    if last_rob_date != today:
        # New day - reset count
        update_rob_data(db, guild_id, user_id, today, 0)
        return 0
    
    return rob_count


def check_rob_limit(db, guild_id: int, user_id: int, today: str) -> tuple[bool, str]:
    """
    Check if user can rob today.
    Returns (can_rob, error_message).
    If can_rob is False, error_message contains the blocking message.
    """
    # Check and reset rob_count if new day
    rob_count = reset_rob_count_if_new_day(db, guild_id, user_id, today)
    
    # Check daily limit (3 attempts per day)
    if rob_count >= 3:
        return False, "🛑 Heat is too high! You've already robbed 3 times today. Lay low until midnight."
    
    return True, ""


# ---------------------------------------------------------------------------
# Test Database Setup
# ---------------------------------------------------------------------------

def create_test_db():
    """Create a temporary test database."""
    db_path = "test_pbt_rob_daily_limit.db"
    
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
    db_path = "test_pbt_rob_daily_limit.db"
    db.close()
    if os.path.exists(db_path):
        os.remove(db_path)


# ---------------------------------------------------------------------------
# Property Test: Rob Daily Limit Enforcement
# ---------------------------------------------------------------------------

@settings(max_examples=10, deadline=None)
@given(
    guild_id=st.integers(min_value=1, max_value=999999999999),
    user_id=st.integers(min_value=1, max_value=999999999999),
    rob_count=st.integers(min_value=3, max_value=100),
    year=st.integers(min_value=2020, max_value=2030),
    month=st.integers(min_value=1, max_value=12),
    day=st.integers(min_value=1, max_value=28)
)
def test_rob_daily_limit_enforcement_property(guild_id, user_id, rob_count, year, month, day):
    """
    Property 6: Rob Daily Limit Enforcement
    
    For any user and guild, if a user has already executed the rob command 3 times
    on the current MYT date, then any additional rob attempts should be blocked
    with an error message.
    
    **Validates: Requirements 4.3**
    """
    # Create test database for this test run
    test_db = create_test_db()
    
    try:
        # Format date string as YYYY-MM-DD
        today = f"{year:04d}-{month:02d}-{day:02d}"
        
        # Set up user with rob_count >= 3 on today
        update_rob_data(test_db, guild_id, user_id, today, rob_count)
        
        # Verify initial state
        last_rob_date, current_rob_count = get_rob_data(test_db, guild_id, user_id)
        assert last_rob_date == today, \
            f"Initial last_rob_date should be '{today}', got '{last_rob_date}'"
        assert current_rob_count == rob_count, \
            f"Initial rob_count should be {rob_count}, got {current_rob_count}"
        assert current_rob_count >= 3, \
            f"Test precondition: rob_count should be >= 3, got {current_rob_count}"
        
        # Check if user can rob
        can_rob, error_message = check_rob_limit(test_db, guild_id, user_id, today)
        
        # Assertion 1: User should NOT be able to rob when rob_count >= 3
        assert can_rob is False, \
            f"User with rob_count={rob_count} should be blocked from robbing, but can_rob={can_rob}"
        
        # Assertion 2: Error message should match the expected blocking message
        expected_message = "🛑 Heat is too high! You've already robbed 3 times today. Lay low until midnight."
        assert error_message == expected_message, \
            f"Error message should be '{expected_message}', got '{error_message}'"
        
        # Assertion 3: rob_count should remain unchanged (not incremented)
        last_rob_date_after, rob_count_after = get_rob_data(test_db, guild_id, user_id)
        assert rob_count_after == rob_count, \
            f"rob_count should remain {rob_count} when blocked, got {rob_count_after}"
        
    finally:
        # Clean up test database
        cleanup_test_db(test_db)


# ---------------------------------------------------------------------------
# Additional Edge Case Tests
# ---------------------------------------------------------------------------

def test_rob_limit_exactly_three():
    """
    Test that rob attempts are blocked when rob_count is exactly 3.
    
    This verifies the boundary condition at the daily limit.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = "2024-01-15"
        rob_count = 3
        
        # Set up user with rob_count = 3
        update_rob_data(test_db, guild_id, user_id, today, rob_count)
        
        # Check if user can rob
        can_rob, error_message = check_rob_limit(test_db, guild_id, user_id, today)
        
        # Should be blocked
        assert can_rob is False, f"User with rob_count=3 should be blocked, got can_rob={can_rob}"
        assert "Heat is too high" in error_message, f"Error message should mention heat, got '{error_message}'"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_limit_below_three_allowed():
    """
    Test that rob attempts are allowed when rob_count < 3.
    
    This verifies that users can rob when they haven't reached the limit.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = "2024-01-15"
        
        # Test rob_count = 0, 1, 2 (all should be allowed)
        for rob_count in [0, 1, 2]:
            # Set up user with rob_count < 3
            update_rob_data(test_db, guild_id, user_id, today, rob_count)
            
            # Check if user can rob
            can_rob, error_message = check_rob_limit(test_db, guild_id, user_id, today)
            
            # Should be allowed
            assert can_rob is True, \
                f"User with rob_count={rob_count} should be allowed to rob, got can_rob={can_rob}"
            assert error_message == "", \
                f"Error message should be empty when allowed, got '{error_message}'"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_limit_high_count():
    """
    Test that rob attempts are blocked even with very high rob_count values.
    
    This verifies the limit enforcement works for edge cases beyond 3.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = "2024-01-15"
        
        # Test with high rob_count values
        for rob_count in [3, 5, 10, 100, 1000]:
            # Set up user with high rob_count
            update_rob_data(test_db, guild_id, user_id, today, rob_count)
            
            # Check if user can rob
            can_rob, error_message = check_rob_limit(test_db, guild_id, user_id, today)
            
            # Should be blocked
            assert can_rob is False, \
                f"User with rob_count={rob_count} should be blocked, got can_rob={can_rob}"
            assert "Heat is too high" in error_message, \
                f"Error message should mention heat for rob_count={rob_count}, got '{error_message}'"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_limit_reset_after_new_day():
    """
    Test that rob limit is reset after a new day, allowing 3 more attempts.
    
    This verifies that the daily limit resets correctly at midnight.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        old_date = "2024-01-15"
        new_date = "2024-01-16"
        
        # Set up user with rob_count = 3 on old_date (blocked)
        update_rob_data(test_db, guild_id, user_id, old_date, 3)
        
        # Verify user is blocked on old_date
        can_rob_old, _ = check_rob_limit(test_db, guild_id, user_id, old_date)
        assert can_rob_old is False, "User should be blocked on old_date with rob_count=3"
        
        # Check on new_date (should reset and allow)
        can_rob_new, error_message = check_rob_limit(test_db, guild_id, user_id, new_date)
        
        # Should be allowed after reset
        assert can_rob_new is True, \
            f"User should be allowed to rob on new_date after reset, got can_rob={can_rob_new}"
        assert error_message == "", \
            f"Error message should be empty after reset, got '{error_message}'"
        
        # Verify rob_count was reset to 0
        last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
        assert rob_count == 0, f"rob_count should be reset to 0 on new day, got {rob_count}"
        assert last_rob_date == new_date, f"last_rob_date should be updated to '{new_date}', got '{last_rob_date}'"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_limit_multiple_users_independent():
    """
    Test that rob limits are enforced independently for different users.
    
    This verifies user isolation in limit enforcement.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user1_id = 111111111
        user2_id = 222222222
        today = "2024-01-15"
        
        # Set up user1 with rob_count = 3 (blocked)
        update_rob_data(test_db, guild_id, user1_id, today, 3)
        
        # Set up user2 with rob_count = 1 (allowed)
        update_rob_data(test_db, guild_id, user2_id, today, 1)
        
        # Check user1 (should be blocked)
        can_rob1, error_message1 = check_rob_limit(test_db, guild_id, user1_id, today)
        assert can_rob1 is False, f"User1 should be blocked, got can_rob={can_rob1}"
        
        # Check user2 (should be allowed)
        can_rob2, error_message2 = check_rob_limit(test_db, guild_id, user2_id, today)
        assert can_rob2 is True, f"User2 should be allowed, got can_rob={can_rob2}"
        assert error_message2 == "", f"User2 error message should be empty, got '{error_message2}'"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_limit_multiple_guilds_independent():
    """
    Test that rob limits are enforced independently across different guilds.
    
    This verifies guild isolation in limit enforcement.
    """
    test_db = create_test_db()
    
    try:
        guild1_id = 111111111
        guild2_id = 222222222
        user_id = 987654321
        today = "2024-01-15"
        
        # Set up same user in guild1 with rob_count = 3 (blocked)
        update_rob_data(test_db, guild1_id, user_id, today, 3)
        
        # Set up same user in guild2 with rob_count = 0 (allowed)
        update_rob_data(test_db, guild2_id, user_id, today, 0)
        
        # Check guild1 (should be blocked)
        can_rob1, error_message1 = check_rob_limit(test_db, guild1_id, user_id, today)
        assert can_rob1 is False, f"Guild1 should be blocked, got can_rob={can_rob1}"
        
        # Check guild2 (should be allowed)
        can_rob2, error_message2 = check_rob_limit(test_db, guild2_id, user_id, today)
        assert can_rob2 is True, f"Guild2 should be allowed, got can_rob={can_rob2}"
        assert error_message2 == "", f"Guild2 error message should be empty, got '{error_message2}'"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_limit_first_time_user_allowed():
    """
    Test that first-time users (rob_count = 0) are allowed to rob.
    
    This verifies that new users can start robbing immediately.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = "2024-01-15"
        
        # Don't set up any data (first-time user)
        # Verify user doesn't exist yet
        last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
        assert last_rob_date is None, "First-time user should have NULL last_rob_date"
        assert rob_count == 0, "First-time user should have 0 rob_count"
        
        # Check if user can rob
        can_rob, error_message = check_rob_limit(test_db, guild_id, user_id, today)
        
        # Should be allowed
        assert can_rob is True, f"First-time user should be allowed to rob, got can_rob={can_rob}"
        assert error_message == "", f"Error message should be empty for first-time user, got '{error_message}'"
        
    finally:
        cleanup_test_db(test_db)
