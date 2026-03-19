# Implementation Plan: Daily Rob Economy System

## Overview

This implementation plan breaks down the daily reward system and PvP robbery mechanic into discrete coding tasks. The plan follows an incremental approach: database schema first, then core utilities, then command handlers, and finally integration with existing gambling commands. Each task builds on previous work and includes references to specific requirements.

## Tasks

- [x] 1. Database schema migration and helper functions
  - [x] 1.1 Add new columns to user_gold table
    - Execute ALTER TABLE statements to add `last_rob_date` (TEXT) and `rob_count` (INTEGER DEFAULT 0)
    - Handle migration gracefully for existing records (NULL values allowed)
    - Test schema changes work with both PostgreSQL (Supabase) and SQLite
    - _Requirements: 5.1, 5.2, 5.3, 5.4_
  
  - [x] 1.2 Implement database helper functions for robbery tracking
    - Write `get_rob_data(guild_id, user_id)` to fetch (last_rob_date, rob_count)
    - Write `update_rob_data(guild_id, user_id, date, count)` to update robbery tracking fields
    - Write `reset_rob_count_if_new_day(guild_id, user_id)` to handle date-based resets
    - Handle NULL values for first-time users
    - _Requirements: 4.1, 4.2, 4.6_
  
  - [x] 1.3 Write unit tests for database helper functions
    - Test NULL handling for first-time users
    - Test rob_count reset logic when date changes
    - Test data persistence across multiple calls
    - _Requirements: 5.1, 5.2, 5.3, 5.4_

- [x] 2. Timezone helper and economic firewall constants
  - [x] 2.1 Implement get_today_myt() timezone helper function
    - Use datetime, timezone, and timedelta from Python standard library
    - Return current date in MYT (UTC+8) as 'YYYY-MM-DD' string
    - Ensure function is pure with no side effects
    - _Requirements: 1.1, 1.2_
  
  - [x] 2.2 Write property test for get_today_myt()
    - **Property: Date format consistency**
    - **Validates: Requirements 1.1**
    - Test output format matches 'YYYY-MM-DD' pattern
    - Test consistency across multiple calls in same day
  
  - [x] 2.3 Define economic firewall constants
    - Define MAX_BET = 5000 constant
    - Define MAX_DEBT = -10000 constant
    - Add constants to appropriate module location
    - _Requirements: 2.1, 2.2_
  
  - [x] 2.4 Implement validate_bet_with_firewall() function
    - Check bet amount against MAX_BET
    - Calculate projected debt for users with Gold <= 0
    - Check projected debt against MAX_DEBT
    - Return (is_valid, error_message) tuple
    - _Requirements: 2.3, 2.4, 2.5, 2.6_
  
  - [x] 2.5 Write property test for MAX_BET enforcement
    - **Property 4: MAX_BET Enforcement**
    - **Validates: Requirements 2.3, 2.4**
    - Generate random bet amounts > MAX_BET
    - Verify all bets > MAX_BET are rejected
  
  - [x] 2.6 Write property test for MAX_DEBT enforcement
    - **Property 5: MAX_DEBT Enforcement**
    - **Validates: Requirements 2.5, 2.6**
    - Generate random negative Gold balances and bet amounts
    - Verify bets causing projected_debt < MAX_DEBT are rejected

- [x] 3. Checkpoint - Verify core utilities
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement /daily command with MYT reset
  - [x] 4.1 Create daily command handler
    - Register slash command with discord.py tree
    - Fetch user's last_daily value from database
    - Compare last_daily with get_today_myt() output
    - Award 500 Gold if dates differ
    - Update last_daily to current MYT date on success
    - Send appropriate success/error messages
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_
  
  - [x] 4.2 Write property test for daily claim blocking
    - **Property 1: Daily Claim Once Per Day**
    - **Validates: Requirements 1.3, 1.5, 3.2**
    - Generate random user IDs, guild IDs, and dates
    - Test claiming daily twice with same date blocks second claim
  
  - [x] 4.3 Write property test for daily reward amount
    - **Property 2: Daily Reward Amount and Persistence**
    - **Validates: Requirements 3.3, 3.4**
    - Generate random user IDs, guild IDs, initial Gold balances
    - Verify Gold increases by exactly 500 and last_daily updates
  
  - [x] 4.4 Write unit tests for daily command edge cases
    - Test first-time user (last_daily = NULL)
    - Test exact message text matches requirements
    - Test database update on successful claim

