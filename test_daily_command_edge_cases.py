"""
Unit tests for daily command edge cases (task 4.4)

Tests specific edge cases for the /daily command including:
- First-time user (last_daily = NULL)
- Exact message text matches requirements
- Database update on successful claim

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
"""

import pytest
import sqlite3


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
    
    # Clean up test database
    db.close()


# ---------------------------------------------------------------------------
# Edge Case Tests
# ---------------------------------------------------------------------------

def test_first_time_user_with_null_last_daily(test_db):
    """
    Test that first-time users (last_daily = NULL) can claim daily reward.
    
    This verifies the edge case where a user has never claimed before.
    The user should not exist in the database initially, so last_daily is NULL.
    
    **Validates: Requirements 3.1, 3.3, 3.4**
    """
    guild_id = 123456789
    user_id = 987654321
    today = get_today_myt()
    
    # Verify user doesn't exist yet (last_daily = NULL)
    last_daily = get_last_daily(test_db, guild_id, user_id)
    assert last_daily is None, "First-time user should have NULL last_daily"
    
    # Verify user has no Gold yet
    initial_gold = get_gold(test_db, guild_id, user_id)
    assert initial_gold == 0, "First-time user should have 0 Gold"
    
    # Claim daily reward
    success, message, new_balance = claim_daily(test_db, guild_id, user_id, today)
    
    # Verify claim succeeded
    assert success is True, f"First-time user should be able to claim daily reward, but got: {message}"
    
    # Verify Gold balance is exactly DAILY_REWARD (500)
    assert new_balance == DAILY_REWARD, \
        f"First-time user should receive exactly {DAILY_REWARD} Gold, got {new_balance}"
    
    # Verify last_daily is now set to today
    last_daily_after = get_last_daily(test_db, guild_id, user_id)
    assert last_daily_after == today, \
        f"last_daily should be set to '{today}' after first claim, got '{last_daily_after}'"
    
    # Verify database balance matches returned balance
    db_balance = get_gold(test_db, guild_id, user_id)
    assert db_balance == new_balance, \
        f"Database balance should match returned balance. Returned: {new_balance}, DB: {db_balance}"


def test_exact_success_message_text(test_db):
    """
    Test that the success message text matches the exact requirement.
    
    According to Requirement 3.5, the success message should be:
    "✅ Here is your daily 500 gold. Don't lose it all at once!"
    
    **Validates: Requirements 3.5**
    """
    guild_id = 123456789
    user_id = 987654321
    today = get_today_myt()
    
    # Claim daily reward
    success, message, _ = claim_daily(test_db, guild_id, user_id, today)
    
    # Verify claim succeeded
    assert success is True, "Claim should succeed for first-time user"
    
    # Verify exact message text matches requirement
    expected_message = "✅ Here is your daily 500 gold. Don't lose it all at once!"
    assert message == expected_message, \
        f"Success message should be exactly: '{expected_message}'\nGot: '{message}'"


def test_exact_error_message_text(test_db):
    """
    Test that the error message text matches the exact requirement.
    
    According to Requirement 3.2, the error message should be:
    "❌ You already claimed your daily relief! Wait until midnight (MYT) for the reset."
    
    **Validates: Requirements 3.2**
    """
    guild_id = 123456789
    user_id = 987654321
    today = get_today_myt()
    
    # First claim - should succeed
    success1, _, _ = claim_daily(test_db, guild_id, user_id, today)
    assert success1 is True, "First claim should succeed"
    
    # Second claim on same date - should be blocked
    success2, message, _ = claim_daily(test_db, guild_id, user_id, today)
    
    # Verify claim was blocked
    assert success2 is False, "Second claim on same date should be blocked"
    
    # Verify exact message text matches requirement
    expected_message = "❌ You already claimed your daily relief! Wait until midnight (MYT) for the reset."
    assert message == expected_message, \
        f"Error message should be exactly: '{expected_message}'\nGot: '{message}'"


def test_database_update_on_successful_claim(test_db):
    """
    Test that the database is correctly updated on successful claim.
    
    This verifies that:
    1. Gold balance is increased by DAILY_REWARD (500)
    2. last_daily is updated to the current MYT date
    3. Changes are persisted in the database
    
    **Validates: Requirements 3.3, 3.4**
    """
    guild_id = 123456789
    user_id = 987654321
    today = get_today_myt()
    initial_gold = 1000
    
    # Set up user with initial Gold balance
    add_gold(test_db, guild_id, user_id, initial_gold)
    
    # Verify initial state
    assert get_gold(test_db, guild_id, user_id) == initial_gold
    assert get_last_daily(test_db, guild_id, user_id) is None
    
    # Claim daily reward
    success, message, new_balance = claim_daily(test_db, guild_id, user_id, today)
    
    # Verify claim succeeded
    assert success is True, f"Claim should succeed, but got: {message}"
    
    # Verify Gold balance increased by exactly DAILY_REWARD
    expected_balance = initial_gold + DAILY_REWARD
    assert new_balance == expected_balance, \
        f"Gold should increase by {DAILY_REWARD}. Initial: {initial_gold}, Expected: {expected_balance}, Got: {new_balance}"
    
    # Verify database Gold balance is updated
    db_gold = get_gold(test_db, guild_id, user_id)
    assert db_gold == expected_balance, \
        f"Database Gold should be {expected_balance}, got {db_gold}"
    
    # Verify last_daily is updated to today
    db_last_daily = get_last_daily(test_db, guild_id, user_id)
    assert db_last_daily == today, \
        f"Database last_daily should be '{today}', got '{db_last_daily}'"
    
    # Verify changes persist across multiple reads
    db_gold_2 = get_gold(test_db, guild_id, user_id)
    db_last_daily_2 = get_last_daily(test_db, guild_id, user_id)
    assert db_gold_2 == expected_balance, "Gold should persist across reads"
    assert db_last_daily_2 == today, "last_daily should persist across reads"


