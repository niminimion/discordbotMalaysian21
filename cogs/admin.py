import discord
from discord.ext import commands
from discord import app_commands
from database import get_tokens, reset_all_tokens


class AdminCog(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="tokens", description="Check your free-play token balance")
    @app_commands.describe(member="The player to check — leave blank for yourself")
    async def cmd_tokens(self, interaction: discord.Interaction, member: discord.Member = None) -> None:
        target = member or interaction.user
        bal    = get_tokens(target.id)
        sign   = "+" if bal > 0 else ""
        color  = discord.Color.green() if bal >= 0 else discord.Color.red()
        embed  = discord.Embed(
            title=f"🪙 {target.display_name}'s Token Balance",
            description=f"**{sign}{bal:,} tokens**",
            color=color,
        )
        embed.set_footer(text="Earned via Free Play (/bj) | reset with /resettoken")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="resettoken", description="Reset all token balances to zero")
    async def cmd_reset_token(self, interaction: discord.Interaction) -> None:
        reset_all_tokens()
        await interaction.response.send_message("✅ All token balances have been reset to **0**.")

    @app_commands.command(name="joinvc", description="[Admin] Join the voice channel you're currently in")
    @app_commands.checks.has_permissions(administrator=True)
    async def cmd_joinvc(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        member = interaction.guild.get_member(interaction.user.id)
        user_vc = member.voice.channel if (member and member.voice) else None
        channel_vc = interaction.channel if isinstance(interaction.channel, discord.VoiceChannel) else None

        target_vc = user_vc or channel_vc
        if target_vc is None:
            await interaction.response.send_message(
                "❌ Join a voice channel first, or run this command in a voice channel's chat.", ephemeral=True
            )
            return
        current_vc = interaction.guild.voice_client

        if current_vc is not None:
            if current_vc.channel == target_vc:
                await interaction.response.send_message(
                    f"Already in **{target_vc.name}**.", ephemeral=True
                )
                return
            await current_vc.move_to(target_vc)
            await interaction.guild.change_voice_state(channel=target_vc, self_deaf=True, self_mute=True)
        else:
            await target_vc.connect(self_deaf=True, self_mute=True)

        await interaction.response.send_message(f"✅ Joined **{target_vc.name}**.")

    @app_commands.command(name="leavevc", description="[Admin] Leave the current voice channel")
    @app_commands.checks.has_permissions(administrator=True)
    async def cmd_leavevc(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        vc = interaction.guild.voice_client
        if vc is None:
            await interaction.response.send_message("❌ Not in any voice channel.", ephemeral=True)
            return

        channel_name = vc.channel.name
        await vc.disconnect()
        await interaction.response.send_message(f"👋 Left **{channel_name}**.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
