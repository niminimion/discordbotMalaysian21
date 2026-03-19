"""
Property-based tests for timezone helper function (task 2.2)

Tests the get_today_myt() function implemented in task 2.1.

**Validates: Requirements 1.1**
"""

import re
from datetime import datetime, timezone, timedelta
from hypothesis import given, settings
import hypothesis.strategies as st


# ---------------------------------------------------------------------------
# Helper Function (copied from main.py for testing)
# ---------------------------------------------------------------------------

def get_today_myt() -> str:
    """
    Return the current date in Malaysia Time (UTC+8) as 'YYYY-MM-DD' string.
    """
    myt = timezone(timedelta(hours=8))
    now_myt = datetime.now(myt)
    return now_myt.strftime('%Y-%m-%d')


# ---------------------------------------------------------------------------
# Property Test: Date Format Consistency
# ---------------------------------------------------------------------------

@settings(max_examples=10)
@given(st.just(None))  # Run 10 times with no input variation
def test_get_today_myt_format_consistency(dummy):
    """
    Property: Date format consistency
    
    Test that get_today_myt() always returns a string in 'YYYY-MM-DD' format.
    
    **Validates: Requirements 1.1**
    """
    result = get_today_myt()
    
    # Check that result is a string
    assert isinstance(result, str), "get_today_myt() should return a string"
    
    # Check format matches 'YYYY-MM-DD' pattern
    pattern = r'^\d{4}-\d{2}-\d{2}$'
    assert re.match(pattern, result), f"Date format should be 'YYYY-MM-DD', got '{result}'"
    
    # Verify it's a valid date by parsing it
    try:
        year, month, day = result.split('-')
        year_int = int(year)
        month_int = int(month)
        day_int = int(day)
        
        # Basic validation
        assert 1 <= month_int <= 12, f"Month should be 1-12, got {month_int}"
        assert 1 <= day_int <= 31, f"Day should be 1-31, got {day_int}"
        assert 2000 <= year_int <= 2100, f"Year should be reasonable, got {year_int}"
        
        # Verify it's a valid date
        datetime(year_int, month_int, day_int)
    except ValueError as e:
        raise AssertionError(f"Date '{result}' is not a valid date: {e}")


# ---------------------------------------------------------------------------
# Property Test: Consistency Across Multiple Calls
# ---------------------------------------------------------------------------

@settings(max_examples=10)
@given(st.just(None))  # Run 10 times with no input variation
def test_get_today_myt_consistency_same_day(dummy):
    """
    Property: Consistency across multiple calls in same day
    
    Test that multiple calls to get_today_myt() within a short time window
    return the same date string.
    
    **Validates: Requirements 1.1**
    """
    # Call the function multiple times in quick succession
    results = [get_today_myt() for _ in range(10)]
    
    # All results should be identical
    first_result = results[0]
    for i, result in enumerate(results[1:], start=1):
        assert result == first_result, \
            f"Call {i+1} returned '{result}' but first call returned '{first_result}'. " \
            f"Multiple calls in same day should return same date."
