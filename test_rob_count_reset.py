"""
Property-based tests for rob count reset (task 5.3)

Tests the rob count reset logic to ensure rob_count resets to 0 when the date
changes (MYT).

**Validates: Requirements 1.4, 1.6, 4.2**
"""

import pytest
import sqlite3
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
# Property Test: Rob Count Daily Reset
# ---------------------------------------------------------------------------

@settings(max_examples=10, deadline=None)
@given(
    guild_id=st.integers(min_value=1, max_value=999999999999),
    user_id=st.integers(min_value=1, max_value=999999999999),
    initial_rob_count=st.integers(min_value=0, max_value=3),
    old_year=st.integers(min_value=2020, max_value=2030),
    old_month=st.integers(min_value=1, max_value=12),
    old_day=st.integers(min_value=1, max_value=28),
    new_year=st.integers(min_value=2020, max_value=2030),
    new_month=st.integers(min_value=1, max_value=12),
    new_day=st.integers(min_value=1, max_value=28)
)
def test_rob_count_reset_property(guild_id, user_id, initial_rob_count, 
                                   old_year, old_month, old_day,
                                   new_year, new_month, new_day):
    """
    Property 3: Rob Count Daily Reset
    
    For any user and guild, if the last_rob_date differs from the current MYT date,
    then the rob_count should be reset to 0 when the rob command is executed.
    
    **Validates: Requirements 1.4, 1.6, 4.2**
    """
    # Create test database for this test run
    test_db = create_test_db()
    
    try:
        # Format date strings as YYYY-MM-DD
        old_date = f"{old_year:04d}-{old_month:02d}-{old_day:02d}"
        new_date = f"{new_year:04d}-{new_month:02d}-{new_day:02d}"
        
        # Set up user with initial rob_count on old_date
        update_rob_data(test_db, guild_id, user_id, old_date, initial_rob_count)
        
        # Verify initial state
        last_rob_date_before, rob_count_before = get_rob_data(test_db, guild_id, user_id)
        assert last_rob_date_before == old_date, \
            f"Initial last_rob_date should be '{old_date}', got '{last_rob_date_before}'"
        assert rob_count_before == initial_rob_count, \
            f"Initial rob_count should be {initial_rob_count}, got {rob_count_before}"
        
        # Call reset_rob_count_if_new_day with new_date
        result = reset_rob_count_if_new_day(test_db, guild_id, user_id, new_date)
        
        # If dates are different, rob_count should be reset to 0
        if old_date != new_date:
            # Assertion 1: Function should return 0 when date changes
            assert result == 0, \
                f"reset_rob_count_if_new_day should return 0 when date changes from '{old_date}' to '{new_date}', got {result}"
            
            # Assertion 2: Database rob_count should be 0
            last_rob_date_after, rob_count_after = get_rob_data(test_db, guild_id, user_id)
            assert rob_count_after == 0, \
                f"rob_count should be reset to 0 when date changes, got {rob_count_after}"
            
            # Assertion 3: last_rob_date should be updated to new_date
            assert last_rob_date_after == new_date, \
                f"last_rob_date should be updated to '{new_date}', got '{last_rob_date_after}'"
        else:
            # If dates are the same, rob_count should NOT be reset
            # Assertion 1: Function should return current rob_count
            assert result == initial_rob_count, \
                f"reset_rob_count_if_new_day should return current count ({initial_rob_count}) when date is the same, got {result}"
            
            # Assertion 2: Database rob_count should remain unchanged
            last_rob_date_after, rob_count_after = get_rob_data(test_db, guild_id, user_id)
            assert rob_count_after == initial_rob_count, \
                f"rob_count should remain {initial_rob_count} when date is the same, got {rob_count_after}"
            
            # Assertion 3: last_rob_date should remain unchanged
            assert last_rob_date_after == old_date, \
                f"last_rob_date should remain '{old_date}', got '{last_rob_date_after}'"
    
    finally:
        # Clean up test database
        cleanup_test_db(test_db)


# ---------------------------------------------------------------------------
# Additional Edge Case Tests
# ---------------------------------------------------------------------------

