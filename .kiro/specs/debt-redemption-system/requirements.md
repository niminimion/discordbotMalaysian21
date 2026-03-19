# Requirements Document

## Introduction

The Debt Redemption System provides economic safeguards and recovery mechanics for players who have accumulated negative gold balances in the Discord casino bot. This feature introduces hard limits to prevent excessive debt accumulation, three redemption commands that allow players to work their way out of debt through labor and luck-based mechanics, a global midnight reset system based on Malaysia Time (UTC+8), and a PvP robbery mechanic.

## Glossary

- **System**: The Discord casino bot economy system
- **Player**: A Discord user participating in the casino economy
- **Gold**: The primary in-game currency (can be negative, representing debt)
- **Ah_Long**: The loan shark entity that charges 30% interest on debt
- **MAX_BET**: Hard limit of 5000 gold per single bet
- **MAX_DEBT**: Hard limit of -10000 gold (maximum debt allowed)
- **Debt_State**: Condition where a player's gold balance is less than 0
- **Gambling_Command**: Any command that accepts a bet parameter (/slots, /bj)
- **Debt_Confirmation_View**: The HTDebtConfirmView UI component that confirms risky bets
- **Redemption_Command**: Commands specifically designed for debt recovery (/work, /scratch, /yolo)
- **MYT**: Malaysia Time timezone (UTC+8 / GMT+8)
- **MYT_Reset**: Global daily reset that occurs at midnight Malaysia Time
- **Daily_Mechanic**: Any command or feature that resets at midnight MYT (/daily, /scratch, /yolo, /rob)
- **Rob_Count**: Number of times a player has used /rob today (resets at midnight MYT)

## Requirements

### Requirement 1: Economic Firewall - Maximum Bet Limit

**User Story:** As a player, I want to be prevented from making excessively large bets, so that I cannot accidentally bankrupt myself in a single transaction.

#### Acceptance Criteria

1. THE System SHALL define MAX_BET as 5000 gold
2. WHEN a Player attempts to place a bet greater than MAX_BET in /slots, THE System SHALL block the transaction
3. WHEN a Player attempts to place a bet greater than MAX_BET in /bj, THE System SHALL block the transaction
4. WHEN a bet is blocked due to MAX_BET violation, THE System SHALL send an ephemeral error message to the Player
5. THE error message SHALL inform the Player that the maximum bet is 5000 gold

### Requirement 2: Economic Firewall - Maximum Debt Limit

**User Story:** As a player, I want to be prevented from going into infinite debt, so that I have a realistic path to recovery.

#### Acceptance Criteria

1. THE System SHALL define MAX_DEBT as -10000 gold
2. WHEN a Player with Debt_State attempts a bet, THE System SHALL calculate the potential new balance as current_gold minus the bet amount multiplied by 1.3
3. IF the calculated potential balance is less than MAX_DEBT, THEN THE System SHALL block the transaction
4. WHEN a bet is blocked due to MAX_DEBT violation, THE System SHALL send an ephemeral warning message to the Player
5. THE warning message SHALL inform the Player that their Ah Long credit limit is maxed out and they need to go wash dishes
6. THE System SHALL apply MAX_DEBT validation to /slots command
7. THE System SHALL apply MAX_DEBT validation to /bj command
8. THE System SHALL apply MAX_DEBT validation to Debt_Confirmation_View

### Requirement 3: Cyber Dishwashing Work Command

**User Story:** As a player in debt, I want to earn a small amount of gold through work, so that I can slowly recover from negative balance.

#### Acceptance Criteria

1. THE System SHALL provide a /work slash command
2. THE /work command SHALL NOT have individual cooldowns
3. WHEN a Player executes /work, THE System SHALL add 50 gold to the Player's balance
4. WHEN a Player executes /work, THE System SHALL update the database with the new balance using parameterized SQL queries
5. THE System SHALL generate a random number between 1 and 100 for each /work execution
6. IF the random number equals 1, THEN THE System SHALL trigger the Rolex event
7. WHEN the Rolex event triggers AND the Player has Debt_State, THE System SHALL set the Player's gold to 0
8. WHEN the Rolex event triggers, THE System SHALL send the message "✨ Holy cow! You found a discarded Rolex in the restaurant drain! Your Ah Long debt is completely wiped out!"
9. WHEN the Rolex event does not trigger, THE System SHALL send the message "🍽️ You washed dishes in the back kitchen for an hour and earned 50 gold."
10. THE /work command SHALL verify the interaction is used in a guild context
11. IF /work is used outside a guild, THEN THE System SHALL send an ephemeral error message

### Requirement 4: Bankrupt Lottery Scratch Card Command