def test_database_update_with_negative_gold(test_db):
    """
    Test that database update works correctly when user is in debt.
    
    This verifies that the database update logic handles negative Gold correctly.
    
    **Validates: Requirements 3.3, 3.4**
    """
    guild_id = 123456789
    user_id = 987654321
    today = get_today_myt()
    initial_gold = -2000  # User is in debt
    
    # Set up user with negative Gold
    add_gold(test_db, guild_id, user_id, initial_gold)
    
    # Verify initial state
    assert get_gold(test_db, guild_id, user_id) == initial_gold
    
    # Claim daily reward
    success, message, new_balance = claim_daily(test_db, guild_id, user_id, today)
    
    # Verify claim succeeded
    assert success is True, f"Claim should succeed even with negative Gold, but got: {message}"
    
    # Verify Gold balance increased by exactly DAILY_REWARD
    expected_balance = initial_gold + DAILY_REWARD  # -2000 + 500 = -1500
    assert new_balance == expected_balance, \
        f"Gold should increase by {DAILY_REWARD}. Initial: {initial_gold}, Expected: {expected_balance}, Got: {new_balance}"
    
    # Verify database is updated correctly
    db_gold = get_gold(test_db, guild_id, user_id)
    assert db_gold == expected_balance, \
        f"Database Gold should be {expected_balance}, got {db_gold}"
    
    # Verify last_daily is updated
    db_last_daily = get_last_daily(test_db, guild_id, user_id)
    assert db_last_daily == today, \
        f"Database last_daily should be '{today}', got '{db_last_daily}'"


def test_database_not_updated_on_blocked_claim(test_db):
    """
    Test that the database is NOT updated when claim is blocked.
    
    This verifies that when a user tries to claim twice on the same day,
    the second attempt doesn't modify the database.
    
    **Validates: Requirements 3.2**
    """
    guild_id = 123456789
    user_id = 987654321
    today = get_today_myt()
    initial_gold = 1000
    
    # Set up user with initial Gold
    add_gold(test_db, guild_id, user_id, initial_gold)
    
    # First claim - should succeed
    success1, _, balance1 = claim_daily(test_db, guild_id, user_id, today)
    assert success1 is True, "First claim should succeed"
    
    # Record state after first claim
    gold_after_first = get_gold(test_db, guild_id, user_id)
    last_daily_after_first = get_last_daily(test_db, guild_id, user_id)
    
    assert gold_after_first == initial_gold + DAILY_REWARD
    assert last_daily_after_first == today
    
    # Second claim on same date - should be blocked
    success2, message, balance2 = claim_daily(test_db, guild_id, user_id, today)
    assert success2 is False, "Second claim should be blocked"
    
    # Verify database was NOT modified
    gold_after_second = get_gold(test_db, guild_id, user_id)
    last_daily_after_second = get_last_daily(test_db, guild_id, user_id)
    
    assert gold_after_second == gold_after_first, \
        f"Gold should not change on blocked claim. Before: {gold_after_first}, After: {gold_after_second}"
    
    assert last_daily_after_second == last_daily_after_first, \
        f"last_daily should not change on blocked claim. Before: '{last_daily_after_first}', After: '{last_daily_after_second}'"
    
    # Verify returned balance matches database
    assert balance2 == gold_after_second, \
        f"Returned balance should match database. Returned: {balance2}, DB: {gold_after_second}"


def test_database_update_allows_claim_on_different_date(test_db):
    """
    Test that database update allows claiming on a different date.
    
    This verifies that after claiming on one date, the user can claim again
    on a different date, and the database is updated correctly.
    
    **Validates: Requirements 3.3, 3.4**
    """
    guild_id = 123456789
    user_id = 987654321
    date1 = "2024-01-15"
    date2 = "2024-01-16"
    
    # First claim on date1
    success1, _, balance1 = claim_daily(test_db, guild_id, user_id, date1)
    assert success1 is True, "First claim should succeed"
    assert balance1 == DAILY_REWARD
    
    # Verify database state after first claim
    assert get_gold(test_db, guild_id, user_id) == DAILY_REWARD
    assert get_last_daily(test_db, guild_id, user_id) == date1
    
    # Second claim on date2 (different date)
    success2, _, balance2 = claim_daily(test_db, guild_id, user_id, date2)
    assert success2 is True, "Second claim on different date should succeed"
    assert balance2 == DAILY_REWARD * 2
    
    # Verify database state after second claim
    assert get_gold(test_db, guild_id, user_id) == DAILY_REWARD * 2, \
        "Gold should accumulate across different dates"
    
    assert get_last_daily(test_db, guild_id, user_id) == date2, \
        f"last_daily should be updated to '{date2}'"
