"""Discord AI Agent Bot - Main entry point."""
import os
import logging
import asyncio

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db import Database
from mcp_manager import MCPManager

# Import tool modules to register tools
import tools         # noqa: F401 - Discord management tools
import tools_web     # noqa: F401 - Web search, reading, screenshots
import tools_system  # noqa: F401 - Shell, GitHub CLI
import tools_codegen # noqa: F401 - Code generation, GitHub projects

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")


def create_bot() -> commands.Bot:
    intents = discord.Intents.default()
    intents.message_content = True
    intents.members = True

    bot = commands.Bot(
        command_prefix="!",
        intents=intents,
        help_command=None,
    )

    @bot.event
    async def on_ready():
        log.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
        log.info(f"Guilds: {[g.name for g in bot.guilds]}")
        try:
            synced = await bot.tree.sync()
            log.info(f"Synced {len(synced)} slash commands")
        except Exception as e:
            log.error(f"Failed to sync commands: {e}")

    return bot


async def main():
    bot = create_bot()
    bot.db = await Database.create()

    # Initialize MCP
    bot.mcp = MCPManager()
    await bot.mcp.start()

    # Load cogs
    for ext in ["cogs.collector", "cogs.agent", "cogs.scheduler"]:
        await bot.load_extension(ext)
        log.info(f"Loaded {ext}")

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        log.error("DISCORD_TOKEN not set in .env")
        return

    try:
        async with bot:
            await bot.start(token)
    finally:
        await bot.mcp.close()


if __name__ == "__main__":
    asyncio.run(main())