- [x] 5. Implement /rob command with daily limits
  - [x] 5.1 Create rob command handler with validation logic
    - Register slash command with target parameter
    - Fetch last_rob_date and rob_count from database
    - Reset rob_count if last_rob_date differs from get_today_myt()
    - Validate rob_count < 3
    - Validate target is not self
    - Validate target Gold > 0
    - Send appropriate error messages for validation failures
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5_
  
  - [x] 5.2 Implement rob execution logic
    - Increment rob_count by 1 after validation passes
    - Generate random number for 40% success probability
    - Calculate stolen amount (20-30% of target Gold) on success
    - Calculate penalty (10-15% of robber Gold) on failure
    - Update Gold balances for both users
    - Update last_rob_date and rob_count in database
    - Send result message with emojis and balance changes
    - _Requirements: 4.6, 4.7, 4.8, 4.9, 4.10, 4.11, 4.12_
  
  - [x] 5.3 Write property test for rob count reset
    - **Property 3: Rob Count Daily Reset**
    - **Validates: Requirements 1.4, 1.6, 4.2**
    - Generate random user IDs, guild IDs, rob_counts, dates
    - Verify rob_count resets to 0 when date changes
  
  - [x] 5.4 Write property test for rob daily limit
    - **Property 6: Rob Daily Limit Enforcement**
    - **Validates: Requirements 4.3**
    - Generate random user IDs with rob_count >= 3
    - Verify all rob attempts are blocked
  
  - [~] 5.5 Write property test for rob count increment
    - **Property 7: Rob Count Increment**
    - **Validates: Requirements 4.6**
    - Generate random valid rob scenarios
    - Verify rob_count increases by exactly 1
  
  - [~] 5.6 Write property test for rob success rate
    - **Property 8: Rob Success Rate Convergence**
    - **Validates: Requirements 4.7**
    - Run 1000 rob simulations with fixed random seed
    - Verify success rate is 40% ± 5%
  
  - [~] 5.7 Write property test for rob amount bounds
    - **Property 9: Rob Amount Bounds**
    - **Validates: Requirements 4.8, 4.10**
    - Generate random target/robber Gold balances
    - Verify stolen amount in [20%, 30%] and penalty in [10%, 15%]
  
  - [~] 5.8 Write property test for Gold conservation
    - **Property 10: Rob Gold Conservation**
    - **Validates: Requirements 4.9, 4.11**
    - Generate random rob scenarios (success and failure)
    - Verify total Gold (robber + target) remains constant
  
  - [~] 5.9 Write unit tests for rob command edge cases
    - Test robbing self is rejected
    - Test robbing target with Gold <= 0 is rejected
    - Test exact message text matches requirements
    - Test first-time user (last_rob_date = NULL)

- [x] 6. Checkpoint - Verify new commands work correctly
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Integrate economic firewall with existing gambling commands
  - [x] 7.1 Add MAX_BET and MAX_DEBT validation to /slots command
    - Call validate_bet_with_firewall() before bet execution
    - Send ephemeral error message if validation fails
    - Ensure existing debt tax (30%) still applies
    - _Requirements: 2.3, 2.4, 2.5, 2.6_
  
  - [x] 7.2 Add MAX_BET and MAX_DEBT validation to /bj command
    - Add validation in LobbyView before game starts
    - Call validate_bet_with_firewall() before bet execution
    - Send ephemeral error message if validation fails
    - Ensure existing debt tax (30%) still applies
    - _Requirements: 2.3, 2.4, 2.5, 2.6_
  
  - [x] 7.3 Add MAX_BET and MAX_DEBT validation to /ht command
    - Call validate_bet_with_firewall() before coin flip
    - Send ephemeral error message if validation fails
    - Ensure existing debt tax (30%) still applies
    - _Requirements: 2.3, 2.4, 2.5, 2.6_
  
  - [x] 7.4 Add MAX_DEBT validation to debt confirmation views
    - Update HTDebtConfirmView.confirm() with MAX_DEBT check
    - Update BjDebtConfirmView.confirm() with MAX_DEBT check
    - Update SlotsDebtConfirmView.confirm() with MAX_DEBT check
    - Send ephemeral warning message if projected debt < MAX_DEBT
    - _Requirements: 2.5, 2.6_
  
  - [~] 7.5 Write integration tests for gambling command firewall
    - Test MAX_BET validation in /slots, /bj, /ht
    - Test MAX_DEBT validation in debt confirmation views
    - Test that existing debt tax (30%) still applies correctly
    - _Requirements: 2.3, 2.4, 2.5, 2.6_

- [x] 8. Final checkpoint and verification
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Property tests use Hypothesis library with minimum 100 iterations
- Integration tests verify compatibility with existing bot components
- Database migration handles backward compatibility with NULL values
- All commands use ephemeral messages for errors to reduce chat clutter