**User Story:** As a player in debt, I want a daily chance to win debt relief through a scratch card lottery, so that I have hope of a lucky break.

#### Acceptance Criteria

1. THE System SHALL provide a /scratch slash command
2. THE /scratch command SHALL reset at midnight MYT (not individual 24-hour cooldowns)
3. WHEN a Player with gold greater than or equal to 0 attempts /scratch, THE System SHALL reject the command
4. WHEN /scratch is rejected due to positive balance, THE System SHALL send the message "❌ Only broke people in debt can get a free scratch card!"
5. WHEN a Player with Debt_State executes /scratch, THE System SHALL generate a random number between 1 and 100
6. IF the random number equals 1, THEN THE System SHALL trigger the jackpot event
7. WHEN the jackpot event triggers, THE System SHALL set the Player's gold to 0
8. WHEN the jackpot event triggers, THE System SHALL send the message "🎉 Jackpot! You won the Ah Long Debt Relief Grand Prize! All debts are cleared!"
9. WHEN the jackpot event does not trigger, THE System SHALL send the message "🗑️ Better luck next time. The scratch card says: Go back to washing dishes and pay what you owe!"
10. THE /scratch command SHALL verify the interaction is used in a guild context
11. IF /scratch is used outside a guild, THEN THE System SHALL send an ephemeral error message
12. THE /scratch command SHALL use MYT_Reset logic to determine if the player has already used it today

### Requirement 5: Russian Roulette YOLO Command

**User Story:** As a desperate player in deep debt, I want a high-risk high-reward option to either escape debt or sink deeper, so that I can gamble on a dramatic recovery.

#### Acceptance Criteria

1. THE System SHALL provide a /yolo slash command
2. THE /yolo command SHALL reset at midnight MYT (not individual 24-hour cooldowns)
3. WHEN a Player with gold greater than -1000 attempts /yolo, THE System SHALL reject the command
4. WHEN /yolo is rejected due to insufficient debt, THE System SHALL send an ephemeral message indicating only desperate players can use this command
5. WHEN a Player with gold less than or equal to -1000 executes /yolo, THE System SHALL generate a random number between 1 and 100
6. IF the random number is less than or equal to 10, THEN THE System SHALL trigger the win event
7. WHEN the win event triggers, THE System SHALL set the Player's gold to 2000
8. WHEN the win event triggers, THE System SHALL send the message "🔫 *Click*. The gun is empty! Ah Long respects your guts. He clears your debt and gives you 2000 gold to start over!"
9. IF the random number is greater than 10, THEN THE System SHALL trigger the lose event
10. WHEN the lose event triggers, THE System SHALL multiply the Player's current gold by 1.5
11. WHEN the lose event triggers, THE System SHALL send the message "💥 *Bang*! You lost! Ah Long beats you up and adds the medical bills to your tab. Your debt increases by 50%!"
12. THE /yolo command SHALL verify the interaction is used in a guild context
13. IF /yolo is used outside a guild, THEN THE System SHALL send an ephemeral error message
14. THE /yolo command SHALL use MYT_Reset logic to determine if the player has already used it today

### Requirement 6: Database Security and Code Quality

**User Story:** As a system administrator, I want all database operations to be secure and follow best practices, so that the system is protected from SQL injection and maintains code consistency.

#### Acceptance Criteria

1. THE System SHALL use parameterized SQL queries for all database updates in Redemption_Commands
2. THE System SHALL use parameterized SQL queries for all database updates in economic firewall validations
3. THE System SHALL follow existing code patterns and style from the bot codebase
4. THE System SHALL send error messages as ephemeral when appropriate
5. THE System SHALL verify guild context for all new commands before execution

### Requirement 7: Global Midnight Reset Logic (MYT Timezone)

**User Story:** As a player, I want all daily mechanics to reset at the same time globally (midnight Malaysia Time), so that the system is fair and predictable for all players.

#### Acceptance Criteria

1. THE System SHALL import datetime, timezone, and timedelta modules
2. THE System SHALL define a MYT timezone object as timezone(timedelta(hours=8))
3. THE System SHALL provide a helper function get_today_myt() that returns the current date in Malaysia Time
4. THE get_today_myt() function SHALL return a string formatted as 'YYYY-MM-DD'
5. THE System SHALL use get_today_myt() for all daily reset checks
6. THE System SHALL apply MYT_Reset logic to /daily, /scratch, /yolo, and /rob commands

### Requirement 8: Updated Daily Command with MYT Reset

**User Story:** As a player, I want to claim my daily gold reward once per day at midnight MYT, so that I can recover from losses.

