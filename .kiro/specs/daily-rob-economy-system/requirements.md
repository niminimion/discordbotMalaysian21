# Requirements Document

## Introduction

This feature implements a daily reward system and PvP robbery mechanic for a Discord casino bot, with global midnight resets synchronized to Malaysia Time (UTC+8) and economic safeguards to prevent excessive debt accumulation.

## Glossary

- **Bot**: The Discord casino bot application built with discord.py
- **User**: A Discord server member interacting with the Bot
- **Gold**: The in-game currency stored in the database (can be negative for debt)
- **MYT**: Malaysia Time (UTC+8 timezone)
- **Daily_Command**: The /daily slash command that awards 500 Gold
- **Rob_Command**: The /rob slash command that enables PvP Gold theft
- **Database**: PostgreSQL database (Supabase) containing user_gold table
- **Ah_Long**: The 30% interest rate applied to net winnings when a User is in debt
- **MAX_BET**: Economic firewall constant set to 5000 Gold
- **MAX_DEBT**: Economic firewall constant set to -10000 Gold
- **Gambling_Commands**: Existing commands (/slots, /bj, /ht) that wager Gold
- **Midnight_Reset**: The global daily reset that occurs at 00:00:00 MYT
- **Rob_Count**: The number of times a User has executed Rob_Command today
- **Target**: The User being robbed in a Rob_Command interaction

## Requirements

### Requirement 1: Global Midnight Reset System

**User Story:** As a developer, I want all daily mechanics to reset at midnight Malaysia Time, so that all users experience consistent daily cycles regardless of when they individually claim rewards.

#### Acceptance Criteria

1. THE Bot SHALL implement a helper function `get_today_myt()` that returns the current date in MYT as a string in 'YYYY-MM-DD' format
2. THE `get_today_myt()` function SHALL use datetime, timezone, and timedelta imports from Python standard library
3. WHEN Daily_Command is executed, THE Bot SHALL compare `last_daily` database value against `get_today_myt()` output
4. WHEN Rob_Command is executed, THE Bot SHALL compare `last_rob_date` database value against `get_today_myt()` output
5. WHEN `last_daily` equals `get_today_myt()`, THE Bot SHALL block Daily_Command execution
6. WHEN `last_rob_date` differs from `get_today_myt()`, THE Bot SHALL reset Rob_Count to 0

### Requirement 2: Economic Firewall System

**User Story:** As a developer, I want to prevent users from accumulating unlimited debt, so that the economy remains balanced and debt mechanics stay meaningful.

#### Acceptance Criteria

1. THE Bot SHALL define MAX_BET constant with value 5000
2. THE Bot SHALL define MAX_DEBT constant with value -10000
3. WHEN a User attempts to place a bet in Gambling_Commands, THE Bot SHALL validate that the bet amount does not exceed MAX_BET
4. IF a bet amount exceeds MAX_BET, THEN THE Bot SHALL reject the transaction with an ephemeral error message
5. WHEN a User in debt attempts to place a bet, THE Bot SHALL calculate projected debt as `current_gold - int(bet * 1.3)`
6. IF projected debt is less than MAX_DEBT, THEN THE Bot SHALL block the transaction with an ephemeral warning about maxed Ah_Long credit limit
7. THE Bot SHALL apply this validation to existing Gambling_Commands (/slots, /bj) and debt confirmation views

### Requirement 3: Daily Reward Command with MYT Reset

**User Story:** As a user, I want to claim 500 Gold once per day at midnight Malaysia Time, so that I can recover from losses and continue playing.

#### Acceptance Criteria

1. WHEN a User executes Daily_Command, THE Bot SHALL fetch `last_daily` value from Database for that User and guild
2. IF `last_daily` equals `get_today_myt()`, THEN THE Bot SHALL send message "❌ You already claimed your daily relief! Wait until midnight (MYT) for the reset."
3. IF `last_daily` does not equal `get_today_myt()`, THEN THE Bot SHALL add 500 Gold to the User's balance
4. WHEN Daily_Command succeeds, THE Bot SHALL update `last_daily` to `get_today_myt()` in Database
5. WHEN Daily_Command succeeds, THE Bot SHALL send message "✅ Here is your daily 500 gold. Don't lose it all at once!"

### Requirement 4: PvP Robbery Command with Daily Limits

**User Story:** As a user, I want to rob other players to steal their Gold, so that I can engage in competitive PvP gameplay with risk and reward.

#### Acceptance Criteria

1. WHEN a User executes Rob_Command, THE Bot SHALL fetch `last_rob_date` and Rob_Count from Database
2. IF `last_rob_date` does not equal `get_today_myt()`, THEN THE Bot SHALL reset Rob_Count to 0 and update `last_rob_date` to `get_today_myt()`
3. IF Rob_Count is greater than or equal to 3, THEN THE Bot SHALL block execution with message "🛑 Heat is too high! You've already robbed 3 times today. Lay low until midnight."
4. IF a User attempts to rob themselves, THEN THE Bot SHALL reject the command with an error message
5. IF Target has Gold less than or equal to 0, THEN THE Bot SHALL reject with message "❌ Target is too broke. Even Ah Long is looking for them. Pick someone else!"
6. WHEN Rob_Command validation passes, THE Bot SHALL increment Rob_Count by 1 and save to Database
7. WHEN Rob_Command executes, THE Bot SHALL generate a random number to determine success with 40% probability
8. IF robbery succeeds, THEN THE Bot SHALL calculate stolen amount as random percentage between 20% and 30% of Target's Gold
9. IF robbery succeeds, THEN THE Bot SHALL transfer the stolen amount from Target to User
10. IF robbery fails, THEN THE Bot SHALL calculate penalty as random percentage between 10% and 15% of User's Gold
11. IF robbery fails, THEN THE Bot SHALL deduct the penalty from User's Gold balance
12. WHEN Rob_Command completes, THE Bot SHALL display results with appropriate emojis and messages showing outcome and balance changes

### Requirement 5: Database Schema Updates

**User Story:** As a developer, I want to extend the user_gold table to support robbery tracking, so that the system can enforce daily limits and reset mechanics.

#### Acceptance Criteria

1. THE Database SHALL add column `last_rob_date` with TEXT data type to user_gold table
2. THE Database SHALL add column `rob_count` with INTEGER data type to user_gold table
3. THE Database SHALL allow NULL values for `last_rob_date` for existing records
4. THE Database SHALL default `rob_count` to 0 for new records