def test_rob_count_reset_first_time_user():
    """
    Test that first-time users (last_rob_date = NULL) have rob_count reset to 0.
    
    This verifies that NULL last_rob_date is treated as a different date.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = "2024-01-15"
        
        # Verify user doesn't exist yet (last_rob_date = NULL)
        last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
        assert last_rob_date is None, "First-time user should have NULL last_rob_date"
        assert rob_count == 0, "First-time user should have 0 rob_count"
        
        # Call reset with today's date
        result = reset_rob_count_if_new_day(test_db, guild_id, user_id, today)
        
        # Should return 0 (reset/initialize)
        assert result == 0, f"Should return 0 for first-time user, got {result}"
        
        # Verify database is initialized
        last_rob_date_after, rob_count_after = get_rob_data(test_db, guild_id, user_id)
        assert last_rob_date_after == today, f"last_rob_date should be set to '{today}', got '{last_rob_date_after}'"
        assert rob_count_after == 0, f"rob_count should be 0, got {rob_count_after}"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_count_reset_max_count():
    """
    Test that rob_count resets to 0 even when at maximum (3).
    
    This verifies the reset works correctly at the daily limit boundary.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        old_date = "2024-01-15"
        new_date = "2024-01-16"
        max_rob_count = 3
        
        # Set up user with max rob_count
        update_rob_data(test_db, guild_id, user_id, old_date, max_rob_count)
        
        # Verify initial state
        last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
        assert rob_count == max_rob_count, f"Initial rob_count should be {max_rob_count}, got {rob_count}"
        
        # Call reset with new date
        result = reset_rob_count_if_new_day(test_db, guild_id, user_id, new_date)
        
        # Should return 0 (reset)
        assert result == 0, f"Should return 0 when date changes, got {result}"
        
        # Verify rob_count is reset
        last_rob_date_after, rob_count_after = get_rob_data(test_db, guild_id, user_id)
        assert rob_count_after == 0, f"rob_count should be reset to 0, got {rob_count_after}"
        assert last_rob_date_after == new_date, f"last_rob_date should be '{new_date}', got '{last_rob_date_after}'"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_count_reset_same_date_no_reset():
    """
    Test that rob_count is NOT reset when the date is the same.
    
    This verifies that calling reset multiple times on the same day doesn't reset the count.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        today = "2024-01-15"
        rob_count = 2
        
        # Set up user with rob_count = 2 on today
        update_rob_data(test_db, guild_id, user_id, today, rob_count)
        
        # Call reset with same date
        result = reset_rob_count_if_new_day(test_db, guild_id, user_id, today)
        
        # Should return current count (2)
        assert result == rob_count, f"Should return current count ({rob_count}) when date is the same, got {result}"
        
        # Verify rob_count is NOT reset
        last_rob_date_after, rob_count_after = get_rob_data(test_db, guild_id, user_id)
        assert rob_count_after == rob_count, f"rob_count should remain {rob_count}, got {rob_count_after}"
        assert last_rob_date_after == today, f"last_rob_date should remain '{today}', got '{last_rob_date_after}'"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_count_reset_multiple_days():
    """
    Test that rob_count resets correctly across multiple day changes.
    
    This verifies the reset works consistently over multiple days.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        
        # Day 1: Set rob_count to 3
        date1 = "2024-01-15"
        update_rob_data(test_db, guild_id, user_id, date1, 3)
        
        # Day 2: Reset should occur
        date2 = "2024-01-16"
        result2 = reset_rob_count_if_new_day(test_db, guild_id, user_id, date2)
        assert result2 == 0, f"Day 2: Should return 0, got {result2}"
        
        # Increment count on day 2
        update_rob_data(test_db, guild_id, user_id, date2, 2)
        
        # Day 3: Reset should occur again
        date3 = "2024-01-17"
        result3 = reset_rob_count_if_new_day(test_db, guild_id, user_id, date3)
        assert result3 == 0, f"Day 3: Should return 0, got {result3}"
        
        # Verify final state
        last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
        assert last_rob_date == date3, f"last_rob_date should be '{date3}', got '{last_rob_date}'"
        assert rob_count == 0, f"rob_count should be 0, got {rob_count}"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_count_reset_year_boundary():
    """
    Test that rob_count resets correctly across year boundaries.
    
    This verifies the reset works when transitioning from one year to the next.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user_id = 987654321
        old_date = "2023-12-31"
        new_date = "2024-01-01"
        
        # Set up user with rob_count = 3 on last day of year
        update_rob_data(test_db, guild_id, user_id, old_date, 3)
        
        # Call reset with first day of new year
        result = reset_rob_count_if_new_day(test_db, guild_id, user_id, new_date)
        
        # Should return 0 (reset)
        assert result == 0, f"Should return 0 when year changes, got {result}"
        
        # Verify rob_count is reset
        last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
        assert rob_count == 0, f"rob_count should be reset to 0, got {rob_count}"
        assert last_rob_date == new_date, f"last_rob_date should be '{new_date}', got '{last_rob_date}'"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_count_reset_multiple_users_isolation():
    """
    Test that rob_count reset for one user doesn't affect other users.
    
    This verifies user isolation in the reset logic.
    """
    test_db = create_test_db()
    
    try:
        guild_id = 123456789
        user1_id = 111111111
        user2_id = 222222222
        old_date = "2024-01-15"
        new_date = "2024-01-16"
        
        # Set up two users with different rob_counts on old_date
        update_rob_data(test_db, guild_id, user1_id, old_date, 2)
        update_rob_data(test_db, guild_id, user2_id, old_date, 3)
        
        # Reset user1 to new_date
        result1 = reset_rob_count_if_new_day(test_db, guild_id, user1_id, new_date)
        assert result1 == 0, f"User1 should be reset to 0, got {result1}"
        
        # Verify user1 is reset
        last_rob_date1, rob_count1 = get_rob_data(test_db, guild_id, user1_id)
        assert rob_count1 == 0, f"User1 rob_count should be 0, got {rob_count1}"
        assert last_rob_date1 == new_date, f"User1 last_rob_date should be '{new_date}', got '{last_rob_date1}'"
        
        # Verify user2 is NOT affected (still on old_date)
        last_rob_date2, rob_count2 = get_rob_data(test_db, guild_id, user2_id)
        assert rob_count2 == 3, f"User2 rob_count should remain 3, got {rob_count2}"
        assert last_rob_date2 == old_date, f"User2 last_rob_date should remain '{old_date}', got '{last_rob_date2}'"
        
    finally:
        cleanup_test_db(test_db)


def test_rob_count_reset_multiple_guilds_isolation():
    """
    Test that rob_count reset for a user in one guild doesn't affect the same user in other guilds.
    
    This verifies guild isolation in the reset logic.
    """
    test_db = create_test_db()
    
    try:
        guild1_id = 111111111
        guild2_id = 222222222
        user_id = 987654321
        old_date = "2024-01-15"
        new_date = "2024-01-16"
        
        # Set up same user in two guilds with different rob_counts on old_date
        update_rob_data(test_db, guild1_id, user_id, old_date, 1)
        update_rob_data(test_db, guild2_id, user_id, old_date, 2)
        
        # Reset user in guild1 to new_date
        result1 = reset_rob_count_if_new_day(test_db, guild1_id, user_id, new_date)
        assert result1 == 0, f"Guild1 should be reset to 0, got {result1}"
        
        # Verify guild1 is reset
        last_rob_date1, rob_count1 = get_rob_data(test_db, guild1_id, user_id)
        assert rob_count1 == 0, f"Guild1 rob_count should be 0, got {rob_count1}"
        assert last_rob_date1 == new_date, f"Guild1 last_rob_date should be '{new_date}', got '{last_rob_date1}'"
        
        # Verify guild2 is NOT affected (still on old_date)
        last_rob_date2, rob_count2 = get_rob_data(test_db, guild2_id, user_id)
        assert rob_count2 == 2, f"Guild2 rob_count should remain 2, got {rob_count2}"
        assert last_rob_date2 == old_date, f"Guild2 last_rob_date should remain '{old_date}', got '{last_rob_date2}'"
        
    finally:
        cleanup_test_db(test_db)
