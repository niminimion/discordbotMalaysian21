# Design Document: Daily Rob Economy System

## Overview

This design implements a daily reward system and PvP robbery mechanic for the Discord casino bot. The system introduces two new slash commands (`/daily` and `/rob`) with global midnight resets synchronized to Malaysia Time (UTC+8) and economic safeguards to prevent unlimited debt accumulation.

The design extends the existing Gold economy with:
- A timezone-aware daily reset mechanism that replaces the current 24-hour cooldown
- A PvP robbery system with daily limits and risk/reward mechanics
- Economic firewall constants (MAX_BET, MAX_DEBT) to prevent debt spiraling
- Database schema extensions to track robbery activity

This feature integrates with the existing debt system (30% Ah Long interest) and applies economic constraints across all gambling commands (/slots, /bj, /ht).

## Architecture

### System Components

The implementation follows the existing bot architecture with these additions:

1. **Timezone Helper Module**: Pure function `get_today_myt()` that returns current MYT date
2. **Economic Firewall Layer**: Validation logic applied before bet execution
3. **Daily Command Handler**: New slash command with MYT-based reset logic
4. **Rob Command Handler**: New slash command with daily limits and PvP mechanics
5. **Database Extensions**: New columns in `user_gold` table for robbery tracking

### Integration Points

- **Existing Gambling Commands**: `/slots`, `/bj`, `/ht` will integrate MAX_BET and MAX_DEBT validation
- **Debt Confirmation Views**: `HTDebtConfirmView`, `BjDebtConfirmView`, `SlotsDebtConfirmView` will integrate MAX_DEBT checks
- **Database Layer**: Extends existing `user_gold` table with new columns

### Data Flow

```
User executes /daily or /rob
    ↓
Fetch user record from database
    ↓
Compare last_daily/last_rob_date with get_today_myt()
    ↓
If dates differ → Reset allowed
If dates match → Block with cooldown message
    ↓
Execute command logic
    ↓
Update database with new timestamp/counts
```

For gambling commands with economic firewall:

```
User places bet in /slots, /bj, or /ht
    ↓
Validate bet <= MAX_BET
    ↓
If user in debt: Calculate projected_debt = current_gold - int(bet * 1.3)
    ↓
Validate projected_debt >= MAX_DEBT
    ↓
If validation passes → Execute bet
If validation fails → Reject with error message
```

## Components and Interfaces

### 1. Timezone Helper Function

```python
def get_today_myt() -> str:
    """
    Return the current date in Malaysia Time (UTC+8) as 'YYYY-MM-DD' string.
    
    Uses Python standard library: datetime, timezone, timedelta.
    """
    pass
```

**Purpose**: Provides consistent date calculation for all daily reset mechanics.

**Implementation Notes**:
- Uses `datetime.datetime.now(timezone(timedelta(hours=8)))` to get MYT time
- Formats as `strftime('%Y-%m-%d')` for database storage
- Pure function with no side effects

### 2. Economic Firewall Constants

```python
MAX_BET: int = 5000   # Maximum bet amount across all gambling commands
MAX_DEBT: int = -10000  # Minimum allowed Gold balance (debt floor)
```

**Purpose**: Prevents unlimited debt accumulation and maintains economic balance.

**Usage**: Applied in validation logic before bet execution.

### 3. Daily Command Handler

```python
@bot.tree.command(name="daily", description="Claim 500 Gold once per day (resets at midnight MYT)")
async def cmd_daily(interaction: discord.Interaction) -> None:
    """
    Award 500 Gold to user if they haven't claimed today (MYT).
    
    Replaces existing /daily command that uses 24-hour cooldown.
    """
    pass
```

**Behavior**:
- Fetches `last_daily` from database
- Compares with `get_today_myt()`
- If match: Reject with message "❌ You already claimed your daily relief! Wait until midnight (MYT) for the reset."
- If different: Award 500 Gold, update `last_daily` to `get_today_myt()`
- Success message: "✅ Here is your daily 500 gold. Don't lose it all at once!"

**Changes from Existing**:
- Current implementation awards 300 Gold with 24-hour cooldown from last claim
- New implementation awards 500 Gold with global midnight MYT reset
- Requires updating DAILY_REWARD constant from 300 to 500

