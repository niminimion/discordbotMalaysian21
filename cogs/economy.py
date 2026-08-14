import asyncio
import random
import discord
from discord.ext import commands
from discord import app_commands
from config import DAILY_REWARD, get_today_myt, validate_bet_with_firewall
from database import (db, get_gold, add_gold, set_gold, get_last_daily, set_last_daily,
                      get_rob_data, update_rob_data, reset_rob_count_if_new_day)


class EconomyCog(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="daily", description="Claim 500 Gold once per day (resets at midnight MYT)")
    async def cmd_daily(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        uid      = interaction.user.id
        today    = get_today_myt()
        last_str = get_last_daily(guild_id, uid)

        if last_str == today:
            await interaction.response.send_message(
                "❌ You already claimed your daily relief! Wait until midnight (MYT) for the reset.",
                ephemeral=True,
            )
            return

        new_bal = add_gold(guild_id, uid, DAILY_REWARD)
        set_last_daily(guild_id, uid, today)

        await interaction.response.send_message(
            "✅ Here is your daily 500 gold. Don't lose it all at once!"
        )

    @app_commands.command(name="work", description="Wash dishes to earn 50 Gold (10 min cooldown)")
    @app_commands.checks.cooldown(1, 10 * 60)
    async def cmd_work(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        uid = interaction.user.id
        gold_before = get_gold(guild_id, uid)

        if random.randint(1, 100) == 1 and gold_before < 0:
            set_gold(guild_id, uid, 0)
            await interaction.response.send_message(
                "✨ Holy cow! You found a discarded Rolex in the drain! Your Ah Long debt is completely wiped out!"
            )
            return

        add_gold(guild_id, uid, 50)
        await interaction.response.send_message(
            "🍽️ You washed dishes in the back kitchen for an hour and earned 50 gold."
        )

    @app_commands.command(name="scratch", description="Free scratch card for broke players (24h cooldown)")
    @app_commands.checks.cooldown(1, 24 * 60 * 60)
    async def cmd_scratch(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        uid = interaction.user.id
        gold_before = get_gold(guild_id, uid)

        if gold_before >= 0:
            await interaction.response.send_message(
                "❌ Only broke people in debt can get a free scratch card!"
            )
            return

        if random.randint(1, 100) == 1:
            set_gold(guild_id, uid, 0)
            await interaction.response.send_message(
                "🎉 Jackpot! You won the Ah Long Debt Relief Grand Prize! All debts are cleared!"
            )
            return

        await interaction.response.send_message(
            "🗑️ Better luck next time. The scratch card says: Go back to washing dishes!"
        )

    @app_commands.command(name="yolo", description="Russian roulette for desperate players (24h cooldown)")
    @app_commands.checks.cooldown(1, 24 * 60 * 60)
    async def cmd_yolo(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        uid = interaction.user.id
        gold_before = get_gold(guild_id, uid)

        if gold_before >= -1000:
            await interaction.response.send_message(
                "❌ Only desperate players with gold below -1000 can use /yolo.",
                ephemeral=True,
            )
            return

        if random.randint(1, 100) <= 10:
            set_gold(guild_id, uid, 500)
            await interaction.response.send_message(
                "🔫 *Click*. The gun is empty! Ah Long respects your guts. He clears your debt and gives you 500 gold to start over!"
            )
            return

        new_gold = int(gold_before * 1.5)
        set_gold(guild_id, uid, new_gold)
        await interaction.response.send_message(
            "💥 *Bang*! You lost! Ah Long beats you up and adds the medical bills to your tab. Your debt increases by 50%!"
        )

    @app_commands.command(name="rob", description="Rob another player for Gold (3 attempts per day)")
    @app_commands.describe(target="The player to rob")
    async def cmd_rob(self, interaction: discord.Interaction, target: discord.Member) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        guild_id = interaction.guild.id
        robber_id = interaction.user.id
        target_id = target.id
        today = get_today_myt()

        if robber_id == target_id:
            await interaction.response.send_message("❌ You cannot rob yourself!", ephemeral=True)
            return

        rob_count = reset_rob_count_if_new_day(guild_id, robber_id, today)

        if rob_count >= 3:
            await interaction.response.send_message(
                "🛑 Heat is too high! You've already robbed 3 times today. Lay low until midnight.",
                ephemeral=True,
            )
            return

        target_gold = get_gold(guild_id, target_id)
        if target_gold <= 0:
            await interaction.response.send_message(
                "❌ Target is too broke. Even Ah Long is looking for them. Pick someone else!",
                ephemeral=True,
            )
            return

        update_rob_data(guild_id, robber_id, today, rob_count + 1)

        success = random.random() < 0.40
        robber_gold = get_gold(guild_id, robber_id)

        if success:
            steal_percentage = random.uniform(0.20, 0.30)
            stolen_amount = int(target_gold * steal_percentage)

            add_gold(guild_id, target_id, -stolen_amount)
            add_gold(guild_id, robber_id, stolen_amount)

            new_robber_gold = get_gold(guild_id, robber_id)
            new_target_gold = get_gold(guild_id, target_id)

            await interaction.response.send_message(
                f"💰 **Robbery Successful!**\n"
                f"{interaction.user.mention} stole **{stolen_amount:,} 💰** from {target.mention}!\n\n"
                f"**{interaction.user.display_name}**: {robber_gold:,} → **{new_robber_gold:,} 💰**\n"
                f"**{target.display_name}**: {target_gold:,} → **{new_target_gold:,} 💰**"
            )
        else:
            penalty_percentage = random.uniform(0.10, 0.15)
            penalty_amount = int(abs(robber_gold) * penalty_percentage)

            add_gold(guild_id, robber_id, -penalty_amount)

            new_robber_gold = get_gold(guild_id, robber_id)

            await interaction.response.send_message(
                f"🚨 **Robbery Failed!**\n"
                f"{interaction.user.mention} got caught trying to rob {target.mention}!\n\n"
                f"**Penalty**: -{penalty_amount:,} 💰\n"
                f"**{interaction.user.display_name}**: {robber_gold:,} → **{new_robber_gold:,} 💰**"
            )

    @app_commands.command(name="balance", description="Check your own or another player's Gold balance")
    @app_commands.describe(member="The player to check — leave blank for yourself")
    async def cmd_balance(self, interaction: discord.Interaction, member: discord.Member = None) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        target = member or interaction.user
        gold   = get_gold(interaction.guild.id, target.id)
        color  = discord.Color.green() if gold >= 0 else discord.Color.red()
        embed  = discord.Embed(
            title=f"💰 {target.display_name}'s Balance",
            description=f"**{gold:,} 💰**" + (" *(💸 In Debt!)*" if gold < 0 else ""),
            color=color,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Top 10 richest players in this server")
    async def cmd_leaderboard(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        await interaction.response.defer()

        rows = db.execute(
            "SELECT user_id, gold FROM user_gold WHERE guild_id = ? ORDER BY gold DESC LIMIT 10",
            (interaction.guild.id,),
        ).fetchall()

        if not rows:
            await interaction.followup.send("No Gold records yet in this server.")
            return

        medals = ["🥇", "🥈", "🥉"]
        lines  = []
        for i, (uid, gold) in enumerate(rows):
            member = interaction.guild.get_member(uid)
            if member is None:
                try:
                    member = await interaction.guild.fetch_member(uid)
                except (discord.NotFound, discord.HTTPException):
                    member = None
            name  = member.display_name if member else f"Unknown User ({uid})"
            medal = medals[i] if i < 3 else f"**{i + 1}.**"
            sign  = "+" if gold > 0 else ""
            lines.append(f"{medal} **{name}** — {sign}{gold:,} 💰")

        embed = discord.Embed(
            title=f"🏆 {interaction.guild.name} Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="disclaimer", description="Legal disclaimer — game tokens, no real money")
    async def cmd_disclaimer(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="⚠️ Legal Disclaimer",
            color=discord.Color.dark_grey(),
        )
        embed.add_field(
            name="Not Gambling",
            value=(
                "This bot provides **entertainment-only** games. "
                "This is **not gambling** — no real money is involved."
            ),
            inline=False,
        )
        embed.add_field(
            name="Gold & Tokens",
            value=(
                "**Gold** 💰 and **Tokens** 🪙 are **game tokens only**. "
                "They have **no monetary value** and cannot be exchanged for real currency. "
                "There is **no real-money purchase** system — you cannot buy Gold or Tokens with cash. "
                "There is **no real-money withdrawal** — you cannot cash out or convert tokens to money."
            ),
            inline=False,
        )
        embed.add_field(
            name="Stay Away from Illegal Gambling",
            value=(
                "We encourage everyone to **stay away from illegal gambling**. "
                "If you or someone you know struggles with gambling addiction, please seek help. "
                "For support, visit: [BeGambleAware.org](https://www.begambleaware.org/) | "
                "[Gamblers Anonymous](https://www.gamblersanonymous.org/)"
            ),
            inline=False,
        )
        embed.add_field(
            name="No Liability",
            value=(
                "This bot is provided \"as is\" for entertainment purposes. "
                "The developers assume no liability for any misuse or misunderstanding. "
                "By using this bot, you agree that you understand these terms."
            ),
            inline=False,
        )
        embed.set_footer(text="For entertainment only — no real money involved")
        await interaction.response.send_message(embed=embed)

    instruction = app_commands.Group(name="instruction", description="中英文指令说明 · Bilingual command guide")

    @instruction.command(name="economy", description="💰 经济系统指令说明 · Economy commands guide")
    async def instruction_economy(self, interaction: discord.Interaction) -> None:
        eco = discord.Embed(
            title="💰 经济系统 · Economy",
            description="**货币 Currency:** 💰 Gold（按服务器独立 · Guild-specific）",
            color=discord.Color.gold(),
        )
        eco.add_field(
            name="指令 Commands",
            value=(
                "`/daily` — 每天领取 **500 Gold**（马来西亚时间午夜重置）\n"
                "Claim **500 Gold** once per day (resets at midnight MYT)\n\n"
                "`/work` — 打工赚 **50 Gold**（10分钟冷却）。1% 概率找到劳力士，清除所有负债\n"
                "Earn **50 Gold** (10-min cooldown). 1% chance to find a Rolex and wipe your debt\n\n"
                "`/scratch` — 负债玩家专属免费刮刮乐（24小时冷却）。1% 中奖直接清债\n"
                "Free scratch card for players **in debt** (24h cooldown). 1% jackpot clears all debt\n\n"
                "`/yolo` — 负债超过 -1000 才能用（24小时冷却）\n"
                "10% 成功：清债 +500 Gold｜90% 失败：负债 ×1.5\n"
                "Only for players below **-1000 Gold** (24h cooldown). 10% win: cleared +500; 90% lose: debt ×1.5\n\n"
                "`/rob @玩家` — 抢劫其他玩家（每天3次机会）\n"
                "40% 成功：偷取对方 20–30% Gold｜失败：自损 10–15%\n"
                "Rob another player (3/day). 40% success: steal 20–30%; fail: lose 10–15% of yours\n\n"
                "`/balance [@玩家]` — 查看自己或他人的 Gold 余额\n"
                "Check your own or another player's Gold balance\n\n"
                "`/leaderboard` — 本服务器 Gold 排行榜前 10 名\n"
                "Top 10 richest players in this server"
            ),
            inline=False,
        )
        eco.add_field(
            name="📉 负债系统 Debt System",
            value=(
                "Gold 可以变成负数，没有下限 · Gold can go negative — no floor\n\n"
                "• 负债状态参与有赌注游戏时，会弹出 **继续 / 取消** 提示\n"
                "  A **Continue / Cancel** prompt appears when joining staked games while in debt\n\n"
                "• 负债期间赢钱，净利润征收 **30% 利息**（向上取整）\n"
                "  Winnings while in debt are taxed **30%** (rounded up)"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=eco)

    @instruction.command(name="banluck", description="🃏 Ban-Luck 班乐玩法说明")
    async def instruction_banluck(self, interaction: discord.Interaction) -> None:
        bj = discord.Embed(
            title="🃏 Ban-Luck（班乐 / 马来西亚21点）",
            color=discord.Color.green(),
        )
        bj.add_field(
            name="指令 Commands",
            value=(
                "`/bj bet:<金额>` — 有赌注模式，使用 💰 Gold\n"
                "Staked mode using Gold\n\n"
                "`/bj` — 免费模式，使用 🎟️ 代币（每局1枚）\n"
                "Free play using Tokens (1 per game)"
            ),
            inline=False,
        )
        bj.add_field(
            name="玩法 Gameplay",
            value=(
                "开局者为**庄家**，最多4名玩家加入（合计5人）\n"
                "Host is the **Banker**; up to 4 others join (5 total)\n\n"
                "每人发2张暗牌，玩家依序行动，庄家最后\n"
                "2 hidden cards each; players act in order, banker acts last\n\n"
                "**🎯 Hit** 要牌 · **🛑 Stand** 停牌（须≥16点）· **🏃 Escape** 逃跑（初始15或16点专用，退还赌注）\n"
                "**🃏 My Cards** 私下查牌 · **🔄 Refresh** 刷新牌桌\n\n"
                "60秒未行动自动停牌 · Auto-Stand after 60s"
            ),
            inline=False,
        )
        bj.add_field(
            name="特殊牌型与赔率 Special Hands & Multipliers",
            value=(
                "✨ **Ban-Ban（双A）** 初始两张A → **3×**\n"
                "🌊 **Ban-Luck（过海）** A + 10/J/Q/K → **2×**\n"
                "👯 **Double（双对子）** 初始两张同点数 → **2×**\n"
                "🐲 **五龙 Five Dragon** 5张不爆牌 → **5×**\n"
                "🎰 **7-7-7（三条七）** 三张7合计21 → **5×**\n"
                "🏆 **普通胜 Normal Win** → **1×**\n\n"
                "神仙打架：双方特殊牌型时倍率高者胜，相同则退注\n"
                "Clash Rule: higher multiplier wins; equal = refund"
            ),
            inline=False,
        )
        bj.add_field(
            name="A 的动态点数 Dynamic Ace",
            value=(
                "2张：1 / 10 / 11（最佳不爆值）· 3张：1 或 10 · 4–5张：只算 1\n"
                "2 cards: 1/10/11 · 3 cards: 1 or 10 · 4-5 cards: 1 only"
            ),
            inline=False,
        )
        bj.set_footer(text="全员同意可 Rematch 重开，随机换庄 · Unanimous Rematch vote, random new banker")
        await interaction.response.send_message(embed=bj)

    @instruction.command(name="poker", description="♠️ Texas Hold'em 德州扑克玩法说明")
    async def instruction_poker(self, interaction: discord.Interaction) -> None:
        poker = discord.Embed(
            title="♠️ Texas Hold'em 德州扑克",
            color=discord.Color.dark_red(),
        )
        poker.add_field(
            name="指令 Commands",
            value=(
                "`/poker [blind:<金额>]` — 开桌（默认底注50，最低10），仅限 💰 Gold\n"
                "Open a table (default blind 50, min 10), Gold only\n\n"
                "`/pokerstop` — 解散自己开的大厅（仅限大厅阶段，仅限房主）\n"
                "Close your open lobby (host only, lobby phase only)"
            ),
            inline=False,
        )
        poker.add_field(
            name="玩法 Gameplay",
            value=(
                "最多8人入座，至少2人才能开局\n"
                "Up to 8 players; minimum 2 to start\n\n"
                "**Preflop → Flop → Turn → River → Showdown**\n\n"
                "底注：小盲 = blind，大盲 = blind × 2\n"
                "Blinds: Small Blind = blind, Big Blind = blind × 2\n\n"
                "开局时底牌通过**私信（DM）**单独发给每位玩家，其他人看不到\n"
                "Hole cards sent via **DM** to each player privately\n\n"
                "60秒未行动自动弃牌 · Auto-fold after 60s"
            ),
            inline=False,
        )
        poker.add_field(
            name="按钮 Buttons",
            value=(
                "🃏 **Fold** — 弃牌 · Fold your hand\n"
                "✅ **Check** / 📞 **Call** — 过牌 / 跟注（标签自动更新）· Check or call\n"
                "💰 **Raise** — 加注，弹出输入框（最低 = 当前注 + 底注）· Raise via modal\n"
                "⚡ **All In** — 全押所有 Gold · Go all-in with remaining Gold\n"
                "🂠 **My Cards** — 私下查看底牌 + 当前最佳牌型 · View hole cards + best hand privately\n"
                "🔄 **Refresh** — 刷新牌桌到频道底部 · Repost board to bottom of channel"
            ),
            inline=False,
        )
        poker.add_field(
            name="牌型排名 Hand Rankings（高→低）",
            value=(
                "👑 Royal Flush（皇家同花顺）\n"
                "🌊 Straight Flush（同花顺）\n"
                "🎰 Four of a Kind（四条）\n"
                "🏠 Full House（葫芦）\n"
                "🎨 Flush（同花）\n"
                "➡️ Straight（顺子）\n"
                "🎲 Three of a Kind（三条）\n"
                "👯 Two Pair（两对）\n"
                "One Pair（一对）\n"
                "High Card（高牌）"
            ),
            inline=False,
        )
        poker.set_footer(text="奖池平分给并列胜者 · Pot split equally among tied winners | 全员同意可 Rematch 重开")
        await interaction.response.send_message(embed=poker)

    @instruction.command(name="games", description="🎲 其他游戏说明 · Other games guide")
    async def instruction_games(self, interaction: discord.Interaction) -> None:
        other = discord.Embed(
            title="🎲 其他游戏 · Other Games",
            color=discord.Color.blurple(),
        )
        other.add_field(
            name="🪙 猜硬币 Heads or Tails — `/ht`",
            value=(
                "`/ht choice:<h|t> bet:<金额>` — 有赌注，💰 Gold\n"
                "`/ht choice:<h|t>` — 免费，🎟️ 代币\n\n"
                "猜正面（h）或反面（t），猜对赢回 **1×** 赌注\n"
                "Guess heads (h) or tails (t) — win **1×** your bet"
            ),
            inline=False,
        )
        other.add_field(
            name="🎰 老虎机 Slot Machine — `/slots`",
            value=(
                "`/slots bet:<金额>` — 仅限 💰 Gold，无免费模式\n"
                "Gold only, no free play\n\n"
                "3×3 表情符号转盘，只看**中间那行**决定胜负\n"
                "3×3 emoji grid — only the **middle row** counts\n\n"
                "🎰🎰🎰 → **50×** JACKPOT\n"
                "💎💎💎 → **20×** MEGA WIN\n"
                "其他三同 Any other triple → **10×** BIG WIN\n"
                "两同 Any pair → **1×** 保本 Push\n"
                "全不同 All different → **0** 输 Lose"
            ),
            inline=False,
        )
        other.add_field(
            name="🎟️ 代币系统 Token System",
            value=(
                "`/tokens [@玩家]` — 查看代币余额 · Check token balance\n"
                "`/resettoken` — 重置全员代币为0 · Reset all balances to 0\n\n"
                "代币用于 `/bj`（无赌注）和 `/ht`（无赌注），可以变负数，无下限\n"
                "Tokens used by free-play `/bj` and `/ht`; can go negative, no floor"
            ),
            inline=False,
        )
        other.add_field(
            name="📋 其他 Others",
            value=(
                "`/balance [@玩家]` — 查看 Gold 余额 · Check Gold balance\n"
                "`/leaderboard` — 服务器排行榜 · Server leaderboard\n"
                "`/disclaimer` — 法律免责声明 · Legal disclaimer"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=other)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(EconomyCog(bot))
