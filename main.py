"""Discord Bot — entry point."""

import asyncio
import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from database import db  # ensure DB is initialized on import

load_dotenv()
TOKEN: str = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    """Global error handler for slash commands."""
    if isinstance(error, app_commands.CommandOnCooldown):
        retry_after = int(error.retry_after)
        minutes, seconds = divmod(retry_after, 60)
        time_str = f"{minutes}m {seconds}s" if minutes > 0 else f"{seconds}s"
        msg = f"⏳ [v3] This command is on cooldown. Try again in **{time_str}**."
    else:
        msg = f"❌ An error occurred while running this command.\n`{error.__class__.__name__}: {error}`"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


async def _start_health_server() -> None:
    from aiohttp import web as _web
    port = int(os.environ.get("PORT", 8080))
    async def _ok(request):
        return _web.Response(text="OK")
    app = _web.Application()
    app.router.add_get("/", _ok)
    app.router.add_get("/health", _ok)
    runner = _web.AppRunner(app)
    await runner.setup()
    await _web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"[HEALTH] Listening on port {port}")


@bot.event
async def on_ready() -> None:
    asyncio.create_task(_start_health_server())
    for guild in bot.guilds:
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    print(f"[BOT] Logged in as {bot.user} (ID: {bot.user.id})")
    print("[BOT] Ready. Slash commands synced.")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author.bot:
        return
    await bot.process_commands(message)


async def main() -> None:
    async with bot:
        await bot.load_extension("cogs.economy")
        await bot.load_extension("cogs.banluck")
        await bot.load_extension("cogs.slots")
        await bot.load_extension("cogs.poker")
        await bot.load_extension("cogs.admin")
        if not TOKEN or TOKEN == "your_token_here":
            raise RuntimeError("Bot token not set. Open .env and paste your token after DISCORD_TOKEN=")
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