### 4. Rob Command Handler

```python
@bot.tree.command(name="rob", description="Rob another player for Gold (3 attempts per day)")
@app_commands.describe(target="The player to rob")
async def cmd_rob(interaction: discord.Interaction, target: discord.Member) -> None:
    """
    Attempt to rob Gold from another player.
    
    Success rate: 40%
    Success reward: 20-30% of target's Gold
    Failure penalty: 10-15% of robber's Gold
    Daily limit: 3 attempts (resets at midnight MYT)
    """
    pass
```

**Validation Logic**:
1. Check if `last_rob_date` != `get_today_myt()` → Reset `rob_count` to 0
2. Check if `rob_count` >= 3 → Reject with "🛑 Heat is too high! You've already robbed 3 times today. Lay low until midnight."
3. Check if target is self → Reject with error
4. Check if target Gold <= 0 → Reject with "❌ Target is too broke. Even Ah Long is looking for them. Pick someone else!"

**Execution Logic**:
1. Increment `rob_count` by 1
2. Generate random number to determine success (40% probability)
3. If success:
   - Calculate stolen = random(20%, 30%) of target's Gold
   - Transfer stolen amount from target to robber
   - Display success message with amounts
4. If failure:
   - Calculate penalty = random(10%, 15%) of robber's Gold
   - Deduct penalty from robber
   - Display failure message with penalty

**Database Updates**:
- Update `rob_count` after validation passes
- Update `last_rob_date` to `get_today_myt()` if date changed
- Update Gold balances for both users

### 5. Economic Firewall Integration

**Validation Function**:

```python
def validate_bet_with_firewall(guild_id: int, user_id: int, bet: int) -> tuple[bool, str]:
    """
    Validate bet against economic firewall rules.
    
    Returns (is_valid, error_message).
    If is_valid is True, error_message is empty.
    """
    pass
```

**Integration Points**:
- `cmd_cointoss` (/ht): Add validation before bet execution
- `cmd_slots` (/slots): Add validation before bet execution
- `cmd_bj` (/bj): Add validation in `LobbyView` before game start
- `HTDebtConfirmView.confirm`: Add MAX_DEBT check before flip
- `BjDebtConfirmView.confirm`: Add MAX_DEBT check before joining
- `SlotsDebtConfirmView.confirm`: Add MAX_DEBT check before spin

**Validation Logic**:
```python
# Check MAX_BET
if bet > MAX_BET:
    return False, f"❌ Bet exceeds maximum allowed ({MAX_BET:,} 💰)"

# Check MAX_DEBT for in-debt players
current_gold = get_gold(guild_id, user_id)
if current_gold <= 0:
    projected_debt = current_gold - int(bet * 1.3)
    if projected_debt < MAX_DEBT:
        return False, f"🚫 Ah Long credit limit reached! Maximum debt is {MAX_DEBT:,} 💰. Your projected debt would be {projected_debt:,} 💰."

return True, ""
```

### 6. Database Helper Functions

```python
def get_rob_data(guild_id: int, user_id: int) -> tuple[str | None, int]:
    """
    Fetch (last_rob_date, rob_count) for a user.
    Returns (None, 0) for first-time users.
    """
    pass

def update_rob_data(guild_id: int, user_id: int, date: str, count: int) -> None:
    """
    Update last_rob_date and rob_count for a user.
    """
    pass

def reset_rob_count_if_new_day(guild_id: int, user_id: int) -> int:
    """
    Check if last_rob_date differs from today (MYT).
    If different, reset rob_count to 0 and update date.
    Returns current rob_count.
    """
    pass
```

## Data Models

### Database Schema Changes

**Existing `user_gold` table**:
```sql
CREATE TABLE user_gold (
    guild_id   BIGINT  NOT NULL,
    user_id    BIGINT  NOT NULL,
    gold       INTEGER NOT NULL DEFAULT 0,
    last_daily TEXT,
    PRIMARY KEY (guild_id, user_id)
)
```

**New columns to add**:
```sql
ALTER TABLE user_gold ADD COLUMN last_rob_date TEXT;
ALTER TABLE user_gold ADD COLUMN rob_count INTEGER DEFAULT 0;
```

