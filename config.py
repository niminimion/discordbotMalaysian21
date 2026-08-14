import datetime
from datetime import timezone, timedelta

DAILY_REWARD:        int   = 500
DEBT_TAX_RATE:       float = 0.30
FREE_PLAY_TOKEN_BET: int   = 1
MAX_BET:             int   = 5000
MAX_DEBT:            int   = -10000

DEBT_WARNING = (
    "⚠️ You are gambling on borrowed Gold! "
    "If you win, **30% interest** will be deducted from your profits! "
    "Run out of Gold? Use `/daily` to claim your daily relief fund!"
)


def get_today_myt() -> str:
    """
    Return the current date in Malaysia Time (UTC+8) as 'YYYY-MM-DD' string.
    """
    myt = timezone(timedelta(hours=8))
    now_myt = datetime.datetime.now(myt)
    return now_myt.strftime('%Y-%m-%d')


def validate_bet_with_firewall(guild_id: int, user_id: int, bet: int) -> tuple[bool, str]:
    """
    Validate bet against economic firewall rules.

    Returns (is_valid, error_message).
    If is_valid is True, error_message is empty.
    """
    from database import get_gold

    if bet > MAX_BET:
        return False, f"❌ Bet exceeds maximum allowed ({MAX_BET:,} 💰)"

    current_gold = get_gold(guild_id, user_id)
    if current_gold <= 0:
        projected_debt = current_gold - int(bet * 1.3)
        if projected_debt < MAX_DEBT:
            return False, f"🚫 Ah Long credit limit reached! Maximum debt is {MAX_DEBT:,} 💰. Your projected debt would be {projected_debt:,} 💰."

    return True, ""