#### Acceptance Criteria

1. THE System SHALL update the /daily command to use MYT_Reset logic
2. THE System SHALL change the daily reward from 300 gold to 500 gold
3. WHEN a Player executes /daily, THE System SHALL fetch the last_daily column from user_gold table
4. IF last_daily equals get_today_myt(), THE System SHALL block the command
5. WHEN /daily is blocked, THE System SHALL send the message "❌ You already claimed your daily relief! Wait until midnight (MYT) for the reset."
6. IF last_daily does not equal get_today_myt(), THE System SHALL give the Player 500 gold
7. THE System SHALL update last_daily to get_today_myt() in the database using parameterized queries
8. WHEN /daily succeeds, THE System SHALL send the message "✅ Here is your daily 500 gold. Don't lose it all at once!"
9. THE System SHALL display the new balance after claiming daily gold

### Requirement 9: PvP Robbery Command

**User Story:** As a player, I want to rob other players to gain gold, with a risk of getting caught by police, so that I have an interactive PvP mechanic.

#### Acceptance Criteria

1. THE System SHALL provide a /rob slash command with a target parameter
2. THE user_gold table SHALL have columns last_rob_date (TEXT) and rob_count (INT)
3. WHEN a Player executes /rob, THE System SHALL fetch last_rob_date and rob_count from the database
4. IF last_rob_date does not equal get_today_myt(), THE System SHALL reset rob_count to 0 and update last_rob_date to get_today_myt()
5. IF rob_count is greater than or equal to 3, THE System SHALL block the command
6. WHEN /rob is blocked due to rob_count, THE System SHALL send the message "🛑 Heat is too high! You've already robbed 3 times today. Lay low until midnight."
7. THE System SHALL validate that the robber is not targeting themselves
8. IF the robber targets themselves, THE System SHALL send an ephemeral error message
9. THE System SHALL validate that the target has gold greater than 0
10. IF the target has gold less than or equal to 0, THE System SHALL send the message "❌ Target is too broke. Even Ah Long is looking for them. Pick someone else!"
11. WHEN validation passes, THE System SHALL increment rob_count by 1 and save to database
12. THE System SHALL generate a random number between 0.0 and 1.0 for success determination
13. IF the random number is less than 0.40, THE System SHALL trigger the success event (40% chance)
14. WHEN the success event triggers, THE System SHALL calculate steal_amount as random.uniform(0.1, 0.2) multiplied by target's gold, converted to int
15. WHEN the success event triggers, THE System SHALL subtract steal_amount from target's gold
16. WHEN the success event triggers, THE System SHALL add steal_amount to robber's gold
17. WHEN the success event triggers, THE System SHALL send the message "🥷 **Heist Successful!** You sneaked up on {target} and stole **{steal_amount}** gold!"
18. IF the random number is greater than or equal to 0.40, THE System SHALL trigger the caught event (60% chance)
19. WHEN the caught event triggers AND robber has gold greater than 0, THE System SHALL set robber's gold to 0
20. WHEN the caught event triggers AND robber has gold greater than 0, THE System SHALL send the message "🚨 **WEE WOO!** PDRM caught you red-handed! You paid all your remaining **{current_gold}** gold for bail!"
21. WHEN the caught event triggers AND robber has gold less than or equal to 0, THE System SHALL multiply robber's gold by 1.5
22. WHEN the caught event triggers AND robber has gold less than or equal to 0, THE System SHALL send the message "🚨 **BUSTED!** You got arrested! Ah Long took advantage and added a 50% penalty to your debt. Current tragic debt: **{new_gold}**"
23. THE /rob command SHALL verify the interaction is used in a guild context
24. IF /rob is used outside a guild, THEN THE System SHALL send an ephemeral error message
25. THE System SHALL use parameterized SQL queries for all database operations in /rob command

### Requirement 10: Documentation Update

**User Story:** As a bot user or developer, I want the README to reflect the new features, so that I understand how to use the debt redemption system.

#### Acceptance Criteria

1. THE System documentation SHALL include a section describing the economic firewalls
2. THE System documentation SHALL include a section describing the MYT timezone reset system
3. THE System documentation SHALL include a section describing the updated /daily command (500 gold, MYT reset)
4. THE System documentation SHALL include a section describing the /work command with mechanics
5. THE System documentation SHALL include a section describing the /scratch command with eligibility requirements and MYT reset
6. THE System documentation SHALL include a section describing the /yolo command with risk/reward details and MYT reset
7. THE System documentation SHALL include a section describing the /rob command with mechanics, limits, and MYT reset
8. THE System documentation SHALL include the probability percentages for special events in each command