**Column Specifications**:
- `last_rob_date`: TEXT, nullable, stores 'YYYY-MM-DD' format date string
- `rob_count`: INTEGER, default 0, tracks number of rob attempts today

**Migration Strategy**:
- Use `ALTER TABLE` with `DEFAULT` values for backward compatibility
- Existing records will have `last_rob_date = NULL` and `rob_count = 0`
- First rob attempt will initialize `last_rob_date` to current MYT date

### Data Structures

**Rob Result**:
```python
@dataclass
class RobResult:
    success: bool
    amount: int  # Positive for gain, negative for loss
    message: str
```

**Economic Validation Result**:
```python
@dataclass
class ValidationResult:
    valid: bool
    error_message: str
```

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system-essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*


### Property Reflection

After analyzing the acceptance criteria, I identified the following redundancies:

1. **Properties 1.3, 1.5, and 3.2** all test the same behavior: blocking daily command when already claimed today. These can be combined into one property about daily claim blocking.

2. **Properties 2.3 and 2.4** both test MAX_BET validation. Property 2.4 is redundant as it just describes the rejection behavior that 2.3 already covers.

3. **Properties 1.4, 1.6, and 4.2** all test rob_count reset behavior when the date changes. These can be combined into one property.

4. **Properties 4.9 and 4.11** test Gold balance changes for success and failure cases. These can be combined into a single property about Gold conservation in the rob system.

5. **Properties 4.8 and 4.10** test the range of stolen/penalty amounts. These can be combined into one property about amount calculation bounds.

After consolidation, the unique testable properties are:

- Daily claim blocking when already claimed today (combines 1.3, 1.5, 3.2)
- Daily claim awards 500 Gold when not claimed today (3.3, 3.4)
- Rob count resets on date change (combines 1.4, 1.6, 4.2)
- MAX_BET validation blocks excessive bets (combines 2.3, 2.4)
- MAX_DEBT validation blocks bets that would exceed debt limit (2.5, 2.6)
- Rob command blocks when rob_count >= 3 (4.3)
- Rob command increments rob_count on execution (4.6)
- Rob success rate converges to 40% over many trials (4.7)
- Rob amounts are within specified ranges (combines 4.8, 4.10)
- Rob transfers preserve Gold conservation (combines 4.9, 4.11)

### Property 1: Daily Claim Once Per Day

*For any* user and guild, if a user successfully claims their daily reward, then attempting to claim again on the same MYT date should be blocked with an error message.

**Validates: Requirements 1.3, 1.5, 3.2**

### Property 2: Daily Reward Amount and Persistence

*For any* user and guild, if a user has not claimed their daily reward today (MYT), then executing the daily command should increase their Gold balance by exactly 500 and update their last_daily timestamp to the current MYT date.

**Validates: Requirements 3.3, 3.4**

### Property 3: Rob Count Daily Reset

*For any* user and guild, if the last_rob_date differs from the current MYT date, then the rob_count should be reset to 0 when the rob command is executed.

**Validates: Requirements 1.4, 1.6, 4.2**

### Property 4: MAX_BET Enforcement

*For any* gambling command (/slots, /bj, /ht) and any bet amount, if the bet exceeds MAX_BET (5000), then the system should reject the bet before any Gold is deducted.

**Validates: Requirements 2.3, 2.4**

### Property 5: MAX_DEBT Enforcement

*For any* user in debt (Gold <= 0) attempting to place a bet, if the projected debt (current_gold - int(bet * 1.3)) would be less than MAX_DEBT (-10000), then the system should reject the bet before any Gold is deducted.

**Validates: Requirements 2.5, 2.6**

### Property 6: Rob Daily Limit Enforcement

*For any* user and guild, if a user has already executed the rob command 3 times on the current MYT date, then any additional rob attempts should be blocked with an error message.

**Validates: Requirements 4.3**

### Property 7: Rob Count Increment

*For any* successful rob command execution (passing all validations), the user's rob_count should increase by exactly 1.

**Validates: Requirements 4.6**

### Property 8: Rob Success Rate Convergence

*For any* large number of rob attempts (n >= 100), the proportion of successful robberies should converge to approximately 40% (within a reasonable statistical margin).

**Validates: Requirements 4.7**

### Property 9: Rob Amount Bounds

