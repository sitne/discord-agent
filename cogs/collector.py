"""Message collector cog - archives server messages for search."""
import logging
import asyncio
from datetime import datetime, timezone, timedelta

import discord
from discord.ext import commands, tasks

log = logging.getLogger("collector")

# How many messages to fetch per channel per backfill batch
BACKFILL_BATCH = 200
# How often to run incremental collection (seconds)
COLLECT_INTERVAL_MINUTES = 5
# Max age of messages to backfill on first run
MAX_BACKFILL_DAYS = 90


class CollectorCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._backfill_done = set()  # channel IDs that completed initial backfill

    async def cog_load(self):
        self.collect_loop.start()
        log.info("Message collector started")

    async def cog_unload(self):
        self.collect_loop.cancel()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Archive every new message in real-time."""
        if not message.guild:
            return
        if not message.content:
            return
        try:
            await self.bot.db.archive_message(
                message_id=str(message.id),
                guild_id=str(message.guild.id),
                channel_id=str(message.channel.id),
                channel_name=message.channel.name if hasattr(message.channel, "name") else "unknown",
                author_id=str(message.author.id),
                author_name=message.author.display_name,
                content=message.content,
                created_at=message.created_at.timestamp(),
            )
            await self.bot.db.conn.commit()
        except Exception as e:
            log.debug(f"Failed to archive message {message.id}: {e}")

    @tasks.loop(minutes=COLLECT_INTERVAL_MINUTES)
    async def collect_loop(self):
        """Periodically backfill and collect new messages."""
        for guild in self.bot.guilds:
            for channel in guild.text_channels:
                try:
                    perms = channel.permissions_for(guild.me)
                    if not perms.read_message_history:
                        continue
                    await self._collect_channel(channel)
                except Exception as e:
                    log.debug(f"Error collecting #{channel.name}: {e}")
                # Be polite to rate limits
                await asyncio.sleep(1)

    @collect_loop.before_loop
    async def before_collect(self):
        await self.bot.wait_until_ready()
        log.info("Starting initial message backfill...")

    async def _collect_channel(self, channel: discord.TextChannel):
        """Collect messages from a single channel."""
        db = self.bot.db
        guild_id = str(channel.guild.id)
        channel_id = str(channel.id)

        # Get last collected message ID
        last_id = await db.get_collection_state(channel_id)

        if last_id:
            # Incremental: fetch messages after last collected
            after = discord.Object(id=int(last_id))
            messages = []
            async for msg in channel.history(limit=BACKFILL_BATCH, after=after, oldest_first=True):
                if msg.content:
                    messages.append(msg)
        else:
            # Initial backfill: fetch recent messages
            cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_BACKFILL_DAYS)
            messages = []
            async for msg in channel.history(limit=BACKFILL_BATCH, after=cutoff, oldest_first=True):
                if msg.content:
                    messages.append(msg)

        if not messages:
            return

        rows = [
            (
                str(msg.id),
                guild_id,
                channel_id,
                channel.name,
                str(msg.author.id),
                msg.author.display_name,
                msg.content,
                msg.created_at.timestamp(),
            )
            for msg in messages
        ]

        await db.archive_messages_bulk(rows)

        # Update state with newest message ID
        newest_id = str(messages[-1].id)
        await db.set_collection_state(channel_id, newest_id)

        if len(messages) > 0:
            log.info(f"Archived {len(messages)} messages from #{channel.name}")


async def setup(bot: commands.Bot):
    await bot.add_cog(CollectorCog(bot))
