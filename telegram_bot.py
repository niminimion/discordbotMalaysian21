import asyncio
import os
import random
import time
from typing import Dict, Tuple, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Reuse economy/database helpers from the existing bot implementation.
# Importing main.py does NOT start the Discord bot unless run as __main__.
from main import (
    DAILY_REWARD,
    MAX_DEBT,
    get_today_myt,
    get_gold,
    add_gold,
    set_gold,
    get_last_daily,
    set_last_daily,
    reset_rob_count_if_new_day,
    update_rob_data,
)


COOLDOWNS = {
    "work": 10 * 60,  # 10 min
    "scratch": 24 * 60 * 60,  # 24h
    "yolo": 24 * 60 * 60,  # 24h
}


_cooldown_lock = asyncio.Lock()
_cooldown_until: Dict[Tuple[int, int, str], float] = {}


async def _check_cooldown(chat_id: int, user_id: int, cmd: str) -> Optional[int]:
    """
    Returns remaining seconds if still on cooldown, otherwise None.
    Uses in-memory cooldown (good enough for MVP; does not persist across restarts).
    """
    cooldown_s = COOLDOWNS.get(cmd)
    if not cooldown_s:
        return None

    key = (chat_id, user_id, cmd)
    now = time.time()
    async with _cooldown_lock:
        until = _cooldown_until.get(key)
        if until is None:
            _cooldown_until[key] = now + cooldown_s
            return None
        if now < until:
            return int(until - now)
        _cooldown_until[key] = now + cooldown_s
        return None


def _parse_int_arg(args) -> Optional[int]:
    if not args:
        return None
    try:
        return int(args[0])
    except Exception:
        return None


async def daily(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return

    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    today = get_today_myt()
    last_str = get_last_daily(chat_id, user_id)

    if last_str == today:
        await update.message.reply_text(
            "❌ You already claimed your daily relief! Wait until midnight (MYT) for the reset."
        )
        return

    add_gold(chat_id, user_id, DAILY_REWARD)
    set_last_daily(chat_id, user_id, today)
    await update.message.reply_text("✅ Here is your daily 500 gold. Don't lose it all at once!")


async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return
    chat_id = update.effective_chat.id
    uid = _parse_int_arg(context.args) or update.effective_user.id
    gold = get_gold(chat_id, uid)
    await update.message.reply_text(f"💰 Balance: {gold:,} gold")


async def work(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    remaining = await _check_cooldown(chat_id, user_id, "work")
    if remaining is not None:
        await update.message.reply_text(f"⏳ On cooldown. Try again in {remaining}s.")
        return

    gold_before = get_gold(chat_id, user_id)
    # 1% Rolex event; only if currently in debt (gold < 0).
    if gold_before < 0 and random.randint(1, 100) == 1:
        set_gold(chat_id, user_id, 0)
        await update.message.reply_text(
            "✨ Holy cow! You found a discarded Rolex in the drain! Your Ah Long debt is completely wiped out!"
        )
        return

    add_gold(chat_id, user_id, 50)
    await update.message.reply_text("🍽️ You washed dishes in the back kitchen for an hour and earned 50 gold.")


async def scratch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    remaining = await _check_cooldown(chat_id, user_id, "scratch")
    if remaining is not None:
        await update.message.reply_text(f"⏳ On cooldown. Try again in {remaining}s.")
        return

    gold_before = get_gold(chat_id, user_id)
    if gold_before >= 0:
        await update.message.reply_text("❌ Only broke people in debt can get a free scratch card!")
        return

    # 1% jackpot
    if random.randint(1, 100) == 1:
        set_gold(chat_id, user_id, 0)
        await update.message.reply_text(
            "🎉 Jackpot! You won the Ah Long Debt Relief Grand Prize! All debts are cleared!"
        )
        return

    await update.message.reply_text("🗑️ Better luck next time. The scratch card says: Go back to washing dishes!")


async def yolo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    remaining = await _check_cooldown(chat_id, user_id, "yolo")
    if remaining is not None:
        await update.message.reply_text(f"⏳ On cooldown. Try again in {remaining}s.")
        return

    gold_before = get_gold(chat_id, user_id)
    if gold_before >= -1000:
        await update.message.reply_text("❌ Only desperate players with gold below -1000 can use /yolo.")
        return

    # Win (10% chance)
    if random.randint(1, 100) <= 10:
        set_gold(chat_id, user_id, 500)
        await update.message.reply_text(
            "🔫 *Click*. The gun is empty! Ah Long respects your guts. He clears your debt and gives you 500 gold to start over!"
        )
        return

    # Lose: multiply current gold by 1.5 (debt worsens)
    new_gold = int(gold_before * 1.5)
    set_gold(chat_id, user_id, new_gold)
    await update.message.reply_text("💥 *Bang*! You lost! Ah Long beats you up and adds the medical bills to your tab. Your debt increases by 50%!")


async def rob(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_chat is None or update.effective_user is None:
        return
    chat_id = update.effective_chat.id
    robber_id = update.effective_user.id

    target_id = _parse_int_arg(context.args)
    if target_id is None:
        await update.message.reply_text("Usage: /rob <target_telegram_user_id>")
        return

    if robber_id == target_id:
        await update.message.reply_text("❌ You cannot rob yourself!")
        return

    today = get_today_myt()

    # reset rob_count if new day
    rob_count = reset_rob_count_if_new_day(chat_id, robber_id, today)
    if rob_count >= 3:
        await update.message.reply_text("🛑 Heat is too high! You've already robbed 3 times today. Lay low until midnight.")
        return

    target_gold = get_gold(chat_id, target_id)
    if target_gold <= 0:
        await update.message.reply_text("❌ Target is too broke. Even Ah Long is looking for them. Pick someone else!")
        return

    # increment count first (matches your Discord logic)
    update_rob_data(chat_id, robber_id, today, rob_count + 1)

    success = random.random() < 0.40
    robber_gold = get_gold(chat_id, robber_id)

    if success:
        steal_percentage = random.uniform(0.20, 0.30)
        stolen_amount = int(target_gold * steal_percentage)
        add_gold(chat_id, target_id, -stolen_amount)
        add_gold(chat_id, robber_id, stolen_amount)
        new_robber_gold = get_gold(chat_id, robber_id)
        await update.message.reply_text(f"💰 Heist Successful! You stole {stolen_amount:,} gold.\nYour balance: {robber_gold:,} -> {new_robber_gold:,}.")
    else:
        penalty_percentage = random.uniform(0.10, 0.15)
        penalty_amount = int(abs(robber_gold) * penalty_percentage)
        add_gold(chat_id, robber_id, -penalty_amount)
        new_robber_gold = get_gold(chat_id, robber_id)
        await update.message.reply_text(
            f"🚨 Heist Failed! You got caught.\nPenalty: -{penalty_amount:,} gold.\nYour balance: {robber_gold:,} -> {new_robber_gold:,}."
        )


def main() -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token or token == "your_token_here":
        raise RuntimeError("TELEGRAM_TOKEN not set. Put it into env (or add .env support).")

    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("daily", daily))
    application.add_handler(CommandHandler("balance", balance))
    application.add_handler(CommandHandler("work", work))
    application.add_handler(CommandHandler("scratch", scratch))
    application.add_handler(CommandHandler("yolo", yolo))
    application.add_handler(CommandHandler("rob", rob))

    application.run_polling()


if __name__ == "__main__":
    main()