*For any* rob command execution, if the robbery succeeds, the stolen amount should be between 20% and 30% (inclusive) of the target's Gold balance; if the robbery fails, the penalty should be between 10% and 15% (inclusive) of the robber's Gold balance.

**Validates: Requirements 4.8, 4.10**

### Property 10: Rob Gold Conservation

*For any* rob command execution, the total Gold in the system (robber + target) should remain constant. If robbery succeeds, the robber gains exactly what the target loses; if robbery fails, only the robber's Gold decreases by the penalty amount.

**Validates: Requirements 4.9, 4.11**

## Error Handling

### Input Validation Errors

1. **Invalid Bet Amount**:
   - Condition: Bet <= 0 or bet > MAX_BET
   - Response: Ephemeral error message
   - Example: "❌ Bet exceeds maximum allowed (5,000 💰)"

2. **Insufficient Funds (Debt Limit)**:
   - Condition: Projected debt < MAX_DEBT
   - Response: Ephemeral warning message
   - Example: "🚫 Ah Long credit limit reached! Maximum debt is -10,000 💰. Your projected debt would be -12,000 💰."

3. **Daily Limit Reached**:
   - Condition: Already claimed daily reward or rob_count >= 3
   - Response: Error message with time until reset
   - Example: "❌ You already claimed your daily relief! Wait until midnight (MYT) for the reset."
   - Example: "🛑 Heat is too high! You've already robbed 3 times today. Lay low until midnight."

4. **Invalid Rob Target**:
   - Condition: Target is self or target Gold <= 0
   - Response: Error message
   - Example: "❌ You cannot rob yourself!"
   - Example: "❌ Target is too broke. Even Ah Long is looking for them. Pick someone else!"

### Database Errors

1. **Connection Failures**:
   - Handled by existing `_DB` wrapper class with auto-reconnect
   - Retries once on `psycopg2.OperationalError`
   - Raises exception if retry fails

2. **Schema Migration Errors**:
   - ALTER TABLE statements wrapped in try-except
   - Silently ignore if columns already exist
   - Log errors for debugging

### Discord API Errors

1. **Interaction Timeout**:
   - All commands respond within 3 seconds
   - Use `defer()` for operations that might take longer
   - Fallback to followup messages if needed

2. **Permission Errors**:
   - Check `interaction.guild` before executing guild-specific commands
   - Return ephemeral error if command used in DMs

### Edge Cases

1. **Negative Gold After Rob**:
   - System allows negative Gold (debt)
   - No special handling needed
   - Debt tax applies to future winnings

2. **Target Gold Changes During Rob**:
   - Fetch target Gold at execution time
   - No locking mechanism needed (single-threaded bot)
   - Race conditions unlikely in Discord command context

