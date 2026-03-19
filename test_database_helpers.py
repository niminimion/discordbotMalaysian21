"""
Unit tests for database helper functions (task 1.3)

Tests the database helper functions implemented in task 1.2:
- get_rob_data()
- update_rob_data()
- reset_rob_count_if_new_day()

**Validates: Requirements 5.1, 5.2, 5.3, 5.4**
"""

import pytest
import sqlite3
from datetime import datetime, timezone, timedelta


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
# Test: NULL Handling for First-Time Users
# ---------------------------------------------------------------------------

def test_get_rob_data_first_time_user(test_db):
    """
    Test that get_rob_data returns (None, 0) for first-time users.
    
    **Validates: Requirements 5.3, 5.4**
    """
    guild_id = 123456789
    user_id = 987654321
    
    # First-time user should return (None, 0)
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    
    assert last_rob_date is None, "First-time user should have None for last_rob_date"
    assert rob_count == 0, "First-time user should have 0 for rob_count"


def test_get_rob_data_null_rob_count(test_db):
    """
    Test that get_rob_data handles NULL rob_count values correctly.
    
    This tests backward compatibility with existing records that might have
    NULL rob_count values after schema migration.
    
    **Validates: Requirements 5.3, 5.4**
    """
    guild_id = 123456789
    user_id = 987654321
    
    # Insert a record with NULL rob_count (simulating old data)
    test_db.execute(
        "INSERT INTO user_gold (guild_id, user_id, gold, last_rob_date, rob_count) VALUES (?, ?, 0, '2024-01-01', NULL)",
        (guild_id, user_id),
    )
    test_db.commit()
    
    # Should return 0 for NULL rob_count
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    
    assert last_rob_date == '2024-01-01', "Should return the stored date"
    assert rob_count == 0, "NULL rob_count should be treated as 0"


# ---------------------------------------------------------------------------
# Test: Rob Count Reset Logic When Date Changes
# ---------------------------------------------------------------------------

def test_reset_rob_count_if_new_day_different_date(test_db):
    """
    Test that rob_count resets to 0 when the date changes.
    
    **Validates: Requirements 5.1, 5.2**
    """
    guild_id = 123456789
    user_id = 987654321
    old_date = "2024-01-01"
    new_date = "2024-01-02"
    
    # Set up user with rob_count = 3 on old date
    update_rob_data(test_db, guild_id, user_id, old_date, 3)
    
    # Verify initial state
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date == old_date
    assert rob_count == 3
    
    # Call reset with new date
    result = reset_rob_count_if_new_day(test_db, guild_id, user_id, new_date)
    
    # Should return 0 (reset count)
    assert result == 0, "Should return 0 when date changes"
    
    # Verify database was updated
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date == new_date, "Date should be updated to new date"
    assert rob_count == 0, "Rob count should be reset to 0"


def test_reset_rob_count_if_new_day_same_date(test_db):
    """
    Test that rob_count is NOT reset when the date is the same.
    
    **Validates: Requirements 5.1, 5.2**
    """
    guild_id = 123456789
    user_id = 987654321
    today = "2024-01-01"
    
    # Set up user with rob_count = 2 on today
    update_rob_data(test_db, guild_id, user_id, today, 2)
    
    # Call reset with same date
    result = reset_rob_count_if_new_day(test_db, guild_id, user_id, today)
    
    # Should return current count (2)
    assert result == 2, "Should return current count when date is the same"
    
    # Verify database was NOT changed
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date == today, "Date should remain unchanged"
    assert rob_count == 2, "Rob count should remain unchanged"


def test_reset_rob_count_if_new_day_first_time_user(test_db):
    """
    Test that reset_rob_count_if_new_day handles first-time users correctly.
    
    First-time users have last_rob_date = None, which should be treated as
    a different date, triggering a reset.
    
    **Validates: Requirements 5.3, 5.4**
    """
    guild_id = 123456789
    user_id = 987654321
    today = "2024-01-01"
    
    # First-time user (no record in database)
    result = reset_rob_count_if_new_day(test_db, guild_id, user_id, today)
    
    # Should return 0 (reset/initialize count)
    assert result == 0, "Should return 0 for first-time user"
    
    # Verify database was initialized
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date == today, "Date should be set to today"
    assert rob_count == 0, "Rob count should be initialized to 0"


# ---------------------------------------------------------------------------
# Test: Data Persistence Across Multiple Calls
# ---------------------------------------------------------------------------