3. **Timezone Edge Cases**:
   - `get_today_myt()` always uses UTC+8 offset
   - No daylight saving time adjustments (Malaysia doesn't observe DST)
   - Date comparison uses string equality (YYYY-MM-DD format)

4. **First-Time Users**:
   - `last_daily = NULL` treated as "never claimed"
   - `last_rob_date = NULL` treated as "different date" → reset rob_count
   - `rob_count = 0` by default

## Testing Strategy

### Unit Testing

Unit tests will focus on specific examples, edge cases, and integration points:

1. **Timezone Helper Tests**:
   - Test `get_today_myt()` returns correct format (YYYY-MM-DD)
   - Test date calculation with mocked current time
   - Test consistency across multiple calls in same day

2. **Economic Firewall Tests**:
   - Test MAX_BET constant value is 5000
   - Test MAX_DEBT constant value is -10000
   - Test validation function with edge cases (bet = MAX_BET, bet = MAX_BET + 1)
   - Test projected debt calculation with various Gold balances

3. **Database Schema Tests**:
   - Test `last_rob_date` column exists and accepts TEXT
   - Test `rob_count` column exists and defaults to 0
   - Test NULL handling for `last_rob_date`

4. **Command Integration Tests**:
   - Test daily command with specific date scenarios
   - Test rob command with specific rob_count values
   - Test error messages match requirements exactly

5. **Edge Case Tests**:
   - Test robbing self is rejected
   - Test robbing target with Gold <= 0 is rejected
   - Test first-time user behavior (NULL values)

### Property-Based Testing

Property tests will verify universal properties across randomized inputs using **Hypothesis** (Python property-based testing library). Each test will run a minimum of 100 iterations.

**Configuration**:
```python
from hypothesis import given, settings
import hypothesis.strategies as st

@settings(max_examples=100)
@given(...)
def test_property_name(...):
    # Feature: daily-rob-economy-system, Property N: <property text>
    pass
```

**Property Test Suite**:

1. **Property 1: Daily Claim Once Per Day**
   - Tag: `# Feature: daily-rob-economy-system, Property 1: Daily claim blocking`
   - Strategy: Generate random user IDs, guild IDs, and dates
   - Test: Claim daily twice with same date → second claim blocked

2. **Property 2: Daily Reward Amount and Persistence**
   - Tag: `# Feature: daily-rob-economy-system, Property 2: Daily reward amount`
   - Strategy: Generate random user IDs, guild IDs, initial Gold balances
   - Test: Claim daily → Gold increases by 500, last_daily updated

3. **Property 3: Rob Count Daily Reset**
   - Tag: `# Feature: daily-rob-economy-system, Property 3: Rob count reset`
   - Strategy: Generate random user IDs, guild IDs, rob_counts, dates
   - Test: Execute rob with different date → rob_count resets to 0

4. **Property 4: MAX_BET Enforcement**
   - Tag: `# Feature: daily-rob-economy-system, Property 4: MAX_BET enforcement`
   - Strategy: Generate random bet amounts > MAX_BET
   - Test: All bets > MAX_BET are rejected

5. **Property 5: MAX_DEBT Enforcement**
   - Tag: `# Feature: daily-rob-economy-system, Property 5: MAX_DEBT enforcement`
   - Strategy: Generate random negative Gold balances and bet amounts
   - Test: Bets causing projected_debt < MAX_DEBT are rejected

6. **Property 6: Rob Daily Limit Enforcement**
   - Tag: `# Feature: daily-rob-economy-system, Property 6: Rob daily limit`
   - Strategy: Generate random user IDs with rob_count >= 3
   - Test: All rob attempts with rob_count >= 3 are blocked

7. **Property 7: Rob Count Increment**
   - Tag: `# Feature: daily-rob-economy-system, Property 7: Rob count increment`
   - Strategy: Generate random valid rob scenarios
   - Test: rob_count increases by exactly 1 after execution

8. **Property 8: Rob Success Rate Convergence**
   - Tag: `# Feature: daily-rob-economy-system, Property 8: Rob success rate`
   - Strategy: Run 1000 rob simulations with fixed random seed
   - Test: Success rate is 40% ± 5% (statistical tolerance)

9. **Property 9: Rob Amount Bounds**
   - Tag: `# Feature: daily-rob-economy-system, Property 9: Rob amount bounds`
   - Strategy: Generate random target/robber Gold balances
   - Test: Stolen amount in [20%, 30%] of target Gold; penalty in [10%, 15%] of robber Gold

10. **Property 10: Rob Gold Conservation**
    - Tag: `# Feature: daily-rob-economy-system, Property 10: Gold conservation`
    - Strategy: Generate random rob scenarios (success and failure)
    - Test: Total Gold (robber + target) remains constant

### Integration Testing

Integration tests will verify the feature works correctly with existing bot components:

1. **Gambling Command Integration**:
   - Test MAX_BET validation in /slots, /bj, /ht
   - Test MAX_DEBT validation in debt confirmation views
   - Test that existing debt tax (30%) still applies

2. **Database Integration**:
   - Test schema migration on existing database
   - Test backward compatibility with existing records
   - Test PostgreSQL and SQLite compatibility

3. **Discord Integration**:
   - Test slash command registration
   - Test ephemeral vs public messages
   - Test user mention formatting in rob command

### Test Coverage Goals

- Unit tests: 100% coverage of new functions and validation logic
- Property tests: All 10 correctness properties implemented
- Integration tests: All 3 integration points verified
- Edge cases: All 4 edge case scenarios tested

### Testing Tools

- **pytest**: Test runner and assertion framework
- **Hypothesis**: Property-based testing library
- **pytest-asyncio**: Async test support for Discord commands
- **unittest.mock**: Mocking Discord interactions and database calls
- **freezegun**: Time mocking for timezone tests