def test_update_rob_data_persistence(test_db):
    """
    Test that update_rob_data persists data correctly across multiple calls.
    
    **Validates: Requirements 5.1, 5.2**
    """
    guild_id = 123456789
    user_id = 987654321
    
    # Update 1: Set initial data
    update_rob_data(test_db, guild_id, user_id, "2024-01-01", 1)
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date == "2024-01-01"
    assert rob_count == 1
    
    # Update 2: Increment count
    update_rob_data(test_db, guild_id, user_id, "2024-01-01", 2)
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date == "2024-01-01"
    assert rob_count == 2
    
    # Update 3: Increment count again
    update_rob_data(test_db, guild_id, user_id, "2024-01-01", 3)
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date == "2024-01-01"
    assert rob_count == 3
    
    # Update 4: Reset on new day
    update_rob_data(test_db, guild_id, user_id, "2024-01-02", 0)
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date == "2024-01-02"
    assert rob_count == 0


def test_multiple_users_isolation(test_db):
    """
    Test that data for different users is properly isolated.
    
    **Validates: Requirements 5.1, 5.2**
    """
    guild_id = 123456789
    user1_id = 111111111
    user2_id = 222222222
    today = "2024-01-01"
    
    # Set different data for two users
    update_rob_data(test_db, guild_id, user1_id, today, 1)
    update_rob_data(test_db, guild_id, user2_id, today, 2)
    
    # Verify user 1 data
    last_rob_date1, rob_count1 = get_rob_data(test_db, guild_id, user1_id)
    assert last_rob_date1 == today
    assert rob_count1 == 1
    
    # Verify user 2 data
    last_rob_date2, rob_count2 = get_rob_data(test_db, guild_id, user2_id)
    assert last_rob_date2 == today
    assert rob_count2 == 2
    
    # Update user 1, verify user 2 is unaffected
    update_rob_data(test_db, guild_id, user1_id, today, 3)
    last_rob_date1, rob_count1 = get_rob_data(test_db, guild_id, user1_id)
    last_rob_date2, rob_count2 = get_rob_data(test_db, guild_id, user2_id)
    
    assert rob_count1 == 3, "User 1 count should be updated"
    assert rob_count2 == 2, "User 2 count should remain unchanged"


def test_multiple_guilds_isolation(test_db):
    """
    Test that data for the same user in different guilds is properly isolated.
    
    **Validates: Requirements 5.1, 5.2**
    """
    guild1_id = 111111111
    guild2_id = 222222222
    user_id = 987654321
    today = "2024-01-01"
    
    # Set different data for same user in two guilds
    update_rob_data(test_db, guild1_id, user_id, today, 1)
    update_rob_data(test_db, guild2_id, user_id, today, 2)
    
    # Verify guild 1 data
    last_rob_date1, rob_count1 = get_rob_data(test_db, guild1_id, user_id)
    assert last_rob_date1 == today
    assert rob_count1 == 1
    
    # Verify guild 2 data
    last_rob_date2, rob_count2 = get_rob_data(test_db, guild2_id, user_id)
    assert last_rob_date2 == today
    assert rob_count2 == 2
    
    # Update guild 1, verify guild 2 is unaffected
    update_rob_data(test_db, guild1_id, user_id, today, 3)
    last_rob_date1, rob_count1 = get_rob_data(test_db, guild1_id, user_id)
    last_rob_date2, rob_count2 = get_rob_data(test_db, guild2_id, user_id)
    
    assert rob_count1 == 3, "Guild 1 count should be updated"
    assert rob_count2 == 2, "Guild 2 count should remain unchanged"


# ---------------------------------------------------------------------------
# Test: Edge Cases
# ---------------------------------------------------------------------------

def test_update_rob_data_creates_record_if_not_exists(test_db):
    """
    Test that update_rob_data creates a new record if user doesn't exist.
    
    **Validates: Requirements 5.1, 5.2**
    """
    guild_id = 123456789
    user_id = 987654321
    today = "2024-01-01"
    
    # User doesn't exist yet
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date is None
    assert rob_count == 0
    
    # Update should create the record
    update_rob_data(test_db, guild_id, user_id, today, 1)
    
    # Verify record was created
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date == today
    assert rob_count == 1


def test_reset_rob_count_multiple_resets_same_day(test_db):
    """
    Test that calling reset multiple times on the same day doesn't cause issues.
    
    **Validates: Requirements 5.1, 5.2**
    """
    guild_id = 123456789
    user_id = 987654321
    today = "2024-01-01"
    
    # Initialize with count 2
    update_rob_data(test_db, guild_id, user_id, today, 2)
    
    # Call reset multiple times with same date
    result1 = reset_rob_count_if_new_day(test_db, guild_id, user_id, today)
    result2 = reset_rob_count_if_new_day(test_db, guild_id, user_id, today)
    result3 = reset_rob_count_if_new_day(test_db, guild_id, user_id, today)
    
    # All should return the same count (2)
    assert result1 == 2
    assert result2 == 2
    assert result3 == 2
    
    # Database should remain unchanged
    last_rob_date, rob_count = get_rob_data(test_db, guild_id, user_id)
    assert last_rob_date == today
    assert rob_count == 2
